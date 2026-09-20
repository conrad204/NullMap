import math
from collections import Counter

import numpy as np
import pytest

from app.gapmap import (
    build_regions,
    calibrate,
    find_gaps,
    kmeans,
    label_region,
    neighbour_threshold,
    normalize,
    place,
)


def unit(angle: float, radius: float = 1.0) -> list[float]:
    """A point on the unit circle embedded in three dimensions."""
    return [math.cos(angle) * radius, math.sin(angle) * radius, 0.0]


def study(identifier: str, angle: float, bucket: str, **fields) -> dict:
    document = {
        "id": identifier,
        "title": f"Study {identifier}",
        "embedding": unit(angle),
        "bucket": bucket,
        "year": 2020,
        "cited_by_count": 1,
    }
    document.update(fields)
    return document


def cluster(prefix: str, centre: float, bucket: str, count: int, **fields) -> list[dict]:
    return [
        study(f"{prefix}{index}", centre + index * 0.004, bucket, **fields)
        for index in range(count)
    ]


def test_kmeans_separates_two_clumps_and_is_deterministic():
    vectors = normalize(
        np.array([unit(0.0), unit(0.02), unit(0.04), unit(3.0), unit(3.02)], dtype=np.float32)
    )
    labels = kmeans(vectors, 2, seed=3)
    assert len(set(labels[:3].tolist())) == 1
    assert len(set(labels[3:].tolist())) == 1
    assert labels[0] != labels[3]
    assert labels.tolist() == kmeans(vectors, 2, seed=3).tolist()


def test_kmeans_handles_fewer_points_than_clusters():
    vectors = normalize(np.array([unit(0.0), unit(2.0)], dtype=np.float32))
    assert sorted(kmeans(vectors, 5, seed=1).tolist()) == [0, 1]


@pytest.mark.parametrize(
    "counts,attempts,expected",
    [
        ({"effect": 2, "reported_null": 1}, 3, "thin"),
        ({"unreported": 4, "effect": 6}, 10, "dark"),
        ({"reported_null": 4, "credible_null": 2, "effect": 4}, 10, "null_saturated"),
        ({"effect": 6, "reported_null": 3, "inconclusive": 1}, 10, "contested"),
        ({"effect": 8, "inconclusive": 2}, 10, "active"),
    ],
)
def test_label_region_rules(counts, attempts, expected):
    assert label_region(Counter(counts), attempts) == expected


def test_dark_outranks_null_saturation():
    """Unreported trials are absence of a report, so they name the region first."""
    counts = Counter({"unreported": 5, "reported_null": 5})
    assert label_region(counts, 10) == "dark"


def test_build_regions_describes_each_clump_by_what_happened_there():
    documents = (
        cluster("effect", 0.0, "effect", 6)
        + cluster("null", 2.0, "reported_null", 6)
        + cluster("dark", 4.0, "unreported", 6)
    )
    built = build_regions(documents, regions=3, seed=5)
    assert built["documents"] == 18 and built["skipped"] == 0
    assert sorted(region["label"] for region in built["regions"]) == [
        "active",
        "dark",
        "null_saturated",
    ]
    for region in built["regions"]:
        assert region["attempts"] == 6
        assert region["coherence"] > 0.99
        assert len(region["exemplars"]) == 3


def test_reviews_do_not_evidence_a_region():
    documents = cluster("null", 0.0, "reported_null", 6) + [
        study("review", 0.01, "effect", is_review=True, cited_by_count=900)
    ]
    region = build_regions(documents, regions=1, seed=1)["regions"][0]
    assert region["size"] == 7 and region["attempts"] == 6
    assert region["label"] == "null_saturated"
    assert region["bucketCounts"] == {"reported_null": 6}


def test_unembedded_documents_are_reported_not_dropped():
    documents = cluster("effect", 0.0, "effect", 5) + [
        {"id": "bare", "bucket": "effect"},
        {"id": "empty", "bucket": "effect", "embedding": []},
    ]
    built = build_regions(documents, regions=1, seed=1)
    assert built["documents"] == 5 and built["skipped"] == 2


def test_build_regions_without_any_vector():
    built = build_regions([{"id": "bare"}], regions=3)
    assert built == {"version": "gapmap-v1", "regions": [], "documents": 0, "skipped": 1}


