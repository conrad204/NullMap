import asyncio
import math

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

    async def sample_embedded(self, limit: int = 4000, seed: int = 0) -> dict:
        self.calls += 1
        return {"documents": self.documents[:limit], "corpus": self.corpus}


def service(documents: list[dict], corpus: int | None = None, **overrides) -> GapMapService:
    config = Settings(gapmap_sample=1000, gapmap_regions=2, gapmap_seed=2, **overrides)
    return GapMapService(FakeRepository(documents, corpus), config=config)


def corpus() -> list[dict]:
    return cluster("null", 0.0, "reported_null", 8) + cluster("effect", 0.8, "effect", 8)


def test_map_describes_regions_and_gaps_without_shipping_centroids():
    result = asyncio.run(service(corpus()).assess())
    assert result["coverage"] == {"sampled": 16, "corpus": 16, "regions": 2}
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


def test_a_sampled_map_says_so():
    result = asyncio.run(service(corpus(), corpus=900).assess())
    assert result["warnings"] == [
        "The map describes a random sample of 16 of 900 embedded studies, not the whole index."
    ]


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


def _resolved(value):
    future: asyncio.Future = asyncio.Future()
    future.set_result(value)
    return future
