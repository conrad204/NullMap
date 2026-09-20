import asyncio
import math

import numpy as np
import pytest

from app.config import Settings
from app.gapmap_service import GapMapService
from app.models import MapArithmetic
from tests.test_gapmap import cluster, unit


class FakeRepository:
    def __init__(self, documents: list[dict], corpus: int | None = None):
        self.documents = documents
        self.corpus = len(documents) if corpus is None else corpus
        self.calls = 0
        self.knn_calls: list[int] = []

    async def count_embedded(self) -> int:
        return self.corpus

    async def scan_embedded(self, batch_size: int = 2000, slices: int = 8, limit: int = 0):
        self.calls += 1
        documents = self.documents if limit <= 0 else self.documents[:limit]
        for start in range(0, len(documents), batch_size):
            yield [dict(document) for document in documents[start : start + batch_size]]

    async def knn_studies(self, vector: list[float], limit: int, filters=None) -> list[dict]:
        self.knn_calls.append(limit)
        query = np.asarray(vector, dtype=np.float32)
        ranked = sorted(
            self.documents,
            key=lambda document: -float(np.dot(query, np.asarray(document["embedding"]))),
        )
        return [
            dict(document, cosine=float(np.dot(query, np.asarray(document["embedding"]))))
            for document in ranked[:limit]
        ]


def service(documents: list[dict], corpus: int | None = None, **overrides) -> GapMapService:
    config = Settings(
        _env_file=None, gapmap_regions=2, gapmap_seed=2, gapmap_batch=100, **overrides
    )
    return GapMapService(FakeRepository(documents, corpus), config=config)


def corpus() -> list[dict]:
    return cluster("null", 0.0, "reported_null", 8) + cluster("effect", 0.8, "effect", 8)


def test_map_describes_regions_and_gaps_without_shipping_centroids():
    result = asyncio.run(service(corpus()).assess())
    assert result["coverage"] == {
        "clustered": 16,
        "corpus": 16,
        "regions": 2,
        "drawn": 16,
        "complete": True,
        "scope": "corpus",
    }
    assert sorted(region["label"] for region in result["regions"]) == ["active", "null_saturated"]
    assert all("centroid" not in region for region in result["regions"])
    assert len(result["gaps"]) == 1
    assert result["placement"] is None and result["calibration"] is None
    assert result["warnings"] == []


def test_the_map_carries_a_2d_point_per_sampled_study():
    result = asyncio.run(service(corpus()).assess())
    assert len(result["points"]) == 16
    ids = {region["id"] for region in result["regions"]}
    for point in result["points"]:
        assert point["region"] in ids
        assert isinstance(point["x"], float) and isinstance(point["y"], float)
    assert all(
        isinstance(region["x"], float) and isinstance(region["y"], float)
        and "centroid" not in region
        for region in result["regions"]
    )


def test_the_layout_is_identical_across_rebuilds():
    """The same sample must draw the same map, or positions could not be trusted."""
    built = service(corpus())
    first = asyncio.run(built.assess())
    second = asyncio.run(built.assess(refresh=True))
    assert first["points"] == second["points"]
    assert [(r["x"], r["y"]) for r in first["regions"]] == [
        (r["x"], r["y"]) for r in second["regions"]
    ]


def test_a_partial_map_says_so():
    result = asyncio.run(service(corpus(), corpus=900).assess())
    assert result["warnings"] == [
        "The map clusters 16 of 900 embedded studies, not the whole index."
    ]


def test_a_scan_limit_is_reported_as_the_coverage_it_actually_reached():
    """A capped build must not describe itself as the index it did not read."""
    result = asyncio.run(service(corpus(), gapmap_scan_limit=8).assess())
    assert result["coverage"]["clustered"] == 8 and result["coverage"]["corpus"] == 16
    assert result["warnings"] == [
        "The map clusters 8 of 16 embedded studies, not the whole index."
    ]


def test_drawing_fewer_points_than_were_clustered_says_which_is_which():
    documents = cluster("null", 0.0, "reported_null", 200) + cluster(
        "effect", 0.8, "effect", 200
    )
    result = asyncio.run(service(documents, gapmap_points=100).assess())
    coverage = result["coverage"]
    assert coverage["clustered"] == 400
    assert coverage["drawn"] == len(result["points"]) < 400
    assert result["warnings"] == [
        f"All 400 clustered studies shape the regions; the canvas draws {coverage['drawn']} "
        "of them."
    ]


def test_the_build_is_streamed_as_it_happens_and_only_the_last_state_is_whole():
    """Progress must be the real intermediate map, never a replay of the result."""
    built = service(corpus())
    events: list[dict] = []

    async def run():
        async def progress(payload):
            events.append(payload)

        return await built.stream(progress=progress)

    result = asyncio.run(run())
    assert events, "the build published no intermediate state"
    assert all(not event["coverage"]["complete"] for event in events)
    assert all(event["coverage"]["corpus"] == 16 for event in events)
    assert [event["stage"] for event in events][-1] == "clustering"
    assert any(event["points"] for event in events)
    assert result["coverage"]["complete"] is True


def test_a_cached_map_streams_no_progress():
    built = service(corpus())
    asyncio.run(built.assess())
    events: list[dict] = []

    async def run():
        async def progress(payload):
            events.append(payload)

        return await built.stream(progress=progress)

    assert asyncio.run(run())["coverage"]["complete"] is True
    assert events == []


