"""negative-priors: parse failed runs, index them, and check a new protocol against prior art.

Three entry points, in pipeline order:

    parse_raw_experiment(raw_text)        messy lab notes  -> Experiment (+ embedding)
    index_to_elastic(client, experiment)  Experiment       -> elasticsearch
    check_prior_risk(client, protocol)    protocol         -> PriorCheck

Every external dependency is optional and degrades instead of failing: the extractor uses an LLM
when `OPENAI_API_KEY` is set and a deterministic regex pass otherwise, embeddings come from OpenAI
or a local sentence-transformer, and the OpenAlex leg is skipped if the network is unavailable.
"""

from __future__ import annotations

import logging
import os
import re
import time
from functools import lru_cache
from typing import Iterable, Sequence

from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk

from schema import (
    AbstractVerdicts,
    Experiment,
    ExperimentDraft,
    ExperimentExtraction,
    OutcomeType,
    PriorCheck,
    PriorRisk,
)

try:  # optional: structured extraction + hosted embeddings
    from openai import OpenAI
except ImportError:  # pragma: no cover - exercised only where openai is absent
    OpenAI = None

try:  # optional: local embeddings, the default path
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover
    SentenceTransformer = None

try:  # optional: published prior art
    import pyalex
    from pyalex import Works
except ImportError:  # pragma: no cover
    pyalex = None
    Works = None

log = logging.getLogger("negative_priors")

INDEX = "negative-priors"
OPENALEX_SOURCE = "openalex"
LOCAL_EMBED_MODEL = "thenlper/gte-small"
OPENAI_EMBED_MODEL = "text-embedding-3-small"
OPENAI_PARSE_MODEL = os.environ.get("NP_PARSE_MODEL", "gpt-4o-mini")
SEMANTIC_MIN_INTERVAL = 1.05
CLASSIFY_BATCH = 10

EXTRACTION_PROMPT = """You convert messy experimental records into structured data.

Rules:
- `hypothesis` is what the run was testing, in one sentence, even if only implied.
- `independent_vars` are the things the experimenter set (compound, dose, temperature, construct).
- `dependent_vars` are what was measured (viability, IC50, expression, yield).
- `outcome_type`: "null" when the measurement showed no effect (p > 0.05, effect ~ 0),
  "negative" when the effect was real but in the unwanted direction (toxicity, loss of signal),
  "positive" when the hypothesis was supported, "inconclusive" when the run could not answer it
  (assay failure, contamination, underpowered).
- Copy numbers exactly; use null when a number is not stated. Never invent an effect size.
- `summary` is 1-2 sentences a colleague could act on."""

CLASSIFY_PROMPT = """You label published abstracts by what the study *found*, for a tool that warns
researchers when an experiment has already been tried and did not work.

Each item is `[index] title` followed by its abstract. Return one verdict per index:
- "null": the study looked for an effect and found none (no significant difference, p > 0.05).
- "negative": a real effect, but the unwanted one -- toxicity, resistance, loss of signal, a
  method or compound that did not work.
- "positive": the hypothesis was supported, or a working method/inhibitor/assay is reported.
- "inconclusive": a review, protocol, dataset, or a study that could not answer its question.

Judge the reported result, not the topic. Default to "inconclusive" when the abstract only
describes methods or the outcome is unclear."""


# --------------------------------------------------------------------------------------- embedding


class Embedder:
    """Whichever embedding backend is available, behind one call."""

    def __init__(self) -> None:
        self.backend: str
        if os.environ.get("OPENAI_API_KEY") and OpenAI is not None:
            self._client = OpenAI()
            self.backend = f"openai/{OPENAI_EMBED_MODEL}"
            self.dims = 1536
        elif SentenceTransformer is not None:
            self._model = SentenceTransformer(LOCAL_EMBED_MODEL)
            self.backend = f"local/{LOCAL_EMBED_MODEL}"
            self.dims = self._model.get_sentence_embedding_dimension()
        else:  # pragma: no cover
            raise RuntimeError("no embedding backend: set OPENAI_API_KEY or install sentence-transformers")

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.backend.startswith("openai"):
            resp = self._client.embeddings.create(model=OPENAI_EMBED_MODEL, input=list(texts))
            return [d.embedding for d in resp.data]
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [v.tolist() for v in vectors]


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder()


