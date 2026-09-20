"""A map of what the literature is missing, built from the indexed corpus alone.

Retrieval answers "what exists". This module answers the complementary question:
for a region of the embedding space, *why* is nothing there. Three kinds of absence
are distinguishable from the fields the index already stores and are not the same
thing at all:

- **null-saturated** — the work was done and came back null or inconclusive. An idea
  landing here is not novel, it is refuted, and the refutations are usually
  uncited, which is exactly why a reader (or a language model summarising the top
  ten hits) concludes the question is open.
- **dark** — trials completed, results never reported. Absence of a paper, not
  absence of evidence.
- **gap** — no work between two populated regions. A combination the neighbouring
  literature implies but nobody has run.

Regions are clusters of real documents, so they can only describe where work exists.
Gaps therefore come from *interpolation*: the midpoint between two nearby region
centroids is a point the literature implies but may never have occupied, and its
cosine to the nearest real paper measures how unoccupied it is. Nothing here calls
an LLM or Elasticsearch, and no score is a probability: every number is a count, a
share of counts, or a cosine.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

MAP_VERSION = "gapmap-v1"

# A region needs this many primary studies before its verdict mix means anything;
# below it the honest label is "thin", not a finding about the science.
MIN_REGION_ATTEMPTS = 5
# Share of attempts that must be null (or unreported) before the region is called
# saturated (or dark). Both are deliberately majority-ish rather than "any".
NULL_SATURATED_SHARE = 0.5
DARK_SHARE = 0.34
# Both directions must be well represented for a region to count as contested.
CONTESTED_SHARE = 0.25
# Two regions are neighbours, and so worth interpolating between, when their
# centroid cosine is in the top decile of all region pairs; below that the
# midpoint is empty for the boring reason that the two literatures are unrelated.
# A quantile rather than a fixed cosine because what counts as "close" is a
# property of the embedding model and of how narrow the indexed corpus is: on
# MiniLM, unrelated clinical topics sit around 0.1-0.3, so any constant either
# admits everything in a single-disease index or nothing in a broad one. The
# floor stops a corpus of entirely unrelated clusters from producing neighbours
# by ranking alone.
NEIGHBOUR_QUANTILE = 0.9
NEIGHBOUR_FLOOR = 0.35
# The band between two regions is open when it holds at most this share of the
# papers the thinner of the two parent cores holds. A ratio, because an absolute
# cosine cannot express "between": every midpoint is close to its own parents.
GAP_OCCUPANCY = 0.1
# A core, a band and the other core are sampled with balls of a quarter of the
# centroid separation, which makes the three disjoint by construction.
BALL_FRACTION = 0.25

REGION_LABELS = ("active", "contested", "null_saturated", "dark", "thin")
NULL_BUCKETS = ("credible_null", "reported_null")


def _vector(document: dict) -> np.ndarray | None:
    embedding = document.get("embedding")
    if not isinstance(embedding, (list, tuple)) or not embedding:
        return None
    vector = np.asarray(embedding, dtype=np.float32)
    return vector if np.isfinite(vector).all() and vector.any() else None


def normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.clip(norms, 1e-8, None)


def kmeans(vectors: np.ndarray, k: int, seed: int = 0, iterations: int = 40) -> np.ndarray:
    """Spherical k-means with k-means++ seeding, returning a label per row.

    Deterministic for a given seed so a map can be rebuilt and compared. Written
    out here rather than pulled from scikit-learn because the ingest extra that
    provides it is optional and the serving path must not depend on it.
    """
    rng = np.random.default_rng(seed)
    count = len(vectors)
    k = max(1, min(k, count))
    centroids = np.empty((k, vectors.shape[1]), dtype=np.float32)
    centroids[0] = vectors[rng.integers(count)]
    for index in range(1, k):
        distance = 1.0 - (vectors @ centroids[:index].T).max(axis=1)
        distance = np.clip(distance, 0.0, None) ** 2
        total = distance.sum()
        pick = rng.integers(count) if total <= 0 else rng.choice(count, p=distance / total)
        centroids[index] = vectors[pick]
    labels = np.full(count, -1, dtype=np.int64)
    for _ in range(iterations):
        assigned = (vectors @ centroids.T).argmax(axis=1)
        if np.array_equal(assigned, labels):
            break
        labels = assigned
        for index in range(k):
            members = vectors[labels == index]
            if len(members):
                centroids[index] = members.mean(axis=0)
        centroids = normalize(centroids)
    return labels


def _bucket(document: dict) -> str:
    return str(document.get("query_bucket") or document.get("bucket") or "inconclusive")


def _is_primary(document: dict) -> bool:
    """Reviews restate other people's results, so they cannot evidence a region."""
    return document.get("is_review") is not True and document.get("record_kind", "study") == "study"


