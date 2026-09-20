"""Within-corpus PageRank over the index's own citation graph.

``referenced_works`` entries are bare work IDs that map directly to document
``_id`` values, so the graph is whatever the index already holds: an edge
i -> j exists when indexed work i cites indexed work j. References outside
the corpus are dropped, which means the score is *within-corpus* authority —
how the indexed literature cites a work — not a global citation rank.

The pass stores ``pagerank`` as ``log10(pr * n + 1)`` (always positive, as the
rank_feature mapping requires) and also backfills ``authority`` as
``log1p(cited_by_count)`` so neither signal needs a full re-ingest.
"""

import math
from collections.abc import Iterable

import numpy as np
import scipy.sparse
from elasticsearch.helpers import async_bulk, async_scan


def pagerank(
    ids: list[str],
    edges: Iterable[tuple[int, int]],
    *,
    damping: float = 0.85,
    iterations: int = 30,
) -> np.ndarray:
    """Power-iterate PageRank; returns a probability vector aligned with ``ids``.

    ``edges`` are ``(source, target)`` index pairs into ``ids``. Duplicate and
    self edges collapse; dangling nodes (no out-edges) redistribute their mass
    uniformly, so scores always sum to ~1.
    """
    if not 0 < damping < 1:
        raise ValueError("damping must be in (0, 1)")
    if iterations < 0:
        raise ValueError("iterations must be non-negative")
    n = len(ids)
    scores = np.full(n, 1.0 / n) if n else np.zeros(0)
    if n == 0:
        return scores
    pairs = {(i, j) for i, j in edges if 0 <= i < n and 0 <= j < n and i != j}
    rows = [i for i, _ in pairs]
    cols = [j for _, j in pairs]
    adjacency = scipy.sparse.csr_matrix(
        (np.ones(len(pairs)), (rows, cols)), shape=(n, n), dtype=np.float64
    )
    out_degree = np.asarray(adjacency.sum(axis=1)).ravel()
    linked = out_degree > 0
    inv_out = np.zeros(n)
    inv_out[linked] = 1.0 / out_degree[linked]
    transition = scipy.sparse.diags(inv_out) @ adjacency
    teleport = (1.0 - damping) / n
    for _ in range(iterations):
        dangling_share = damping * scores[~linked].sum() / n
        scores = damping * (transition.T @ scores) + teleport + dangling_share
    return scores


async def update_authority(
    repository,
    *,
    damping: float = 0.85,
    iterations: int = 30,
    batch_size: int = 500,
) -> dict:
    """Score the indexed citation graph and bulk-update both rank features.

    One scroll reads ``record_kind=study`` IDs, references and citation counts;
    one bulk pass writes the signals back. Edges to works outside the index are
    dropped, so the graph is exactly the stored corpus.
    """
    ids: list[str] = []
    references: list[list[str]] = []
    citations: list = []
    async for hit in async_scan(
        repository.client,
        index=repository.index,
        query={
            "query": {"term": {"record_kind": "study"}},
            "_source": ["referenced_works", "cited_by_count"],
        },
        size=batch_size,
    ):
        ids.append(hit["_id"])
        source = hit.get("_source") or {}
        references.append(
            [
                str(ref).rsplit("/", 1)[-1]
                for ref in source.get("referenced_works") or []
            ]
        )
        citations.append(source.get("cited_by_count"))
    position = {identifier: i for i, identifier in enumerate(ids)}
    edges = {
        (i, position[ref])
        for i, refs in enumerate(references)
        for ref in refs
        if ref in position
    }
    scores = pagerank(ids, sorted(edges), damping=damping, iterations=iterations)
    n = len(ids)
    actions = []
    for i, identifier in enumerate(ids):
        patch = {"pagerank": math.log10(scores[i] * n + 1.0)}
        count = citations[i]
        if isinstance(count, (int, float)) and not isinstance(count, bool) and count > 0:
            patch["authority"] = math.log1p(count)
        actions.append(
            {"_op_type": "update", "_index": repository.index, "_id": identifier, "doc": patch}
        )
    updates = 0
    if actions:
        updates, _ = await async_bulk(
            repository.client, actions, refresh="wait_for", chunk_size=batch_size
        )
    top = np.argsort(-scores)[:5] if n else []
    return {
        "nodes": n,
        "edges": len(edges),
        "updates": int(updates),
        "top_pagerank": [{"id": ids[i], "pagerank": float(scores[i])} for i in top],
    }