def embed(text: str) -> list[float]:
    return get_embedder()([text])[0]


def embed_many(texts: Sequence[str]) -> list[list[float]]:
    return get_embedder()(texts)


# ------------------------------------------------------------------------------------- extraction

P_VALUE = re.compile(r"\bp[\s-]*(?:value)?\s*([<>=~≈]{1,2})\s*(0?\.\d+|\d+(?:\.\d+)?e-\d+)", re.I)
EFFECT = re.compile(
    r"\b(?:cohen'?s\s*d|effect\s*size|\bd\b|log2fc|log2\s*fold[\s-]*change|fold[\s-]*change|"
    r"hedges'?\s*g|odds\s*ratio|\bor\b|\br\b)\s*[=:]\s*(-?\d+(?:\.\d+)?)",
    re.I,
)
# a variable assignment owns a whole comma/newline/sentence-delimited segment, so prose like
# "...control. delta = 2.1%" yields `delta`, not a 60-character pseudo-key
SEGMENT = re.compile(r"[,;|\n]|(?<=[.!?])\s+")
KEY_VALUE = re.compile(r"^\s*([A-Za-z][\w %/'-]{0,30})\s*[:=]\s*(.+?)\s*$")
HYPOTHESIS_CUE = re.compile(
    r"(?:hypothesis|aim|goal|objective|premise)\s*[:\-]\s*(.+)",
    re.I,
)

IV_WORDS = (
    "compound", "drug", "dose", "concentration", "conc", "temperature", "temp", "buffer", "solvent",
    "ph", "vehicle", "incubation", "time", "duration", "strain", "cell line", "cellline", "construct",
    "vector", "sirna", "guide", "treatment", "condition", "agonist", "inhibitor", "stimulus", "diet",
)
DV_WORDS = (
    "viability", "ic50", "ec50", "signal", "readout", "absorbance", "luminescence", "fluorescence",
    "expression", "yield", "growth", "proliferation", "titer", "activity", "response", "count",
    "rate", "delta", "auc", "od600", "mass", "weight", "output",
)
NULL_CUES = (
    "no significant", "not significant", "ns)", "n.s.", "no difference", "no effect", "null result",
    "did not differ", "failed to replicate", "failed to detect", "indistinguishable from control",
    "within noise", "no measurable",
)
NEGATIVE_CUES = (
    "toxic", "cytotox", "killed", "death", "worse", "decreas", "reduc", "loss of", "opposite",
    "precipitat", "crashed out", "insoluble", "degrad", "inhibited growth", "detrimental",
)
INCONCLUSIVE_CUES = (
    "inconclusive", "underpowered", "contaminat", "aborted", "discarded", "qc fail",
    "could not be assessed", "unusable", "instrument failure",
)
POSITIVE_CUES = (
    "significant increase", "significantly increased", "significantly higher", "confirmed",
    "as predicted", "supported the hypothesis", "robust effect", "reproduced",
)


def _sentences(text: str) -> list[str]:
    # unwrap soft line breaks first: lab notes wrap mid-sentence, and half a clause is a bad summary
    unwrapped = re.sub(r"(?<![.!?:])\n\s*", " ", text.strip())
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", unwrapped) if s.strip()]


def _maybe_number(value: str) -> float | str:
    value = value.strip().rstrip(".")
    try:
        return float(value)
    except ValueError:
        return value


