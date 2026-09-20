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
from app.statistics import (
    DERIVED_SCALES,
    INCONCLUSIVE_REASONS,
    _analysis_type,
    _completion_date,
    _margin,
    _unreported,
    assign_bucket,
    derived_field,
    stored_analysis,
)

BUCKETS = ("effect", "credible_null", "reported_null", "inconclusive", "failed", "unreported")
_CACHE_FIELDS = set(
    (
        "extracted_at extraction_version extraction_status extraction_evidence evidence_span "
        "extraction_source fulltext_status "
        "population intervention comparator outcome outcome_unit n n_intervention n_comparator "
        "mean_intervention mean_comparator sd_intervention sd_comparator "
        "events_intervention events_comparator "
        "estimate ci_low ci_high p_value effect_type ci_level ci_sides p_value_operator has_control "
        "design primary_outcome_met reported_result result_direction effect_direction "
        "outcome_direction"
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
        "classification_method abstract_available work_type snapshot_provenance pmcid"
    ).split()
)


def index_mapping(dimensions: int = 384) -> dict:
    properties: dict[str, Any] = {}
    for name in (
        "record_kind source result_label bucket evidence_tier effect_type overall_status "
        "pmids nct_ids referenced_works result_pmids canonical_id canonical_ids outcome_unit "
        "p_value_operator ci_sides extraction_version extraction_status embedding_model "
        "analysis_effect_type effect_direction outcome_direction work_type "
        "pmcid extraction_source fulltext_status reported_result result_direction"
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
        "analysis_estimate analysis_se analysis_ci_low analysis_ci_high mde "
        "n_intervention n_comparator mean_intervention mean_comparator "
        "sd_intervention sd_comparator events_intervention events_comparator"
    ).split():
        properties[name] = {"type": "double"}
    # Arm-level effects on every derivable scale, so a query on any scale can be
    # bucketed over the full match set without reading documents.
    for scale in DERIVED_SCALES:
        for name in ("estimate", "ci_low", "ci_high"):
            properties[derived_field(scale, name)] = {"type": "double"}
    for name in "year cited_by_count".split():
        properties[name] = {"type": "integer"}
    # Citation-authority signals. rank_feature rejects non-positive values, so
    # writers log-scale and leave the field absent instead of storing 0.
    for name in ("authority", "pagerank"):
        properties[name] = {"type": "rank_feature"}
    for name in (
        "is_retracted is_review has_results has_linked_publication has_control spin_flag "
        "possible_abstract_spin reporting_missing significant_but_trivial abstract_available"
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
        "metadata",
        "linked_papers",
        "snapshot_provenance",
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
expression expressed elevated elevation higher lower associated association status presence
""".split()
)
_QUERY_STEMS = ("depress", "cognit", "arthroscop", "osteoarthrit", "hypertens")
# Two or more letters then digits (kim1, il6, covid19). A single letter is left alone
# so the vitamin d3/d2 handling keeps its own tokens.
_JOINED_DESIGNATOR = re.compile(r"([a-z]{2,})(\d+)")


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
        elif joined := _JOINED_DESIGNATOR.fullmatch(term):
            # The standard analyzer indexes "KIM-1" as kim + 1, so a typed "kim1" would
            # otherwise match only the few documents that spell it without the hyphen.
            spaced = " ".join(joined.groups())
            clauses.append(
                {
                    "bool": {
                        "should": [
                            {"multi_match": {"query": term, "fields": fields}},
                            {"multi_match": {"query": spaced, "fields": fields, "type": "phrase"}},
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


# An alias made only of these words would admit nearly every clinical record.
_ALIAS_TOO_BROAD = set(
    "disease diseases disorder disorders syndrome syndromes condition conditions illness "
    "illnesses complication complications health".split()
)
_MAX_POPULATION_ALIASES = 4


def _specific_terms(value: str) -> list[str]:
    return [
        term
        for term in _concept_terms(value)
        if term not in _POPULATION_GENERIC and not term.isdecimal()
    ]


def _population_terms(pico: dict) -> list[str]:
    return _specific_terms(pico.get("population") or "")


def _population_alias_terms(pico: dict) -> list[list[str]]:
    """Model-proposed equivalent condition names, reduced to specific terms.

    Aliases widen the full match set and therefore every bucket count, so an
    alias that adds nothing specific, or merely repeats the population, is dropped.
    """
    primary = set(_population_terms(pico))
    result: list[list[str]] = []
    for alias in list(pico.get("populationAliases") or [])[:_MAX_POPULATION_ALIASES]:
        terms = _specific_terms(str(alias))
        if not terms or terms in result:
            continue
        if all(term in _ALIAS_TOO_BROAD or term in _ANATOMICAL_TERMS for term in terms):
            continue
        specific = {term for term in terms if term not in _ALIAS_TOO_BROAD}
        if specific <= primary or primary <= specific:
            # A subset is broader than the population; a superset is already matched by it.
            continue
        result.append(terms)
    return result


def population_query(pico: dict) -> dict | None:
    """Constrain explicit conditions/sites; generic demographic descriptions only rank.

    This guard also applies to reference expansion. It deliberately does not try
    to infer diagnostic synonyms itself: absent textual support is a retrieval limitation.
    The parser may supply ``populationAliases`` (equivalent names of the same condition);
    each is a full alternative to the condition clause and is ignored without a population.
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
    primary = {"bool": {"must": clauses}}
    alternatives = []
    for alias in _population_alias_terms(pico):
        must = [_concept_query(alias, fields, require_all=True)]
        # An alias that drops the anatomical site must not escape it: "OA" still needs "knee".
        if anatomy and not any(term in _ANATOMICAL_TERMS for term in alias):
            must.append(clauses[0])
        alternatives.append({"bool": {"must": must}})
    if not alternatives:
        return primary
    return {"bool": {"should": [primary, *alternatives], "minimum_should_match": 1}}


# ClinicalTrials.gov records have no citation count: ingestion writes 0 for every
# registry row. A citation bound applied to them would delete exactly the
# terminated and never-reported trials this tool exists to surface, so registry
# rows are exempt from citation bounds and the pipeline warns that they are.
# Publication dates do exist for registry rows (the primary completion date), so
# a date bound applies to papers and trials alike.
CITATION_EXEMPT_SOURCES = ("ctgov", "merged")


def citation_range(filters: dict | None) -> dict | None:
    """The citation bound on its own, or None when the request set neither end."""
    if not filters:
        return None
    bounds = {}
    if filters.get("minCitations") is not None:
        bounds["gte"] = int(filters["minCitations"])
    if filters.get("maxCitations") is not None:
        bounds["lte"] = int(filters["maxCitations"])
    return {"range": {"cited_by_count": bounds}} if bounds else None


def registry_citation_exemption_query(query: dict, filters: dict | None) -> dict | None:
    """Rows in the match set only because citation bounds skip the registry.

    Counting these makes the exemption visible: it is the number of trial records
    the citation requirement would have removed had it applied to them.
    """
    bound = citation_range(filters)
    if bound is None:
        return None
    return {
        "bool": {
            "must": [query],
            "filter": [{"terms": {"source": list(CITATION_EXEMPT_SOURCES)}}],
            "must_not": [bound],
        }
    }


def filter_clauses(filters: dict | None) -> list[dict]:
    """Pre-search corpus restrictions, as filter clauses over the full match set.

    These belong in the query's ``filter``, never in ``should``: the caller's
    expanded review references and every aggregation share that clause list, so a
    filtered-out record cannot re-enter the study list while the counts exclude it.
    A record with no ``publication_date`` cannot satisfy a date bound and drops out.
    """
    if not filters:
        return []
    clauses: list[dict] = []
    dates = {}
    if filters.get("yearFrom") is not None:
        dates["gte"] = f"{int(filters['yearFrom']):04d}-01-01"
    if filters.get("yearTo") is not None:
        dates["lte"] = f"{int(filters['yearTo']):04d}-12-31"
    if dates:
        clauses.append({"range": {"publication_date": dates}})
    citations = citation_range(filters)
    if citations is not None:
        clauses.append(
            {
                "bool": {
                    "should": [
                        citations,
                        {"terms": {"source": list(CITATION_EXEMPT_SOURCES)}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    return clauses


def lexical_query(
    pico: dict,
    idea: str,
    expanded_ids: list[str] | None = None,
    filters: dict | None = None,
) -> dict:
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
    required = [{"term": {"record_kind": "study"}}]
    population = population_query(pico)
    if population is not None:
        required.append(population)
    required.extend(filter_clauses(filters))
    return {
        "bool": {
            "filter": required,
            "should": should,
            "minimum_should_match": 1,
        }
    }


def authority_query(query: dict) -> dict:
    """Rank the query's own match set by combined citation authority.

    Each ``rank_feature`` leaf contributes a saturation-scaled function of the
    stored log signals (``authority`` = log citation count, ``pagerank`` =
    within-corpus citation graph), and the ``should`` clauses sum them. The
    main query stays a filter, so authority re-ranks relevant hits only —
    an unrelated famous paper must not enter results on reputation alone.
    Documents with neither signal simply absent themselves from this leg.
    """
    return {
        "bool": {
            "filter": [query],
            "should": [
                {"rank_feature": {"field": "authority"}},
                {"rank_feature": {"field": "pagerank"}},
            ],
            "minimum_should_match": 1,
        }
    }


# How many ranked records one search carries forward. It has to cover the relevance screen
# (pipeline.SCREEN_LIMIT): a shorter list would silently cap what can be screened.
RETRIEVE_LIMIT = 500


def rrf_fuse(*rankings: list[dict], limit: int = RETRIEVE_LIMIT) -> list[dict]:
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
# An inconclusive row is emitted as 'inconclusive:<reason>' (statistics.INCONCLUSIVE_REASONS)
# so one pass yields both the bucket and why; aggregate() folds the reasons back together.
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
if (flag(doc, 'is_review')) { emit('inconclusive:review'); return; }
if (flag(doc, 'reporting_missing') && present(doc, 'reporting_due_date')
    && doc['reporting_due_date'].value.toInstant().toEpochMilli() < params.today) {
    emit('unreported'); return;
}
if (!present(doc, 'analysis_ci_low') || !present(doc, 'analysis_ci_high')) {
    String source = text(doc, 'source');
    boolean comparative = flag(doc, 'has_control') || source == 'ctgov' || source == 'merged'
        || present(doc, 'nct_ids');
    boolean read = text(doc, 'extraction_status') == 'verified';
    String label = comparative && present(doc, 'reported_result') ? text(doc, 'reported_result')
        : (read && !comparative ? '' : text(doc, 'result_label'));
    emit(label == 'positive' ? 'effect' : (label == 'null' ? 'reported_null'
        : (label == 'mixed' ? 'inconclusive:mixed_result' : 'inconclusive:no_result')));
    return;
}
if (params.delta == null) { emit('inconclusive:no_threshold'); return; }
// The stored analysis is on the default scale; a query on another scale falls
// back to the arm-level effect derived for that scale, mirroring _numeric_evidence.
String prefix = 'analysis';
if (text(doc, 'analysis_effect_type') != params.effect) {
    if (params.derived == null || !present(doc, params.derived + '_ci_low')
        || !present(doc, params.derived + '_ci_high')) { emit('inconclusive:other_scale'); return; }
    prefix = params.derived;
}
double low = doc[prefix + '_ci_low'].value;
double high = doc[prefix + '_ci_high'].value;
if (low > -params.delta && high < params.delta) { emit('credible_null'); return; }
if ((low > 0 || high < 0) && present(doc, prefix + '_estimate')
    && Math.abs(doc[prefix + '_estimate'].value) >= params.delta) { emit('effect'); return; }
emit('inconclusive:wide_interval');
"""


def bucket_runtime(sesoi: float, effect_type: str, today: datetime | None = None) -> dict:
    """Runtime field applying BUCKET_SCRIPT with one query's margin and effect scale."""
    today = (today or datetime.now(UTC)).replace(hour=0, minute=0, second=0, microsecond=0)
    scale = _analysis_type(effect_type)
    return {
        "query_bucket": {
            "type": "keyword",
            "script": {
                "source": BUCKET_SCRIPT,
                "params": {
                    "delta": _margin(sesoi, effect_type),
                    "effect": scale,
                    "derived": derived_field(scale, "")[:-1] if scale in DERIVED_SCALES else None,
                    "today": int(today.timestamp() * 1000),
                },
            },
        }
    }


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
    doc.update(stored_analysis(doc))
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
    # rank_feature rejects non-positive values, so authority is absent rather
    # than 0. pagerank is written only by the offline citation-graph pass and
    # survives reingest through the {**old, **incoming} merge above.
    cited_by = doc.get("cited_by_count")
    if (
        isinstance(cited_by, (int, float))
        and not isinstance(cited_by, bool)
        and math.isfinite(cited_by)
        and cited_by > 0
    ):
        doc["authority"] = math.log1p(cited_by)
    else:
        doc.pop("authority", None)
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

    async def sample_embedded(self, limit: int = 4000, seed: int = 0) -> dict:
        """A deterministic random sample of embedded studies, plus what it omits.

        Random rather than top-cited: a map of where the literature is thin must
        not be drawn from its most popular corner. The counts travel with the
        sample so a caller can say which fraction of the corpus it describes.
        """
        await self.ensure_index()
        query = {
            "bool": {
                "filter": [{"term": {"record_kind": "study"}}, {"exists": {"field": "embedding"}}],
            }
        }
        total = await self.client.count(index=self.index, query=query)
        body = {
            "index": self.index,
            "size": max(0, limit),
            "query": {
                "function_score": {
                    "query": query,
                    "random_score": {"seed": seed, "field": "_seq_no"},
                    # A filter-only query scores every document 0, and the default
                    # multiply boost would turn the random score into 0 as well,
                    # making "the sample" the first `size` documents in index order
                    # — measured against the live index, that returned 4000 registry
                    # records and not one of the 567k papers.
                    "boost_mode": "replace",
                }
            },
            "source_includes": [
                "embedding",
                "title",
                "year",
                "bucket",
                "record_kind",
                "is_review",
                "cited_by_count",
                "source",
            ],
        }
        if self._vector_source_filter:
            try:
                result = await self.client.search(**body, source_exclude_vectors=False)
            except (BadRequestError, TypeError):
                self._vector_source_filter = False
                result = await self.client.search(**body)
        else:
            result = await self.client.search(**body)
        return {"documents": self.hits(result), "corpus": total["count"]}

    async def retrieve(self, query: dict, vector: list[float] | None) -> tuple[list[dict], str]:
        base = {"index": self.index, "size": RETRIEVE_LIMIT, "source_excludes": ["embedding"]}
        # Third ranking signal: citation authority over the same match set.
        authority = authority_query(query)
        if vector is None:
            lexical, ranked = await asyncio.gather(
                self.client.search(**base, query=query),
                self.client.search(**base, query=authority),
            )
            return rrf_fuse(self.hits(lexical), self.hits(ranked)), "bm25"
        knn = {
            "field": "embedding",
            "query_vector": vector,
            "k": RETRIEVE_LIMIT,
            "num_candidates": 2 * RETRIEVE_LIMIT,
            "filter": query,
        }
        try:
            result = await self.client.search(
                **base,
                retriever={
                    "rrf": {
                        "rank_window_size": RETRIEVE_LIMIT,
                        "rank_constant": 60,
                        "retrievers": [
                            {"standard": {"query": query}},
                            {"knn": knn},
                            {"standard": {"query": authority}},
                        ],
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
            lexical, semantic, ranked = await asyncio.gather(
                self.client.search(**base, query=query),
                self.client.search(**base, knn=knn),
                self.client.search(**base, query=authority),
            )
            return (
                rrf_fuse(self.hits(lexical), self.hits(semantic), self.hits(ranked)),
                "hybrid_client_rrf",
            )

    async def screen_population(
        self, ids: list[str], pico: dict, filters: dict | None = None
    ) -> set[str]:
        """Apply the population guard and the corpus filters to expanded rows.

        Review references enter the study list without passing the lexical query,
        so they are screened here against the same conditions the aggregation uses.
        """
        identifiers = list(dict.fromkeys(ids))
        population = population_query(pico)
        restrictions = filter_clauses(filters)
        if not identifiers or (population is None and not restrictions):
            return set(identifiers)
        clauses = [
            {"ids": {"values": identifiers}},
            {"term": {"record_kind": "study"}},
        ]
        if population is not None:
            clauses.append(population)
        clauses.extend(restrictions)
        result = await self.client.search(
            index=self.index,
            size=len(identifiers),
            source=False,
            query={"bool": {"filter": clauses}},
        )
        return {hit["_id"] for hit in result["hits"]["hits"]}

    async def registry_sweep(self, query: dict) -> list[dict]:
        result = await self.client.search(
            index=self.index,
            size=100,
            query={
                "bool": {"must": [query], "filter": [{"terms": {"source": ["ctgov", "merged"]}}]}
            },
            source_excludes=["embedding"],
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

    @staticmethod
    def match_set(query: dict) -> dict:
        """The counted population: primary study records matching the query."""
        return {
            "bool": {
                "must": [query],
                "filter": [{"term": {"record_kind": "study"}}],
                "must_not": [{"term": {"is_review": True}}],
            }
        }

    async def count_studies(self, query: dict) -> int:
        """Size of one match set, so a filtered search can say what it removed.

        Uses the same predicate as ``aggregate``; otherwise the difference between
        a filtered and an unfiltered count would not be attributable to the filters.
        """
        await self.ensure_index()
        result = await self.client.count(index=self.index, query=self.match_set(query))
        return result["count"]

    async def aggregate(self, query: dict, sesoi: float, effect_type: str) -> dict:
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        runtime = bucket_runtime(sesoi, effect_type, today)
        q = self.match_set(query)
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
                "buckets": {
                    "terms": {
                        "field": "query_bucket",
                        "size": len(BUCKETS) + len(INCONCLUSIVE_REASONS),
                    }
                },
                "years": {
                    "date_histogram": {
                        "field": "publication_date",
                        "calendar_interval": "year",
                        "min_doc_count": 1,
                    }
                },
                "nulls": {
                    "filter": {"terms": {"query_bucket": ["credible_null", "reported_null"]}},
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
        reasons = dict.fromkeys(INCONCLUSIVE_REASONS, 0)
        for row in aggs["buckets"]["buckets"]:
            bucket, _, reason = row["key"].partition(":")
            counts[bucket] += row["doc_count"]
            if reason:
                reasons[reason] += row["doc_count"]
        registered = aggs["completed"]["doc_count"]
        unreported = aggs["completed"]["missing"]["doc_count"]
        return {
            "total": result["hits"]["total"]["value"],
            "bucketCounts": counts,
            "inconclusiveReasons": reasons,
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