def label_region(counts: Counter, attempts: int) -> str:
    """Name the kind of absence a region represents, most specific rule first."""
    if attempts < MIN_REGION_ATTEMPTS:
        return "thin"
    nulls = sum(counts[bucket] for bucket in NULL_BUCKETS)
    effects, dark = counts["effect"], counts["unreported"]
    if dark / attempts >= DARK_SHARE:
        return "dark"
    if nulls / attempts >= NULL_SATURATED_SHARE:
        return "null_saturated"
    if min(nulls, effects) / attempts >= CONTESTED_SHARE:
        return "contested"
    return "active"


def _exemplars(documents: list[dict], limit: int = 3) -> list[dict]:
    ranked = sorted(documents, key=lambda doc: doc.get("cited_by_count") or 0, reverse=True)
    return [
        {
            "id": doc.get("id", ""),
            "title": doc.get("title", ""),
            "year": doc.get("year"),
            "bucket": _bucket(doc),
        }
        for doc in ranked[:limit]
    ]


def build_regions(documents: list[dict], regions: int = 24, seed: int = 0) -> dict:
    """Cluster embedded documents and describe each cluster by what happened in it.

    Documents without a stored vector are counted in ``skipped`` rather than
    dropped silently: a region map over a partly unembedded corpus describes the
    embedded part only, and saying so is the difference between a map and a claim.
    """
    embedded = [(doc, vector) for doc in documents if (vector := _vector(doc)) is not None]
    skipped = len(documents) - len(embedded)
    if not embedded:
        return {"version": MAP_VERSION, "regions": [], "documents": 0, "skipped": skipped}
    corpus = [doc for doc, _ in embedded]
    vectors = normalize(np.vstack([vector for _, vector in embedded]))
    labels = kmeans(vectors, regions, seed=seed)
    described = []
    for index in sorted(set(labels.tolist())):
        members = [doc for doc, label in zip(corpus, labels) if label == index]
        centroid = normalize(vectors[labels == index].mean(axis=0, keepdims=True))[0]
        primary = [doc for doc in members if _is_primary(doc)]
        counts = Counter(_bucket(doc) for doc in primary)
        years = [doc["year"] for doc in members if isinstance(doc.get("year"), int)]
        citations = [doc.get("cited_by_count") or 0 for doc in primary]
        described.append(
            {
                "id": int(index),
                "size": len(members),
                "attempts": len(primary),
                "centroid": [float(value) for value in centroid],
                "label": label_region(counts, len(primary)),
                "bucketCounts": dict(counts),
                "medianYear": int(np.median(years)) if years else None,
                "medianCitations": float(np.median(citations)) if citations else None,
                "coherence": float((vectors[labels == index] @ centroid).mean()),
                "exemplars": _exemplars(members),
            }
        )
    return {
        "version": MAP_VERSION,
        "regions": described,
        "documents": len(embedded),
        "skipped": skipped,
    }


def _corpus(documents: list[dict]) -> tuple[list[dict], np.ndarray]:
    embedded = [(doc, vector) for doc in documents if (vector := _vector(doc)) is not None]
    if not embedded:
        return [], np.zeros((0, 0), dtype=np.float32)
    return (
        [doc for doc, _ in embedded],
        normalize(np.vstack([vector for _, vector in embedded])),
    )