def test_find_gaps_reports_the_unoccupied_band_between_neighbours():
    documents = cluster("left", 0.0, "effect", 8) + cluster("right", 0.8, "effect", 8)
    built = build_regions(documents, regions=2, seed=2)
    gaps = find_gaps(documents, built["regions"])
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["support"] == 8 and gap["band"] == 0
    assert gap["openness"] == 1.0
    assert gap["nearest"]["cosine"] == pytest.approx(math.cos(0.4), abs=0.02)
    assert gap["discouraged"] is False


def test_a_gap_between_failed_literatures_is_flagged_discouraged():
    documents = cluster("left", 0.0, "reported_null", 8) + cluster("right", 0.8, "effect", 8)
    built = build_regions(documents, regions=2, seed=2)
    assert find_gaps(documents, built["regions"])[0]["discouraged"] is True


def test_unrelated_literatures_do_not_make_a_gap():
    """An empty midpoint between distant regions is not an implied combination."""
    documents = cluster("left", 0.0, "effect", 8) + cluster("right", 2.6, "effect", 8)
    built = build_regions(documents, regions=2, seed=2)
    assert find_gaps(documents, built["regions"]) == []


def test_neighbours_are_judged_against_the_corpus_own_spread():
    """Adjacency is relative: 0.45 is close in a corpus whose regions are farther."""
    documents = cluster("left", 0.0, "effect", 8) + cluster("right", 1.1, "effect", 8)
    built = build_regions(documents, regions=2, seed=2)
    assert neighbour_threshold(
        normalize(np.vstack([region["centroid"] for region in built["regions"]]))
    ) == pytest.approx(math.cos(1.1), abs=0.02)
    assert len(find_gaps(documents, built["regions"])) == 1
    assert find_gaps(documents, built["regions"], neighbour_cosine=0.9) == []


def test_a_band_someone_already_works_in_is_not_a_gap():
    documents = (
        cluster("left", 0.0, "effect", 8)
        + cluster("middle", 0.4, "effect", 8)
        + cluster("right", 0.8, "effect", 8)
    )
    built = build_regions(documents, regions=3, seed=2)
    named = {region["id"]: region["exemplars"][0]["id"][:-1] for region in built["regions"]}
    spanned = {
        frozenset(named[region] for region in gap["regions"])
        for gap in find_gaps(documents, built["regions"])
    }
    assert frozenset({"left", "right"}) not in spanned


def test_place_reports_redundancy_against_the_closest_paper():
    documents = cluster("null", 0.0, "reported_null", 8) + cluster("effect", 0.8, "effect", 8)
    built = build_regions(documents, regions=2, seed=2)
    gaps = find_gaps(documents, built["regions"])
    placed = place(unit(0.01), documents, built["regions"], gaps)
    assert placed["redundancy"] > 0.99
    assert placed["nearest"]["bucket"] == "reported_null"
    assert placed["region"]["label"] == "null_saturated"
    assert "centroid" not in placed["region"]
    assert placed["nearestGap"]["openness"] > 0


def test_place_on_an_empty_corpus_says_nothing():
    assert place(unit(0.0), [], [])["redundancy"] is None


def test_calibrate_scores_labels_against_what_came_later():
    documents = (
        cluster("null", 0.0, "reported_null", 8, year=2018)
        + cluster("effect", 2.0, "effect", 8, year=2018)
        + cluster("nulllater", 0.02, "reported_null", 4, year=2023)
        + cluster("effectlater", 2.02, "effect", 4, year=2023)
    )
    report = calibrate(documents, cutoff_year=2020, regions=2, seed=2)
    assert report["past"] == 16 and report["future"] == 8
    rows = {row["label"]: row for row in report["labels"]}
    assert rows["null_saturated"]["laterNullShare"] == 1.0
    assert rows["active"]["laterNullShare"] == 0.0
    assert rows["active"]["laterEffect"] == 4


def test_calibrate_without_later_papers_reports_nothing():
    documents = cluster("null", 0.0, "reported_null", 8, year=2018)
    report = calibrate(documents, cutoff_year=2020, regions=2, seed=2)
    assert report["future"] == 0 and report["labels"] == []
