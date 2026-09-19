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

from schema import (
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
LOCAL_EMBED_MODEL = "thenlper/gte-small"
OPENAI_EMBED_MODEL = "text-embedding-3-small"
OPENAI_PARSE_MODEL = os.environ.get("NP_PARSE_MODEL", "gpt-4o-mini")
SEMANTIC_MIN_INTERVAL = 1.05

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


def _internal_matches(client: Elasticsearch, query: str, top_k: int) -> list[PriorRisk]:
    """kNN and BM25 as separate legs: RRF decides the order, cosine stays as the reported score.

    Blending the two ES scores into one number would make `score` query-relative -- the top hit
    always looks perfect -- and the risk threshold needs an absolute similarity.
    """
    if not client.indices.exists(index=INDEX):
        return []
    vector = embed(query)
    dense = client.search(
        index=INDEX,
        knn={"field": "embedding", "query_vector": vector, "k": top_k, "num_candidates": 100},
        size=top_k,
        source_excludes=["embedding"],
    )["hits"]["hits"]
    lexical = client.search(
        index=INDEX,
        query={"multi_match": {"query": query, "fields": ["hypothesis^2", "summary"]}},
        size=top_k,
        source_excludes=["embedding"],
    )["hits"]["hits"]

    sources = {h["_id"]: h["_source"] for h in dense + lexical}
    # ES reports cosine as (1 + cos) / 2
    cosine = {h["_id"]: 2 * h["_score"] - 1 for h in dense}
    fused: dict[str, float] = {}
    for leg in (dense, lexical):
        for rank, hit in enumerate(leg):
            fused[hit["_id"]] = fused.get(hit["_id"], 0.0) + 1.0 / (60 + rank + 1)

    ordered = sorted(fused, key=lambda i: -fused[i])[:top_k]
    return [
        PriorRisk(
            origin="internal",
            ref=sources[i]["experiment_id"],
            title=sources[i]["hypothesis"],
            outcome_type=sources[i]["outcome_type"],
            score=round(cosine.get(i, 0.0), 4),
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


def _published_matches(query: str, top_k: int) -> list[PriorRisk]:
    if Works is None:
        return []
    pyalex.config.email = os.environ.get("OPENALEX_EMAIL", "hackmit-negative-priors@example.com")
    semantic_first = os.environ.get("NP_OPENALEX_SEMANTIC", "1") != "0"
    attempts = [(query, True)] * (2 if semantic_first else 0)
    # last resort: lexical, then lexical without internal codes ('NP-114' matches nothing published)
    attempts += [(query, False), (_generalize(query), False)]

    works: list[dict] = []
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
            break

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
    """Find prior attempts -- internal failures first, then published work -- for a proposal."""
    matches = _internal_matches(client, protocol_or_hypothesis, top_k)
    if include_openalex:
        matches += _published_matches(protocol_or_hypothesis, top_k)

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
    "connect",
    "embed",
    "get_embedder",
    "index_to_elastic",
    "parse_raw_experiment",
    "reset_index",
]