def _nearest(vector: np.ndarray, documents: list[dict], vectors: np.ndarray) -> dict:
    similarity = vectors @ vector
    position = int(similarity.argmax())
    document = documents[position]
    return {
        "id": document.get("id", ""),
        "title": document.get("title", ""),
        "year": document.get("year"),
        "bucket": _bucket(document),
        "cosine": float(similarity[position]),
    }


def neighbour_threshold(centroids: np.ndarray) -> float:
    """Centroid cosine above which two regions count as adjacent literatures."""
    pairs = centroids @ centroids.T
    upper = pairs[np.triu_indices(len(centroids), k=1)]
    if not len(upper):
        return NEIGHBOUR_FLOOR
    return max(NEIGHBOUR_FLOOR, float(np.quantile(upper, NEIGHBOUR_QUANTILE)))


def find_gaps(
    documents: list[dict],
    regions: list[dict],
    limit: int = 10,
    neighbour_cosine: float | None = None,
    occupancy: float = GAP_OCCUPANCY,
) -> list[dict]:
    """Bands between neighbouring regions that the indexed corpus barely occupies.

    Occupancy is counted, not measured by distance: a ball of a quarter of the
    centroid separation is placed on each core and on the midpoint, and the band
    is open when it holds almost nothing while both cores are populated. Distance
    alone cannot express this — a midpoint is always near its own parents, so an
    absolute cosine threshold either rejects every gap or accepts every one.

    ``support`` is the thinner parent core: a band between two substantial
    literatures is a combination many people were positioned to try and did not.
    The parent labels carry the warning that matters — an open band between two
    null-saturated regions is unexplored *because the neighbours failed*.
    """
    corpus, vectors = _corpus(documents)
    if not corpus or len(regions) < 2:
        return []
    centroids = normalize(np.vstack([region["centroid"] for region in regions]).astype(np.float32))
    if neighbour_cosine is None:
        neighbour_cosine = neighbour_threshold(centroids)
    gaps = []
    for left in range(len(regions)):
        for right in range(left + 1, len(regions)):
            separation = float(np.clip(centroids[left] @ centroids[right], -1.0, 1.0))
            if separation < neighbour_cosine:
                continue
            midpoint = normalize(((centroids[left] + centroids[right]) / 2)[None, :])[0]
            ball = float(np.cos(np.arccos(separation) * BALL_FRACTION))
            cores = [int((vectors @ centroids[side] >= ball).sum()) for side in (left, right)]
            support = min(cores)
            if not support:
                continue
            band = int((vectors @ midpoint >= ball).sum())
            if band > occupancy * support:
                continue
            parents = [regions[left], regions[right]]
            gaps.append(
                {
                    "regions": [region["id"] for region in parents],
                    "parentLabels": [region["label"] for region in parents],
                    "openness": 1.0 - band / support,
                    "support": support,
                    "band": band,
                    "separation": separation,
                    "nearest": _nearest(midpoint, corpus, vectors),
                    "exemplars": [region["exemplars"][:1] for region in parents],
                    "discouraged": any(
                        region["label"] in {"null_saturated", "dark"} for region in parents
                    ),
                }
            )
    gaps.sort(key=lambda gap: (gap["openness"], gap["support"]), reverse=True)
    return gaps[:limit]


