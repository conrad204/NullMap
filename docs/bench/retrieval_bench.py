"""Measure the OpenAlex-side cost and latency of the hybrid retrieval core.

Runs one query end to end -- multi-probe semantic + lexical + graph, RRF fusion,
batch hydration, local embedding rerank -- and prints per-stage wall time, the
number of distinct works recovered, and the dollar cost drawn from the OpenAlex
budget. LLM planning and evidence extraction are out of scope here; this measures
only what the API and the local rerank cost.

    pip install sentence-transformers   # optional, for the rerank stage
    OPENALEX_API_KEY=... python docs/bench/retrieval_bench.py

Numbers quoted in docs/openalex-mcp-scope.md come from this script.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

BASE = "https://api.openalex.org"
API_KEY = os.environ.get("OPENALEX_API_KEY")
SEMANTIC_MIN_INTERVAL = 1.05  # the documented semantic limit is 1 request/second
RERANK_MODEL = "thenlper/gte-small"

IDEA = (
    "We propose to test whether intermittent fasting improves insulin sensitivity in mice with "
    "diet-induced obesity, measuring glucose tolerance and HOMA-IR after eight weeks."
)

SEMANTIC_PROBES = [
    IDEA,
    IDEA + " Studies reporting no significant effect, null results, or failure to replicate.",
    IDEA + " Mechanism: hepatic insulin signaling, AMPK activation, circadian feeding window.",
    "Time-restricted feeding and glucose tolerance in rodent models of obesity: reported metabolic outcomes.",
    "Caloric restriction versus intermittent fasting for insulin resistance, including negative findings.",
]

LEXICAL_CLAUSES = [
    '("intermittent fasting" OR "time-restricted feeding") AND "insulin sensitivity"',
    '"no significant difference" AND ("intermittent fasting" OR "time-restricted feeding")',
    '("failed to replicate" OR "did not differ") AND fasting AND insulin',
    '"intermittent fasting" AND ("glucose tolerance test" OR "HOMA-IR")',
]

# Biology profile: Life Sciences + Health Sciences. Restricting to the five
# "pure biology" fields drops ~2/3 of relevant hits -- for a query like this one,
# 67% of matching works are classified primary_topic.field = Medicine.
BIOLOGY_DOMAINS = {"1", "4"}


@dataclass
class Ledger:
    cost_usd: float = 0.0
    calls: int = 0
    stages: dict[str, float] = field(default_factory=dict)

    def record(self, meta: dict) -> None:
        self.calls += 1
        self.cost_usd += float(meta.get("cost_usd") or 0.0)


LEDGER = Ledger()
_semantic_lock = asyncio.Lock()
_semantic_last = 0.0


def _get(params: dict, path: str = "/works") -> dict:
    if API_KEY:
        params = {**params, "api_key": API_KEY}
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            # 429 = over budget or over the per-second limit; 5xx = transient upstream
            if (exc.code != 429 and exc.code < 500) or attempt == 4:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


async def call(params: dict, path: str = "/works") -> dict:
    payload = await asyncio.to_thread(_get, params, path)
    LEDGER.record(payload.get("meta", {}))
    return payload


async def semantic(probe: str, per_page: int = 50) -> list[dict]:
    """Semantic requests are hard-limited to 1/second; parallel calls return 429."""
    global _semantic_last
    async with _semantic_lock:
        gap = time.monotonic() - _semantic_last
        if gap < SEMANTIC_MIN_INTERVAL:
            await asyncio.sleep(SEMANTIC_MIN_INTERVAL - gap)
        _semantic_last = time.monotonic()
    payload = await call(
        {
            "search.semantic": probe[:2000],
            "per-page": per_page,
            "select": "id,title,relevance_score",
            "filter": "has_abstract:true",
        }
    )
    return payload["results"]


async def lexical(clause: str, per_page: int = 100) -> list[dict]:
    payload = await call(
        {"search": clause, "per-page": per_page, "select": "id,title,relevance_score"}
    )
    return payload["results"]


def rrf(branches: list[list[dict]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for branch in branches:
        for rank, work in enumerate(branch):
            scores[work["id"]] = scores.get(work["id"], 0.0) + 1.0 / (k + rank + 1)
    return [wid for wid, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


async def hydrate(ids: list[str]) -> list[dict]:
    """Edges arrive with the metadata -- referenced_works costs nothing extra here."""
    select = (
        "id,doi,title,publication_year,type,cited_by_count,primary_topic,"
        "abstract_inverted_index,referenced_works,authorships,open_access"
    )
    chunks = [ids[i : i + 100] for i in range(0, len(ids), 100)]
    payloads = await asyncio.gather(
        *[
            call({"filter": "ids.openalex:" + "|".join(c), "per-page": 100, "select": select})
            for c in chunks
        ]
    )
    return [w for p in payloads for w in p["results"]]


async def cited_by(seed_ids: list[str], limit: int = 100) -> list[dict]:
    payload = await call(
        {
            "filter": "cites:" + "|".join(i.rsplit("/", 1)[-1] for i in seed_ids[:100]),
            "per-page": limit,
            "sort": "cited_by_count:desc",
            "select": "id,title",
        }
    )
    return payload["results"]


def abstract_of(work: dict) -> str:
    index = work.get("abstract_inverted_index")
    if not index:
        return ""
    positions: dict[int, str] = {}
    for word, spots in index.items():
        for spot in spots:
            positions[spot] = word
    return " ".join(positions[k] for k in sorted(positions))


def in_biology(work: dict) -> bool:
    topic = work.get("primary_topic") or {}
    domain_id = ((topic.get("domain") or {}).get("id") or "").rsplit("/", 1)[-1]
    return domain_id in BIOLOGY_DOMAINS


async def main() -> None:
    t_total = time.perf_counter()

    t = time.perf_counter()
    semantic_task = asyncio.gather(*[semantic(p) for p in SEMANTIC_PROBES])
    lexical_task = asyncio.gather(*[lexical(c) for c in LEXICAL_CLAUSES])
    semantic_branches, lexical_branches = await asyncio.gather(semantic_task, lexical_task)
    LEDGER.stages["retrieve (semantic ∥ lexical)"] = time.perf_counter() - t

    branches = list(semantic_branches) + list(lexical_branches)
    fused = rrf(branches)
    print(f"branches: {[len(b) for b in branches]}  fused distinct: {len(fused)}")
    print(f"semantic-only distinct: {len(rrf(list(semantic_branches)))}")
    print(f"single best probe:      {len(semantic_branches[0])}")

    t = time.perf_counter()
    works = await hydrate(fused[:150])
    LEDGER.stages["hydrate"] = time.perf_counter() - t

    scoped = [w for w in works if in_biology(w)]
    edges = sum(len(w.get("referenced_works") or []) for w in works)
    print(f"hydrated {len(works)}; biology post-filter keeps {len(scoped)}; "
          f"{edges} citation edges came along free")

    t = time.perf_counter()
    expansion = await cited_by([w["id"] for w in works[:100]])
    LEDGER.stages["graph expansion"] = time.perf_counter() - t
    print(f"citation expansion returned {len(expansion)} works in one call")

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("(sentence-transformers not installed; skipping rerank stage)")
    else:
        t = time.perf_counter()
        model = SentenceTransformer(RERANK_MODEL, device="cpu")
        LEDGER.stages["load rerank model (once per process)"] = time.perf_counter() - t

        texts = [f"{w['title'] or ''}. {abstract_of(w)}"[:2000] for w in scoped]
        t = time.perf_counter()
        doc_vecs = model.encode(texts, batch_size=16, normalize_embeddings=True)
        query_vec = model.encode([IDEA], normalize_embeddings=True)[0]
        LEDGER.stages[f"rerank {len(texts)} abstracts (cpu)"] = time.perf_counter() - t
        order = sorted(range(len(texts)), key=lambda i: -float(doc_vecs[i] @ query_vec))
        print("\ntop 5 after rerank:")
        for i in order[:5]:
            print(f"  {float(doc_vecs[i] @ query_vec):.3f}  {scoped[i]['title'][:90]}")

    print("\n-- stages --")
    for name, secs in LEDGER.stages.items():
        print(f"  {name:42s} {secs:6.2f}s")
    print(f"  {'TOTAL (openalex + local rerank)':42s} {time.perf_counter() - t_total:6.2f}s")
    print(f"\n{LEDGER.calls} API calls, ${LEDGER.cost_usd:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
