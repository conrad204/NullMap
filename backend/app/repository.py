"""Elasticsearch storage, source/extraction preservation, and full-match aggregations."""

import asyncio
import calendar
import math
import re
import unicodedata
from datetime import UTC, date, datetime
from typing import Any

from elasticsearch import AsyncElasticsearch, AuthorizationException, BadRequestError, NotFoundError
from elasticsearch.helpers import async_bulk

from app.config import Settings, settings
from app.statistics import _analysis_type, _completion_date, _margin, _unreported, assign_bucket

BUCKETS = ("effect", "credible_null", "inconclusive", "failed", "unreported")
_CACHE_FIELDS = set(
    (
        "extracted_at extraction_version extraction_status extraction_evidence evidence_span "
        "population intervention comparator outcome outcome_unit n n_intervention n_comparator "
        "estimate ci_low ci_high p_value effect_type ci_level ci_sides p_value_operator has_control "
        "design primary_outcome_met effect_direction outcome_direction"
    ).split()
)
_LINK_FIELDS = set(
    (
        "linked_papers canonical_id canonical_ids registry_title registry_abstract registry_url "
        "paper_result_label possible_abstract_spin"
    ).split()
)
_PAPER_FIELDS = set(
    (
        "title abstract authors venue embedding embedding_model result_label null_score evidence_span "
        "classification_method"
    ).split()
)


def index_mapping(dimensions: int = 384) -> dict:
    properties: dict[str, Any] = {}
    for name in (
        "record_kind source result_label bucket evidence_tier effect_type overall_status "
        "pmids nct_ids referenced_works result_pmids canonical_id canonical_ids outcome_unit "
        "p_value_operator ci_sides extraction_version extraction_status embedding_model "
        "analysis_effect_type effect_direction outcome_direction"
    ).split():
        properties[name] = {"type": "keyword"}
    for name in "title abstract population intervention comparator outcome".split():
        properties[name] = {"type": "text"}
    # Separate retrieval text allows normalization without changing source quotes.
    properties["search_text"] = {"type": "text", "analyzer": "english"}
    for name in "evidence_span why_stopped rationale".split():
        properties[name] = {"type": "text", "index": False}
    for name in (
        "estimate ci_low ci_high p_value null_score ci_level n enrollment_actual enrollment_planned "
        "analysis_estimate analysis_se analysis_ci_low analysis_ci_high mde"
    ).split():
        properties[name] = {"type": "double"}
    for name in "year cited_by_count".split():
        properties[name] = {"type": "integer"}
    for name in (
        "is_retracted is_review has_results has_linked_publication has_control spin_flag "
        "possible_abstract_spin reporting_missing significant_but_trivial"
    ).split():
        properties[name] = {"type": "boolean"}
    for name in (
        "extracted_at primary_completion_date publication_date received_at reporting_due_date"
    ).split():
        properties[name] = {"type": "date"}
    properties["embedding"] = {
        "type": "dense_vector",
        "dims": dimensions,
        "index": True,
        "similarity": "cosine",
        "index_options": {"type": "int8_hnsw"},
    }
    for name in (
        "registry_analyses",
        "extraction_evidence",
        "attachments",
        "metadata",
        "linked_papers",
    ):
        properties[name] = {"type": "object", "enabled": False}
    return {"dynamic": False, "properties": properties}


_QUERY_STOPWORDS = set(
    """
a an the and or of in on at to for from with without by versus vs as is are was were be been
being do does did can could should would will may might whether that this these those their
its our your among into over under than compared comparing comparison effect effects efficacy
impact influence intervention interventions treatment treatments therapy therapies supplementation
supplement supplements supplemental administration administered oral daily receiving receive
change changes changing increase increased increasing decrease decreased decreasing reduce reduced
reducing reduction improvement improved improving improve decline severity symptoms symptom
outcome outcomes measure measures measured score scores level levels rate rates global function
functional study studies trial trials controlled randomized randomised placebo control group groups
""".split()
)
_QUERY_STEMS = ("depress", "cognit", "arthroscop", "osteoarthrit")


def _search_normalize(value: str) -> str:
    folded = "".join(
        char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char)
    ).casefold()
    # NFKD turns subscript ₃ into 3; also align VitaminD3 and vitamin D(3).
    return re.sub(
        r"\bvitamin[\s-]*d(?:[\s-]*\(?([23])\)?)?(?![a-z0-9])",
        lambda match: "vitamin d" + (match[1] or ""),
        folded,
    )