def place(
    vector: list[float] | np.ndarray,
    documents: list[dict],
    regions: list[dict],
    gaps: list[dict] | None = None,
) -> dict:
    """Locate an idea on the map: how redundant it is and what surrounds it.

    ``redundancy`` is the cosine to the single closest indexed paper. It is the
    honest headline: most proposals are a near-duplicate of something already
    published, and the nearest paper is shown so the claim can be checked by
    reading it.
    """
    corpus, vectors = _corpus(documents)
    query = normalize(np.asarray(vector, dtype=np.float32)[None, :])[0]
    if not corpus:
        return {"redundancy": None, "nearest": None, "region": None, "nearestGap": None}
    nearest = _nearest(query, corpus, vectors)
    region = None
    if regions:
        centroids = normalize(
            np.vstack([entry["centroid"] for entry in regions]).astype(np.float32)
        )
        similarity = centroids @ query
        position = int(similarity.argmax())
        region = {**regions[position], "cosine": float(similarity[position])}
        region.pop("centroid", None)
    return {
        "redundancy": nearest["cosine"],
        "nearest": nearest,
        "region": region,
        "nearestGap": _nearest_gap(query, regions, gaps or []),
    }


def _nearest_gap(query: np.ndarray, regions: list[dict], gaps: list[dict]) -> dict | None:
    if not gaps or not regions:
        return None
    by_id = {region["id"]: region for region in regions}
    best, best_cosine = None, -1.0
    for gap in gaps:
        parents = [by_id[identifier] for identifier in gap["regions"] if identifier in by_id]
        if len(parents) != 2:
            continue
        midpoint = normalize(
            np.vstack([parent["centroid"] for parent in parents]).astype(np.float32).mean(
                axis=0, keepdims=True
            )
        )[0]
        cosine = float(midpoint @ query)
        if cosine > best_cosine:
            best, best_cosine = gap, cosine
    return {**best, "cosine": best_cosine} if best else None


def calibrate(
    documents: list[dict],
    cutoff_year: int,
    regions: int = 24,
    seed: int = 0,
) -> dict:
    """Rebuild the map as it would have looked at ``cutoff_year`` and score it since.

    The labels claim something checkable: work published after the cutoff should
    behave differently depending on the label its region carried before it. A
    null-saturated region that keeps producing nulls, and an active region that
    keeps producing effects, is the map earning its labels. This is a calibration
    record over one corpus and one cutoff, not a forecast and not an accuracy
    study: a region can be mislabelled and still look right if later authors
    simply copied the earlier design.
    """
    past = [doc for doc in documents if isinstance(doc.get("year"), int) and doc["year"] < cutoff_year]
    future = [
        doc for doc in documents if isinstance(doc.get("year"), int) and doc["year"] >= cutoff_year
    ]
    built = build_regions(past, regions=regions, seed=seed)
    if not built["regions"] or not future:
        return {
            "version": MAP_VERSION,
            "cutoffYear": cutoff_year,
            "past": len(past),
            "future": 0,
            "labels": [],
        }
    centroids = normalize(
        np.vstack([region["centroid"] for region in built["regions"]]).astype(np.float32)
    )
    later: dict[int, list[dict]] = {region["id"]: [] for region in built["regions"]}
    corpus, vectors = _corpus(future)
    for document, vector in zip(corpus, vectors):
        later[built["regions"][int((centroids @ vector).argmax())]["id"]].append(document)
    rows: dict[str, dict] = {}
    for region in built["regions"]:
        arrivals = [doc for doc in later[region["id"]] if _is_primary(doc)]
        row = rows.setdefault(
            region["label"],
            {"label": region["label"], "regions": 0, "priorAttempts": 0, "laterPapers": 0,
             "laterNull": 0, "laterEffect": 0},
        )
        row["regions"] += 1
        row["priorAttempts"] += region["attempts"]
        row["laterPapers"] += len(arrivals)
        row["laterNull"] += sum(_bucket(doc) in NULL_BUCKETS for doc in arrivals)
        row["laterEffect"] += sum(_bucket(doc) == "effect" for doc in arrivals)
    for row in rows.values():
        row["laterNullShare"] = row["laterNull"] / row["laterPapers"] if row["laterPapers"] else None
    return {
        "version": MAP_VERSION,
        "cutoffYear": cutoff_year,
        "past": len(past),
        "future": len(corpus),
        "labels": sorted(rows.values(), key=lambda row: row["label"]),
    }
