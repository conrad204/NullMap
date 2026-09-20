import asyncio
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

import app.ingest.pagerank as pagerank_module
from app.ingest.__main__ import parser, run
from app.ingest.pagerank import pagerank, update_authority


def test_pagerank_prefers_the_work_two_papers_cite():
    # A -> B -> C and C -> B: B is cited by both A and C; nothing cites A.
    scores = pagerank(["A", "B", "C"], [(0, 1), (1, 2), (2, 1)])
    assert scores[1] == max(scores)
    assert scores[1] > scores[2] > scores[0]
    assert scores.sum() == pytest.approx(1.0)


def test_dangling_node_redistributes_uniformly_without_nan():
    # A -> B, D has no out-edges; its mass spreads evenly each iteration.
    scores = pagerank(["A", "B", "D"], [(0, 1)])
    assert np.isfinite(scores).all()
    assert scores.sum() == pytest.approx(1.0)
    assert scores[1] > scores[0]
    # Uncited A and dangling D share only teleport plus dangling mass.
    assert scores[0] == pytest.approx(scores[2])


def test_duplicate_and_self_edges_do_not_inflate_out_degree():
    plain = pagerank(["A", "B"], [(0, 1)])
    noisy = pagerank(["A", "B"], [(0, 1), (0, 1), (1, 1)])
    assert noisy == pytest.approx(plain)


def test_empty_corpus_and_bad_parameters():
    assert pagerank([], []).size == 0
    with pytest.raises(ValueError):
        pagerank(["A"], [], damping=1.0)


def test_update_authority_writes_log_scaled_signals(monkeypatch):
    docs = [
        {"_id": "A", "_source": {"referenced_works": ["B"], "cited_by_count": 0}},
        {"_id": "B", "_source": {"referenced_works": ["C"], "cited_by_count": 9}},
        {
            "_id": "C",
            # "outside" is not in the index; the edge must be dropped.
            "_source": {"referenced_works": ["B", "outside"], "cited_by_count": None},
        },
        {"_id": "D", "_source": {"referenced_works": [], "cited_by_count": 100}},
    ]

    async def scan(client, **kwargs):
        assert kwargs["query"]["query"] == {"term": {"record_kind": "study"}}
        for doc in docs:
            yield doc

    written = []

    async def bulk(client, actions, **kwargs):
        batch = list(actions)
        written.extend(batch)
        return len(batch), []

    monkeypatch.setattr(pagerank_module, "async_scan", scan)
    monkeypatch.setattr(pagerank_module, "async_bulk", bulk)
    repository = SimpleNamespace(client=object(), index="studies")
    summary = asyncio.run(update_authority(repository))
    assert summary["nodes"] == 4 and summary["edges"] == 3 and summary["updates"] == 4
    assert summary["top_pagerank"][0]["id"] == "B"
    patches = {action["_id"]: action["doc"] for action in written}
    assert all(action["_index"] == "studies" and action["_op_type"] == "update"
               for action in written)
    assert all(patch["pagerank"] > 0 for patch in patches.values())
    assert patches["B"]["authority"] == pytest.approx(math.log1p(9))
    assert patches["D"]["authority"] == pytest.approx(math.log1p(100))
    # rank_feature rejects non-positive values; zero or missing counts omit it.
    assert "authority" not in patches["A"]
    assert "authority" not in patches["C"]


def test_pagerank_subcommand_passes_flags_through(monkeypatch):
    captured = {}

    async def fake_update(repository, **kwargs):
        captured.update(kwargs)
        return {"nodes": 0, "edges": 0, "updates": 0, "top_pagerank": []}

    import app.repository

    monkeypatch.setattr(pagerank_module, "update_authority", fake_update)
    monkeypatch.setattr(
        app.repository, "ElasticRepository", lambda: SimpleNamespace(close=AsyncMock())
    )
    arguments = parser().parse_args(["pagerank", "--damping", "0.5", "--iterations", "10"])
    result = asyncio.run(run(arguments))
    assert captured == {"damping": 0.5, "iterations": 10, "batch_size": 500}
    assert result["nodes"] == 0