def _concept_terms(value: str) -> list[str]:
    terms = []
    for word in re.findall(r"[a-z0-9]+", _search_normalize(value)):
        if word in _QUERY_STOPWORDS:
            continue
        token = next((stem for stem in _QUERY_STEMS if word.startswith(stem)), word)
        if token not in terms:
            terms.append(token)
    return terms


def _concept_query(terms: list[str], fields: list[str], *, require_all: bool) -> dict:
    clauses = []
    for term in terms:
        if term in _QUERY_STEMS:
            # The stored standard analyzer does not stem depression/depressive.
            # A small, explicit morphology set avoids changing an existing index.
            clauses.append(
                {
                    "bool": {
                        "should": [
                            {"prefix": {field.split("^")[0]: {"value": term}}} for field in fields
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        else:
            clauses.append({"multi_match": {"query": term, "fields": fields}})
    minimum = len(clauses) if require_all else max(1, math.ceil(len(clauses) * 0.6))
    return {"bool": {"should": clauses, "minimum_should_match": minimum}}


_POPULATION_GENERIC = set(
    """
adult adults patient patients participant participants people person persons individual individuals
population populations subject subjects volunteer volunteers men women male males female females
child children pediatric paediatric adolescent adolescents young younger old older elderly aged age
ages aging ageing year years month months day days healthy general community dwelling communitydwelling
post menopausal postmenopausal premenopausal middle middleaged senior seniors who whom have has had
having diagnosed diagnosis confirmed suspected established symptomatic asymptomatic mild moderate severe
major minor history risk risks high higher low lower at living suffering experiencing presenting
scheduled elective undergoing eligible enrolled treated untreated baseline underlying previously
currently type mellitus chronic acute recent newly stage stages all any approximately least more less
""".split()
)
_ANATOMICAL_TERMS = set(
    """
knee knees hip hips elbow elbows shoulder shoulders ankle ankles foot feet wrist wrists hand hands
spine spinal lumbar cervical thoracic lung lungs breast prostate kidney kidneys renal heart cardiac
brain colon colorectal rectal liver hepatic skin eye eyes ear ears dental tooth teeth jaw
""".split()
)


def _population_terms(pico: dict) -> list[str]:
    return [
        term
        for term in _concept_terms(pico.get("population") or "")
        if term not in _POPULATION_GENERIC and not term.isdecimal()
    ]


def population_query(pico: dict) -> dict | None:
    """Constrain explicit conditions/sites; generic demographic descriptions only rank.

    This guard also applies to reference expansion. It deliberately does not try
    to infer diagnostic synonyms: absent textual support is a retrieval limitation.
    Multiple anatomical sites are alternatives under the same condition.
    """
    terms = _population_terms(pico)
    if not terms:
        return None
    fields = ["population^3", "title^3", "abstract", "search_text"]
    anatomy = [term for term in terms if term in _ANATOMICAL_TERMS]
    condition = [term for term in terms if term not in _ANATOMICAL_TERMS]
    clauses = []
    if anatomy:
        clauses.append(
            {
                "bool": {
                    "should": [
                        _concept_query([term], fields, require_all=True) for term in anatomy
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    if condition:
        clauses.append(_concept_query(condition, fields, require_all=True))
    return {"bool": {"must": clauses}}


def lexical_query(pico: dict, idea: str, expanded_ids: list[str] | None = None) -> dict:
    """Require intervention/outcome plus explicit disease or anatomical scope.

    Untyped model synonyms are boosts, not alternative mandatory concepts: a mood
    synonym must not let a depression-only paper satisfy the vitamin D clause.
    Verified review references remain an explicit extension of this match universe.
    """
    intervention = _concept_terms(pico.get("intervention") or "")
    outcome = _concept_terms(pico.get("outcome") or "")
    intervention_fields = ["intervention^5", "title^3", "abstract", "search_text"]
    outcome_fields = ["outcome^5", "title^3", "abstract", "search_text"]
    must = []
    if intervention and outcome:
        if "arthroscop" in intervention:
            # "Arthroscopic" already supplies the surgical anchor. Preserve
            # an explicit choice of lavage OR debridement instead of requiring both.
            intervention = [term for term in intervention if term not in {"surgery", "surgical"}]
        primary_intervention = _concept_query(intervention, intervention_fields, require_all=True)
        if (
            "arthroscop" in intervention
            and {"lavage", "debridement"}.issubset(intervention)
            and re.search(r"\bor\b|/", _search_normalize(pico.get("intervention") or ""))
        ):
            anchor = [term for term in intervention if term not in {"lavage", "debridement"}]
            primary_intervention = {
                "bool": {
                    "must": [
                        _concept_query(anchor, intervention_fields, require_all=True),
                        {
                            "bool": {
                                "should": [
                                    _concept_query(
                                        [procedure], intervention_fields, require_all=True
                                    )
                                    for procedure in ("lavage", "debridement")
                                ],
                                "minimum_should_match": 1,
                            }
                        },
                    ]
                }
            }
        intervention_options = [primary_intervention]
        # Established intervention aliases for the three seeded clinical topics.
        aliases = list(pico.get("interventionAliases", []))[:6]
        if "vitamin" in intervention and any(term in intervention for term in ("d", "d3", "d2")):
            aliases += [
                "cholecalciferol",
                "ergocalciferol",
                "calciferol",
                "vitamin d3",
                "vitamin d2",
            ]
        elif "omega" in intervention and "3" in intervention:
            aliases += [
                "fish oil",
                "n 3 fatty acids",
                "eicosapentaenoic acid",
                "docosahexaenoic acid",
            ]
        for alias in aliases:
            terms = _concept_terms(alias)
            if terms:
                intervention_options.append(
                    _concept_query(terms, intervention_fields, require_all=True)
                )
        outcome_options = [_concept_query(outcome, outcome_fields, require_all=False)]
        outcome_aliases = list(pico.get("outcomeAliases", []))[:6]
        population_terms = _population_terms(pico)
        if "pain" in outcome and "knee" in population_terms and "osteoarthrit" in population_terms:
            # WOMAC is a knee/hip OA pain-stiffness-function composite. Retrieve
            # it in this narrow context; retain the actual endpoint for statistics.
            # Instrument validation: https://pubmed.ncbi.nlm.nih.gov/3068365/
            outcome_aliases.append("WOMAC")
        for alias in outcome_aliases:
            terms = _concept_terms(alias)
            if terms:
                outcome_options.append(_concept_query(terms, outcome_fields, require_all=True))
        must = [
            {"bool": {"should": intervention_options, "minimum_should_match": 1}},
            {"bool": {"should": outcome_options, "minimum_should_match": 1}},
        ]
    else:
        # With no reliable PICO pair, retain a transparent original-query fallback.
        must = [
            {
                "multi_match": {
                    "query": idea,
                    "fields": [
                        "title^3",
                        "abstract",
                        "intervention^3",
                        "outcome^2",
                        "population",
                        "search_text",
                    ],
                    "minimum_should_match": "60%",
                }
            }
        ]
    boosts = []
    if pico.get("population"):
        boosts.append(
            {
                "multi_match": {
                    "query": pico["population"],
                    "fields": ["population^2", "title", "abstract"],
                    "boost": 0.3,
                }
            }
        )
    synonyms = [str(term) for term in pico.get("synonyms", []) if term]
    if synonyms:
        boosts.append(
            {
                "multi_match": {
                    "query": " ".join(synonyms),
                    "fields": ["title", "abstract", "intervention", "outcome"],
                    "boost": 0.3,
                }
            }
        )
    candidate = {"bool": {"must": must, "should": boosts, "minimum_should_match": 0}}
    should = [candidate]
    if expanded_ids:
        should.append({"ids": {"values": list(dict.fromkeys(expanded_ids))}})
    filters = [{"term": {"record_kind": "study"}}]
    population = population_query(pico)
    if population is not None:
        filters.append(population)
    return {
        "bool": {
            "filter": filters,
            "should": should,
            "minimum_should_match": 1,
        }
    }


def rrf_fuse(*rankings: list[dict], limit: int = 200) -> list[dict]:
    scores: dict[str, float] = {}
    docs = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, 1):
            key = doc["id"]
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            docs[key] = doc
    return [docs[key] for key in sorted(scores, key=lambda key: (-scores[key], key))[:limit]]


# All numerical validation, transformations, reconstruction and failure rules run
# in statistics.assign_bucket at write time. This script only reapplies the query's
# margin to those validated 95% bounds, plus the time-sensitive reporting deadline.
# Pass doc explicitly to functions: Painless functions do not capture script locals.
BUCKET_SCRIPT = """
boolean present(def values, String key) {
    return values.containsKey(key) && values[key].size() > 0;
}
String text(def values, String key) {
    return present(values, key) ? values[key].value.toString() : '';
}
boolean flag(def values, String key) {
    return present(values, key) && values[key].value == true;
}
if (text(doc, 'bucket') == 'failed') { emit('failed'); return; }
if (flag(doc, 'is_review')) { emit('inconclusive'); return; }
if (flag(doc, 'reporting_missing') && present(doc, 'reporting_due_date')
    && doc['reporting_due_date'].value.toInstant().toEpochMilli() < params.today) {
    emit('unreported'); return;
}
if (!present(doc, 'analysis_ci_low') || !present(doc, 'analysis_ci_high')) {
    emit(text(doc, 'result_label') == 'positive' ? 'effect' : 'inconclusive'); return;
}
if (params.delta == null || text(doc, 'analysis_effect_type') != params.effect) {
    emit('inconclusive'); return;
}
double low = doc['analysis_ci_low'].value;
double high = doc['analysis_ci_high'].value;
if (low > -params.delta && high < params.delta) { emit('credible_null'); return; }
if ((low > 0 || high < 0) && present(doc, 'analysis_estimate')
    && Math.abs(doc['analysis_estimate'].value) >= params.delta) { emit('effect'); return; }
emit('inconclusive');
"""


def _prepare_document(incoming: dict, existing: dict | None = None) -> dict:
    """Refresh source data while preserving a cache tied to unchanged source text."""
    old = existing or {}
    doc = {**old, **incoming}
    doc["record_kind"] = old.get("record_kind", incoming.get("record_kind", "study"))
    doc.setdefault("is_review", False)
    # A later registry refresh must not undo a proven paper/trial link.
    if old.get("source") == "merged" and incoming.get("source") == "ctgov":
        for key in _LINK_FIELDS | _PAPER_FIELDS:
            if key in old:
                doc[key] = old[key]
        doc.update(source="merged", has_linked_publication=True)
        for key in ("pmids", "result_pmids", "referenced_works"):
            doc[key] = sorted(set(old.get(key) or []) | set(incoming.get(key) or []))
    # Reingestion of a linked paper must not resurrect a second patient sample.
    if old.get("record_kind") == "linked_publication":
        for key in ("canonical_id", "canonical_ids"):
            if key in old:
                doc[key] = old[key]
    unchanged = old.get("abstract", "") == doc.get("abstract", "")
    cached = bool(old.get("extracted_at"))
    source_has_new_registry_numbers = incoming.get("source") in {"ctgov", "merged"} and bool(
        incoming.get("numeric_source")
    )
    if unchanged and cached and not source_has_new_registry_numbers:
        for key in _CACHE_FIELDS:
            if key in old:
                doc[key] = old[key]
    elif cached and (not unchanged or source_has_new_registry_numbers):
        # Partial updates need explicit nulls, otherwise removed cache keys survive.
        for key in _CACHE_FIELDS:
            doc[key] = incoming.get(key)
        # Registry-linked rows keep searchable literature text and its provenance.
        if old.get("source") == "merged" and incoming.get("source") == "ctgov":
            doc["evidence_span"] = incoming.get("evidence_span") or old.get("evidence_span", "")
    if not unchanged and "embedding" not in incoming:
        doc["embedding"] = None
    elif unchanged and incoming.get("embedding") is None:
        # get/mget may intentionally omit vector source; partial update preserves it.
        doc.pop("embedding", None)
    if not doc.get("publication_date"):
        year = doc.get("year")
        doc["publication_date"] = (
            f"{int(year):04d}-01-01" if year and 1 <= int(year) <= 9999 else None
        )
    doc["search_text"] = _search_normalize(
        " ".join(
            str(doc.get(field) or "")
            for field in (
                "title",
                "abstract",
                "population",
                "intervention",
                "comparator",
                "outcome",
            )
        )
    )
    # Normalization is the single source of truth for query-time numerical rules.
    doc.update(assign_bucket(doc))
    completion = _completion_date(doc.get("primary_completion_date"))
    doc["primary_completion_date"] = completion.isoformat() if completion else None
    # Substitute a known past date only to ask the same strict metadata predicate
    # whether results are confirmed absent. Recency is evaluated separately.
    doc["reporting_missing"] = _unreported(
        {**doc, "primary_completion_date": "1900-01-01"}, date(2000, 1, 1)
    )
    doc["reporting_due_date"] = None
    if completion is not None and completion.year < 9999:
        anniversary = completion.replace(
            year=completion.year + 1,
            day=min(completion.day, calendar.monthrange(completion.year + 1, completion.month)[1]),
        )
        doc["reporting_due_date"] = anniversary.isoformat()
    return doc


class ElasticRepository:
    def __init__(self, config: Settings = settings, client: AsyncElasticsearch | None = None):
        self.config = config
        self.index = config.elastic_index
        self.client = client or AsyncElasticsearch(
            config.elastic_url,
            api_key=config.elastic_api_key
            if config.elastic_api_key and not config.elastic_local
            else None,
            request_timeout=30,
            max_retries=2,
            retry_on_timeout=True,
        )
        self._index_ready = False
        self._vector_source_filter = True
        self._index_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.client.close()

    async def ensure_index(self) -> None:
        if self._index_ready:
            return
        async with self._index_lock:
            if self._index_ready:
                return
            mapping = index_mapping(self.config.embedding_dimensions)
            if not await self.client.indices.exists(index=self.index):
                try:
                    await self.client.indices.create(index=self.index, mappings=mapping)
                except BadRequestError as exc:
                    if "resource_already_exists_exception" not in str(exc):
                        raise
                    await self.client.indices.put_mapping(index=self.index, **mapping)
            else:
                # Add newly introduced explicit fields on existing deployments.
                await self.client.indices.put_mapping(index=self.index, **mapping)
            self._index_ready = True

    async def health(self) -> dict:
        await self.client.info()
        exists = await self.client.indices.exists(index=self.index)
        count = (
            await self.client.count(index=self.index, query={"term": {"record_kind": "study"}})
            if exists
            else {"count": 0}
        )
        return {"connected": True, "index": self.index, "studies": count["count"]}

    async def bulk_upsert(self, studies: list[dict]) -> int:
        if not studies:
            return 0
        await self.ensure_index()
        existing = {
            doc["id"]: doc
            for doc in await self.get_many(
                [study["id"] for study in studies], include_vectors=False
            )
        }
        actions = []
        for item in studies:
            doc = _prepare_document(item, existing.get(item["id"]))
            actions.append(
                {
                    "_op_type": "update",
                    "_index": self.index,
                    "_id": doc["id"],
                    "doc": doc,
                    "doc_as_upsert": True,
                }
            )
        count, _ = await async_bulk(self.client, actions, refresh="wait_for")
        return count

    async def get(self, study_id: str) -> dict | None:
        try:
            result = await self.client.get(index=self.index, id=study_id)
            return dict(result["_source"], id=result["_id"])
        except NotFoundError:
            return None

    async def get_many(self, ids: list[str], *, include_vectors: bool = True) -> list[dict]:
        if not ids:
            return []
        identifiers = list(dict.fromkeys(ids))
        if not include_vectors:
            result = await self.client.mget(
                index=self.index, ids=identifiers, source_excludes=["embedding"]
            )
        elif not self._vector_source_filter:
            result = await self.client.mget(index=self.index, ids=identifiers)
        else:
            # 9.2+ defaults to excluding vectors; 9.1 keeps them but does not
            # accept this newer source-filter option inside mget documents.
            try:
                result = await self.client.mget(
                    index=self.index,
                    docs=[
                        {"_id": identifier, "_source": {"exclude_vectors": False}}
                        for identifier in identifiers
                    ],
                )
            except BadRequestError as exc:
                message = str(exc).lower()
                if not any(
                    fragment in message
                    for fragment in ("exclude_vectors", "expected text", "unknown field")
                ):
                    raise
                self._vector_source_filter = False
                result = await self.client.mget(index=self.index, ids=identifiers)
        return [
            dict(item["_source"], id=item["_id"]) for item in result["docs"] if item.get("found")
        ]

    @staticmethod
    def hits(response: Any) -> list[dict]:
        return [dict(h["_source"], id=h["_id"]) for h in response["hits"]["hits"]]

    async def retrieve(self, query: dict, vector: list[float] | None) -> tuple[list[dict], str]:
        base = {"index": self.index, "size": 200, "source_excludes": ["embedding", "attachments"]}
        if vector is None:
            result = await self.client.search(**base, query=query)
            return self.hits(result), "bm25"
        knn = {
            "field": "embedding",
            "query_vector": vector,
            "k": 200,
            "num_candidates": 1000,
            "filter": query,
        }
        try:
            result = await self.client.search(
                **base,
                retriever={
                    "rrf": {
                        "rank_window_size": 200,
                        "rank_constant": 60,
                        "retrievers": [{"standard": {"query": query}}, {"knn": knn}],
                    }
                },
            )
            return self.hits(result), "hybrid_rrf"
        except (BadRequestError, AuthorizationException) as exc:
            # Only a license restriction permits fallback; auth/DSL errors must surface.
            message = str(exc).lower()
            if "license" not in message or not any(
                term in message for term in ("rrf", "rank fusion", "retriever")
            ):
                raise
            lexical, semantic = await asyncio.gather(
                self.client.search(**base, query=query), self.client.search(**base, knn=knn)
            )
            return rrf_fuse(self.hits(lexical), self.hits(semantic)), "hybrid_client_rrf"

    async def screen_population(self, ids: list[str], pico: dict) -> set[str]:
        """Apply the same population guard to expanded rows and aggregate counts."""
        identifiers = list(dict.fromkeys(ids))
        population = population_query(pico)
        if not identifiers or population is None:
            return set(identifiers)
        result = await self.client.search(
            index=self.index,
            size=len(identifiers),
            source=False,
            query={
                "bool": {
                    "filter": [
                        {"ids": {"values": identifiers}},
                        {"term": {"record_kind": "study"}},
                        population,
                    ]
                }
            },
        )
        return {hit["_id"] for hit in result["hits"]["hits"]}

    async def registry_sweep(self, query: dict) -> list[dict]:
        result = await self.client.search(
            index=self.index,
            size=100,
            query={
                "bool": {"must": [query], "filter": [{"terms": {"source": ["ctgov", "merged"]}}]}
            },
            source_excludes=["embedding", "attachments"],
        )
        return self.hits(result)

    async def cache_extraction(self, study_id: str, extraction: dict) -> None:
        current = await self.get(study_id)
        if current is None:
            raise ValueError("Cannot cache extraction for a missing study")
        updated = _prepare_document({**current, **extraction})
        # Only cache and derived fields change; source vectors remain intact.
        derived = set(assign_bucket(updated)) | {
            "reporting_missing",
            "reporting_due_date",
            "search_text",
        }
        patch = {key: updated.get(key) for key in set(extraction) | derived}
        await self.client.update(index=self.index, id=study_id, doc=patch, refresh="wait_for")

    async def persist_links(self, merged: list[dict]) -> None:
        """Persist each canonical trial, then hide its duplicate publication rows."""
        if not merged:
            return
        paper_ids = list(
            dict.fromkeys(
                paper["id"]
                for trial in merged
                for paper in trial.get("linked_papers", [])
                if paper.get("id")
            )
        )
        papers = {paper["id"]: paper for paper in await self.get_many(paper_ids)}
        canonical_ids = {trial["id"] for trial in merged}
        links: dict[str, set[str]] = {}
        prepared = []
        for trial in merged:
            doc = dict(trial)
            doc["linked_papers"] = list(
                {
                    paper["id"]: paper
                    for paper in trial.get("linked_papers", [])
                    if paper.get("id")
                }.values()
            )
            for paper in doc["linked_papers"]:
                pid = paper["id"]
                if pid in canonical_ids:
                    continue
                links.setdefault(pid, set()).add(doc["id"])
                full_paper = papers.get(pid, {})
                if (
                    full_paper.get("abstract") == doc.get("abstract")
                    and full_paper.get("embedding") is not None
                ):
                    doc["embedding"] = full_paper["embedding"]
            prepared.append(doc)
        await self.bulk_upsert(prepared)
        actions = []
        for paper_id, targets in links.items():
            if paper_id not in papers:
                continue
            existing = papers[paper_id].get("canonical_ids") or []
            if isinstance(existing, str):
                existing = [existing]
            targets.update(existing)
            actions.append(
                {
                    "_op_type": "update",
                    "_index": self.index,
                    "_id": paper_id,
                    "doc": {"record_kind": "linked_publication", "canonical_ids": sorted(targets)},
                }
            )
        if actions:
            await async_bulk(self.client, actions, refresh="wait_for")

    async def aggregate(self, query: dict, sesoi: float, effect_type: str) -> dict:
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        runtime = {
            "query_bucket": {
                "type": "keyword",
                "script": {
                    "source": BUCKET_SCRIPT,
                    "params": {
                        "delta": _margin(sesoi, effect_type),
                        "effect": _analysis_type(effect_type),
                        "today": int(today.timestamp() * 1000),
                    },
                },
            }
        }
        q = {
            "bool": {
                "must": [query],
                "filter": [{"term": {"record_kind": "study"}}],
                "must_not": [{"term": {"is_review": True}}],
            }
        }
        completed = {
            "bool": {
                "filter": [
                    {"term": {"overall_status": "COMPLETED"}},
                    {"terms": {"source": ["ctgov", "merged"]}},
                ]
            }
        }
        result = await self.client.search(
            index=self.index,
            query=q,
            size=0,
            track_total_hits=True,
            runtime_mappings=runtime,
            aggs={
                "buckets": {"terms": {"field": "query_bucket", "size": 5}},
                "years": {
                    "date_histogram": {
                        "field": "publication_date",
                        "calendar_interval": "year",
                        "min_doc_count": 1,
                    }
                },
                "nulls": {
                    "filter": {"term": {"query_bucket": "credible_null"}},
                    "aggs": {
                        "sample": {
                            "sampler": {"shard_size": 300},
                            "aggs": {
                                "terms": {
                                    "significant_text": {
                                        "field": "abstract",
                                        "size": 12,
                                        "filter_duplicate_text": True,
                                        "background_filter": q,
                                    }
                                }
                            },
                        }
                    },
                },
                "completed": {
                    "filter": completed,
                    "aggs": {
                        "missing": {"filter": {"term": {"reporting_missing": True}}},
                        "overdue": {
                            "filter": {
                                "bool": {
                                    "filter": [
                                        {"term": {"reporting_missing": True}},
                                        {
                                            "range": {
                                                "reporting_due_date": {"lt": today.isoformat()}
                                            }
                                        },
                                    ]
                                }
                            }
                        },
                    },
                },
                "spin_candidates": {
                    "filter": {
                        "bool": {
                            "filter": [
                                {"term": {"source": "merged"}},
                                {"term": {"has_results": True}},
                            ]
                        }
                    },
                    "aggs": {"spin": {"filter": {"term": {"possible_abstract_spin": True}}}},
                },
            },
        )
        aggs = result["aggregations"]
        counts = dict.fromkeys(BUCKETS, 0)
        counts.update({b["key"]: b["doc_count"] for b in aggs["buckets"]["buckets"]})
        registered = aggs["completed"]["doc_count"]
        unreported = aggs["completed"]["missing"]["doc_count"]
        return {
            "total": result["hits"]["total"]["value"],
            "bucketCounts": counts,
            "yearCounts": [
                {"year": int(b["key_as_string"][:4]), "count": b["doc_count"]}
                for b in aggs["years"]["buckets"]
            ],
            "nullTerms": [
                {"term": b["key"], "score": b["score"], "count": b["doc_count"]}
                for b in aggs["nulls"]["sample"]["terms"]["buckets"]
            ],
            "fileDrawer": {
                "completed": registered,
                "unreported": unreported,
                "overdue": aggs["completed"]["overdue"]["doc_count"],
                "share": unreported / registered if registered else None,
            },
            "spin": {
                "eligible": aggs["spin_candidates"]["doc_count"],
                "disagreements": aggs["spin_candidates"]["spin"]["doc_count"],
            },
        }

    async def save_contribution(self, contribution: dict) -> None:
        await self.ensure_index()
        await self.client.index(
            index=self.index, id=contribution["id"], document=contribution, refresh="wait_for"
        )