def _classify(text: str, p_value: float | None, effect_size: float | None) -> OutcomeType:
    low = text.lower()
    hits = {
        "null": sum(cue in low for cue in NULL_CUES),
        "negative": sum(cue in low for cue in NEGATIVE_CUES),
        "inconclusive": sum(cue in low for cue in INCONCLUSIVE_CUES),
        "positive": sum(cue in low for cue in POSITIVE_CUES),
    }
    if p_value is not None:
        if p_value > 0.05:
            hits["null"] += 2
        else:
            hits["positive"] += 1
    if effect_size is not None and abs(effect_size) < 0.1:
        hits["null"] += 1
    best = max(hits, key=lambda k: hits[k])
    return best if hits[best] else "inconclusive"  # type: ignore[return-value]


def _short_value(raw_value: str) -> str | None:
    """A variable's value, or None when the text is prose that merely starts with `key:`."""
    value = raw_value.strip()
    if len(value) <= 40 and len(value.split()) <= 5:
        return value
    leading_number = re.match(r"-?\d+(?:\.\d+)?\s*[%\w/µ]{0,8}", value)
    return leading_number.group(0).strip() if leading_number else None


def _var_segment(segment: str) -> tuple[str, str] | None:
    match = KEY_VALUE.match(segment)
    if not match:
        return None
    value = _short_value(match.group(2))
    return (match.group(1), value) if value is not None else None


def _drop_var_listing(sentence: str) -> str:
    """Strip `key: value` runs out of a sentence so a summary reads as prose, not a parameter dump."""
    kept = [
        seg.strip()
        for seg in SEGMENT.split(sentence)
        if seg.strip() and _var_segment(seg) is None
    ]
    return " ".join(kept)


def _split_vars(text: str) -> tuple[dict[str, float | str], dict[str, float | str]]:
    independent: dict[str, float | str] = {}
    dependent: dict[str, float | str] = {}
    for segment in SEGMENT.split(text):
        pair = _var_segment(segment)
        if pair is None:
            continue
        raw_key, raw_value = pair
        key = re.sub(r"\s+", "_", raw_key.strip().lower())
        if not key or key in {"p", "p_value", "note", "notes", "run_id"}:
            continue
        value = _maybe_number(raw_value)
        if any(word in key for word in DV_WORDS):
            dependent[key] = value
        elif any(word in key for word in IV_WORDS):
            independent[key] = value
    return independent, dependent


def _heuristic_draft(raw_text: str) -> ExperimentDraft:
    p_match = P_VALUE.search(raw_text)
    p_value = float(p_match.group(2)) if p_match else None
    effect_match = EFFECT.search(raw_text)
    effect_size = float(effect_match.group(1)) if effect_match else None

    sentences = _sentences(raw_text)
    cue = HYPOTHESIS_CUE.search(raw_text)
    hypothesis = (
        cue.group(1).strip().split("\n")[0]
        if cue
        else (sentences[0] if sentences else raw_text[:160])
    )

    outcome = _classify(raw_text, p_value, effect_size)
    cues = {"null": NULL_CUES, "negative": NEGATIVE_CUES, "inconclusive": INCONCLUSIVE_CUES,
            "positive": POSITIVE_CUES}[outcome]
    prose = [c for c in (_drop_var_listing(s) for s in sentences if s != hypothesis) if c]
    observation = next(
        (s for s in prose if any(c in s.lower() for c in cues)),
        prose[-1] if prose else "",
    )
    independent, dependent = _split_vars(raw_text)
    if p_value is not None:
        dependent.setdefault("p_value", p_value)

    return ExperimentDraft(
        hypothesis=hypothesis,
        independent_vars=independent,
        dependent_vars=dependent,
        outcome_type=outcome,
        effect_size=effect_size,
        p_value=p_value,
        summary=" ".join(x for x in (hypothesis, observation) if x)[:600],
    )