def test_the_map_is_built_once_and_rebuilt_on_request():
    built = service(corpus())
    asyncio.run(built.assess())
    asyncio.run(built.assess())
    assert built.repo.calls == 1
    asyncio.run(built.assess(refresh=True))
    assert built.repo.calls == 2


def test_an_idea_is_placed_against_the_nearest_paper():
    built = service(corpus())
    built.embed = lambda text: _resolved(unit(0.01))
    result = asyncio.run(built.assess(idea="a new trial of the same thing"))
    placement = result["placement"]
    assert built.repo.calls == 0, "a question must not scan the whole index"
    assert built.repo.knn_calls == [built.config.gapmap_neighborhood]
    assert result["coverage"]["scope"] == "neighborhood"
    assert placement["redundancy"] == pytest.approx(1.0, abs=0.01)
    assert placement["region"]["label"] == "null_saturated"
    assert "centroid" not in placement["region"]
    point = placement["point"]
    region = next(r for r in result["regions"] if r["label"] == "null_saturated")
    assert math.hypot(point["x"] - region["x"], point["y"] - region["y"]) < 0.05


def test_an_expression_is_placed_and_echoed_back():
    """start − remove + add lands where the combination points, not where start was."""
    built = service(corpus())
    vectors = {"start": unit(0.0), "old context": unit(0.0), "new context": unit(0.8)}
    built.embed = lambda text: _resolved(vectors[text])
    expression = MapArithmetic(start="start", remove=["old context"], add=["new context"])
    result = asyncio.run(built.assess(arithmetic=expression))
    placement = result["placement"]
    assert placement["expression"] == {
        "start": "start",
        "remove": ["old context"],
        "add": ["new context"],
    }
    assert placement["region"]["label"] == "active"
    assert len(placement["neighbors"]) == 4
    assert isinstance(placement["point"]["x"], float)


def test_an_expression_that_cancels_out_warns_instead_of_placing():
    built = service(corpus())
    built.embed = lambda text: _resolved(unit(0.0))
    expression = MapArithmetic(start="same thing", remove=["same thing"], add=[])
    result = asyncio.run(built.assess(arithmetic=expression))
    assert result["placement"] is None
    assert result["warnings"] == [
        "The expression cancels itself out; the combined vector has no direction."
    ]


def test_a_question_builds_its_neighborhood_and_says_so():
    """The map around a question is the N nearest studies, and the coverage says exactly that."""
    documents = cluster("null", 0.0, "reported_null", 60) + cluster("effect", 0.8, "effect", 60)
    built = service(documents, corpus=5000, gapmap_neighborhood=50)
    built.embed = lambda text: _resolved(unit(0.01))
    result = asyncio.run(built.assess(idea="a new trial of the same thing"))
    assert built.repo.knn_calls == [50]
    assert built.repo.calls == 0
    coverage = result["coverage"]
    assert coverage["scope"] == "neighborhood"
    assert coverage["neighborhood"] == 50
    assert coverage["clustered"] == 50 and coverage["corpus"] == 5000
    assert coverage["drawn"] == len(result["points"]) == 50
    assert result["warnings"] == [
        "The map shows the 50 embedded studies nearest this question, out of 5000 in the "
        "index: the question's neighborhood, not the whole index."
    ]
    assert all("sample" not in warning.lower() for warning in result["warnings"])
    assert all(isinstance(edge[2], float) for edge in result["edges"])
    assert len(result["edges"]) > 0


def test_placement_is_impossible_without_embeddings():
    built = service(corpus(), embeddings_enabled=False)
    result = asyncio.run(built.assess(idea="a new trial of the same thing"))
    assert result["placement"] is None
    assert result["warnings"] == ["Embeddings are disabled, so the idea could not be placed."]


def test_calibration_is_returned_only_when_a_cutoff_is_given():
    documents = (
        cluster("null", 0.0, "reported_null", 8, year=2018)
        + cluster("effect", 0.8, "effect", 8, year=2018)
        + cluster("nulllater", 0.02, "reported_null", 4, year=2023)
    )
    result = asyncio.run(service(documents).assess(cutoff_year=2020))
    assert result["calibration"]["cutoffYear"] == 2020
    assert result["calibration"]["future"] == 4


def test_coverage_never_reports_fewer_studies_than_it_clustered():
    """The count runs beside the scan, so ingestion can outrun it; it may not shrink coverage."""
    result = asyncio.run(service(corpus(), corpus=4).assess())
    assert result["coverage"]["clustered"] == 16
    assert result["coverage"]["corpus"] == 16
    assert result["warnings"] == []


def test_the_question_is_placed_on_the_streamed_map_before_it_is_finished():
    built = service(corpus())
    built.embed = lambda text: _resolved(unit(0.01))
    events: list[dict] = []

    async def run():
        async def progress(payload):
            events.append(payload)

        return await built.stream(idea="a new trial of the same thing", progress=progress)

    asyncio.run(run())
    placed = [event["placement"] for event in events if event.get("placement")]
    assert placed, "the question never reached the streaming map"
    assert all(isinstance(place["x"], float) and isinstance(place["y"], float) for place in placed)


def _resolved(value):
    future: asyncio.Future = asyncio.Future()
    future.set_result(value)
    return future