def _llm_draft(raw_text: str) -> ExperimentDraft:
    client = OpenAI()
    completion = client.chat.completions.parse(
        model=OPENAI_PARSE_MODEL,
        messages=[
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        response_format=ExperimentExtraction,
        temperature=0,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:  # pragma: no cover - refusal path
        raise RuntimeError("model returned no parsed object")
    return parsed.to_draft()


def parse_raw_experiment(raw_text: str, source: str = "lab-notes") -> Experiment:
    """Turn a messy lab note or CSV excerpt into an `Experiment` with its embedding attached."""
    if os.environ.get("OPENAI_API_KEY") and OpenAI is not None:
        try:
            draft = _llm_draft(raw_text)
        except Exception as exc:  # network, quota, refusal -- the regex pass still works
            log.warning("LLM extraction failed (%s); using heuristic extractor", exc)
            draft = _heuristic_draft(raw_text)
    else:
        draft = _heuristic_draft(raw_text)

    experiment = Experiment(source=source, **draft.model_dump())
    experiment.embedding = embed(experiment.text_for_embedding())
    return experiment


# ------------------------------------------------------------------------------- openalex ingestion


def work_to_experiment(work: dict) -> Experiment:
    """An OpenAlex work as an `Experiment`, so published and internal evidence share one index."""
    abstract = _abstract_of(work)
    title = work.get("title") or "(untitled)"
    text = f"{title} {abstract}"
    p_match = P_VALUE.search(abstract)
    effect_match = EFFECT.search(abstract)
    return Experiment(
        experiment_id=work.get("id", "").rsplit("/", 1)[-1] or f"W{abs(hash(title))}",
        source=OPENALEX_SOURCE,
        hypothesis=title,
        outcome_type=_classify(text, None, None),
        p_value=float(p_match.group(2)) if p_match else None,
        effect_size=float(effect_match.group(1)) if effect_match else None,
        summary=abstract[:2000] or title,
        year=work.get("publication_year"),
        url=work.get("doi") or work.get("id"),
    )


def _llm_outcomes(experiments: Sequence[Experiment]) -> dict[int, OutcomeType]:
    """Classify abstracts `CLASSIFY_BATCH` at a time; one call per paper is the expensive way."""
    client = OpenAI()
    outcomes: dict[int, OutcomeType] = {}
    for start in range(0, len(experiments), CLASSIFY_BATCH):
        batch = experiments[start:start + CLASSIFY_BATCH]
        listing = "\n\n".join(
            f"[{i}] {e.hypothesis}\n{e.summary[:1200]}" for i, e in enumerate(batch)
        )
        completion = client.chat.completions.parse(
            model=OPENAI_PARSE_MODEL,
            messages=[
                {"role": "system", "content": CLASSIFY_PROMPT},
                {"role": "user", "content": listing},
            ],
            response_format=AbstractVerdicts,
            temperature=0,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:  # pragma: no cover - refusal path
            continue
        for verdict in parsed.verdicts:
            if 0 <= verdict.index < len(batch):
                outcomes[start + verdict.index] = verdict.outcome_type
    return outcomes


def classify_published(experiments: Sequence[Experiment]) -> None:
    """Relabel published work in place with an LLM read of the abstract.

    The heuristic classifier keys off phrases an experimenter writes ('no effect', 'crashed out');
    papers describe their own outcomes nothing like that, so on published text it is near noise.
    Without a key the heuristic labels stand.
    """
    if not experiments or not os.environ.get("OPENAI_API_KEY") or OpenAI is None:
        return
    try:
        outcomes = _llm_outcomes(experiments)
    except Exception as exc:
        log.warning("LLM classification failed (%s); keeping heuristic outcomes", exc)
        return
    for i, outcome in outcomes.items():
        experiments[i].outcome_type = outcome


def ingest_openalex(client: Elasticsearch, query: str, limit: int = 25) -> list[Experiment]:
    """Pull published work for a topic into the index so later checks kNN it instead of refetching.

    The live API is rate-limited and its semantic endpoint 504s often, so a demo-time corpus beats
    a per-query call; embeddings and outcome labels are batched.
    """
    works = _fetch_works(query, limit)
    experiments = [work_to_experiment(w) for w in works]
    if not experiments:
        return []

    classify_published(experiments)
    for exp, vector in zip(experiments, embed_many([e.text_for_embedding() for e in experiments])):
        exp.embedding = vector
    ensure_index(client, dims=len(experiments[0].embedding or []))
    bulk(
        client,
        (
            {"_index": INDEX, "_id": e.experiment_id, "_source": e.model_dump()}
            for e in experiments
        ),
        refresh="wait_for",
    )
    return experiments


# ------------------------------------------------------------------------------------ elasticsearch


def ensure_index(client: Elasticsearch, dims: int | None = None) -> None:
    if client.indices.exists(index=INDEX):
        return
    dims = dims or get_embedder().dims
    client.indices.create(
        index=INDEX,
        mappings={
            "properties": {
                "experiment_id": {"type": "keyword"},
                "source": {"type": "keyword"},
                "outcome_type": {"type": "keyword"},
                "hypothesis": {"type": "text"},
                "summary": {"type": "text"},
                "independent_vars": {"type": "object", "enabled": False},
                "dependent_vars": {"type": "object", "enabled": False},
                "effect_size": {"type": "float"},
                "p_value": {"type": "float"},
                "year": {"type": "integer"},
                "url": {"type": "keyword"},
                "embedding": {"type": "dense_vector", "dims": dims, "index": True,
                              "similarity": "cosine"},
            }
        },
    )


def index_to_elastic(client: Elasticsearch, exp: Experiment, refresh: bool = True) -> str:
    """Push one experiment into the `negative-priors` index (dense_vector + BM25 text)."""
    if exp.embedding is None:
        exp.embedding = embed(exp.text_for_embedding())
    ensure_index(client, dims=len(exp.embedding))
    client.index(
        index=INDEX,
        id=exp.experiment_id,
        document=exp.model_dump(),
        refresh="wait_for" if refresh else False,
    )
    return exp.experiment_id


# -------------------------------------------------------------------------------------- prior risk


def _origin_filter(origin: str) -> dict:
    """Published works are the same documents with `source == "openalex"`, so origin is a filter."""
    term = {"term": {"source": OPENALEX_SOURCE}}
    return {"bool": {"filter" if origin == "openalex" else "must_not": [term]}}


def _cosine_of(client: Elasticsearch, vector: list[float], ids: list[str]) -> dict[str, float]:
    """Cosine for BM25-only hits, which carry no kNN score; reporting 0.0 would hide a real prior."""
    if not ids:
        return {}
    hits = client.search(
        index=INDEX,
        query={
            "script_score": {
                "query": {"ids": {"values": ids}},
                # script_score rejects negative scores, hence the +1 shift
                "script": {
                    "source": "cosineSimilarity(params.q, 'embedding') + 1.0",
                    "params": {"q": vector},
                },
            }
        },
        size=len(ids),
        source=False,
    )["hits"]["hits"]
    return {h["_id"]: h["_score"] - 1.0 for h in hits}


def _index_matches(
    client: Elasticsearch,
    query: str,
    top_k: int,
    origin: str = "internal",
) -> list[PriorRisk]:
    """kNN and BM25 as separate legs: RRF decides the order, cosine stays as the reported score.

    Blending the two ES scores into one number would make `score` query-relative -- the top hit
    always looks perfect -- and the risk threshold needs an absolute similarity.
    """
    if not client.indices.exists(index=INDEX):
        return []
    vector = embed(query)
    scope = _origin_filter(origin)
    dense = client.search(
        index=INDEX,
        knn={"field": "embedding", "query_vector": vector, "k": top_k, "num_candidates": 100,
             "filter": scope},
        size=top_k,
        source_excludes=["embedding"],
    )["hits"]["hits"]
    lexical = client.search(
        index=INDEX,
        query={"bool": {
            "must": [{"multi_match": {"query": query, "fields": ["hypothesis^2", "summary"]}}],
            **scope["bool"],
        }},
        size=top_k,
        source_excludes=["embedding"],
    )["hits"]["hits"]

    sources = {h["_id"]: h["_source"] for h in dense + lexical}
    # ES reports cosine as (1 + cos) / 2
    cosine = {h["_id"]: 2 * h["_score"] - 1 for h in dense}
    cosine.update(_cosine_of(client, vector, [h["_id"] for h in lexical if h["_id"] not in cosine]))

    fused: dict[str, float] = {}
    for leg in (dense, lexical):
        for rank, hit in enumerate(leg):
            fused[hit["_id"]] = fused.get(hit["_id"], 0.0) + 1.0 / (60 + rank + 1)

    ordered = sorted(fused, key=lambda i: -fused[i])[:top_k]
    return [
        PriorRisk(
            origin=origin,  # type: ignore[arg-type]
            ref=sources[i]["experiment_id"],
            title=sources[i]["hypothesis"],
            outcome_type=sources[i]["outcome_type"],
            score=round(cosine.get(i, 0.0), 4),
            year=sources[i].get("year"),
            url=sources[i].get("url"),
            p_value=sources[i].get("p_value"),
            effect_size=sources[i].get("effect_size"),
            snippet=sources[i].get("summary", "")[:300],
        )
        for i in ordered
    ]


INTERNAL_CODE = re.compile(r"\b[A-Z]{1,4}[- ]?\d{2,6}\b")
BOILERPLATE = re.compile(r"\b(?:proposed\s+)?protocol\s*:|\bwe\s+propose\b", re.I)


def _generalize(query: str) -> str:
    return re.sub(r"\s+", " ", BOILERPLATE.sub("", INTERNAL_CODE.sub("", query))).strip()


def _abstract_of(work: dict) -> str:
    index = work.get("abstract_inverted_index") or {}
    if not index:
        return ""
    positions = [(pos, token) for token, spots in index.items() for pos in spots]
    return " ".join(token for _, token in sorted(positions))


def _openalex_query(query: str, top_k: int, semantic: bool) -> list[dict]:
    works = Works().filter(has_abstract=True)
    if semantic:
        # OpenAlex embeds the query itself; a whole protocol paragraph is a valid input here,
        # whereas the lexical endpoint treats it as a bag of words and returns noise
        works._add_params("search.semantic", query[:2000])
    else:
        works = works.search(query)
    return works.get(per_page=top_k)


def _fetch_works(query: str, top_k: int) -> list[dict]:
    """Semantic first, then lexical, then lexical without internal codes ('NP-114' is unpublished)."""
    if Works is None:
        return []
    pyalex.config.email = os.environ.get("OPENALEX_EMAIL", "hackmit-negative-priors@example.com")
    semantic_first = os.environ.get("NP_OPENALEX_SEMANTIC", "1") != "0"
    attempts = [(query, True)] * (2 if semantic_first else 0)
    attempts += [(query, False), (_generalize(query), False)]

    for i, (text, semantic) in enumerate(attempts):
        if not text:
            continue
        if i:
            time.sleep(SEMANTIC_MIN_INTERVAL)  # the semantic endpoint allows ~1 request/second
        try:
            works = _openalex_query(text, top_k, semantic)
        except Exception as exc:  # 504s are routine on the semantic endpoint
            log.warning("OpenAlex lookup failed (semantic=%s): %s", semantic, exc)
            continue
        if works:
            return works
    return []


def _published_matches(query: str, top_k: int) -> list[PriorRisk]:
    works = _fetch_works(query, top_k)
    matches: list[PriorRisk] = []
    for rank, work in enumerate(works[:top_k]):
        abstract = _abstract_of(work)
        text = f"{work.get('title') or ''} {abstract}"
        matches.append(
            PriorRisk(
                origin="openalex",
                ref=work.get("id", ""),
                title=work.get("title") or "(untitled)",
                outcome_type=_classify(text, None, None),
                score=round(1.0 / (rank + 1), 4),  # reciprocal rank: OpenAlex scores are not comparable
                year=work.get("publication_year"),
                url=work.get("doi") or work.get("id"),
                snippet=abstract[:300],
            )
        )
    return matches


# cosine is only comparable within one embedding space: gte-small puts unrelated biology text around
# 0.75 and a same-compound run around 0.89, while text-embedding-3-small spreads those to 0.27/0.75.
# risk ramps across each model's own band instead of over the whole [0, 1] range.
BANDS = {
    "local/thenlper/gte-small": (0.80, 0.95),
    "openai/text-embedding-3-small": (0.45, 0.80),
}
DEFAULT_BAND = (0.60, 0.90)
WEIGHT = {"null": 1.0, "negative": 0.9, "inconclusive": 0.4, "positive": 0.0}


def similarity_band() -> tuple[float, float]:
    """(similar, near-duplicate) cosine thresholds for the active embedding backend."""
    return BANDS.get(get_embedder().backend, DEFAULT_BAND)


def _score_risk(matches: Iterable[PriorRisk]) -> float:
    """Rescale cosine so only genuinely-close prior runs carry risk, then weight by outcome."""
    internal = [m for m in matches if m.origin == "internal"]
    if not internal:
        return 0.0
    similar, near_duplicate = similarity_band()
    span = near_duplicate - similar
    return round(
        max(
            min(1.0, max(0.0, (m.score - similar) / span)) * WEIGHT[m.outcome_type]
            for m in internal
        ),
        4,
    )


def check_prior_risk(
    client: Elasticsearch,
    protocol_or_hypothesis: str,
    top_k: int = 5,
    include_openalex: bool = True,
) -> PriorCheck:
    """Find prior attempts -- internal failures first, then published work -- for a proposal.

    Published work comes from whatever `ingest_openalex` has already indexed; the live API is only
    called when that corpus has nothing, so a warm index means no per-query OpenAlex round trip.
    """
    matches = _index_matches(client, protocol_or_hypothesis, top_k, origin="internal")
    if include_openalex:
        published = _index_matches(client, protocol_or_hypothesis, top_k, origin="openalex")
        matches += published or _published_matches(protocol_or_hypothesis, top_k)

    risk = _score_risk(matches)
    similar, _ = similarity_band()
    blocking = [
        m
        for m in matches
        if m.origin == "internal" and m.outcome_type in ("null", "negative") and m.score >= similar
    ]
    if risk >= 0.5 and blocking:
        outcomes = "/".join(sorted({m.outcome_type for m in blocking}))
        verdict = f"HIGH RISK -- {len(blocking)} internal run(s) already came back {outcomes}"
    elif risk >= 0.2:
        verdict = "CHECK FIRST -- a related internal run did not work; read it before repeating"
    else:
        verdict = "NO STRONG PRIOR -- nothing internal contradicts this yet"

    return PriorCheck(query=protocol_or_hypothesis, matches=matches, risk_score=risk, verdict=verdict)


def connect(url: str | None = None, api_key: str | None = None) -> Elasticsearch:
    """Local single-node by default; `ELASTIC_URL` + `ELASTIC_API_KEY` point at a hosted cluster."""
    url = url or os.environ.get("ELASTIC_URL", "http://localhost:9200")
    api_key = api_key or os.environ.get("ELASTIC_API_KEY")
    return Elasticsearch(url, api_key=api_key) if api_key else Elasticsearch(url)


def reset_index(client: Elasticsearch) -> None:
    client.indices.delete(index=INDEX, ignore_unavailable=True)


__all__ = [
    "Experiment",
    "PriorCheck",
    "PriorRisk",
    "check_prior_risk",
    "classify_published",
    "connect",
    "embed",
    "embed_many",
    "get_embedder",
    "index_to_elastic",
    "ingest_openalex",
    "parse_raw_experiment",
    "reset_index",
    "work_to_experiment",
]
