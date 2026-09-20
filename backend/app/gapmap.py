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
# Rows per chunk when multiplying the corpus against the centroids. Bounds the
# intermediate to a few tens of megabytes whatever the corpus size.
ASSIGN_CHUNK = 50_000
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
# Below this share of readable outcomes a region says nothing about the science,
# only about what the index could read.
READABLE_SHARE = 0.1

REGION_LABELS = ("active", "contested", "null_saturated", "dark", "unread", "thin")
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


def seed_centroids(vectors: np.ndarray, k: int, seed: int = 0) -> np.ndarray:
    """k-means++ seeding over a cosine space, deterministic for a given seed."""
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
    return centroids


def assign(vectors: np.ndarray, centroids: np.ndarray, chunk: int = ASSIGN_CHUNK) -> np.ndarray:
    """Nearest centroid per row, in chunks so a corpus-sized matrix is never built.

    ``vectors @ centroids.T`` over two million rows and eighty centroids is a
    640 MB intermediate; the chunked loop is the same arithmetic in 16 MB pieces.
    """
    if not len(vectors):
        return np.zeros(0, dtype=np.int64)
    labels = np.empty(len(vectors), dtype=np.int64)
    for start in range(0, len(vectors), chunk):
        stop = start + chunk
        labels[start:stop] = (vectors[start:stop] @ centroids.T).argmax(axis=1)
    return labels


def _recentre(vectors: np.ndarray, labels: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    """Move each centroid onto the mean direction of the rows assigned to it.

    Empty clusters keep their previous position rather than being reseeded, so a
    warm-started run cannot make the map jump between successive iterations.
    """
    moved = centroids.copy()
    order = np.argsort(labels, kind="stable")
    bounds = np.searchsorted(labels[order], np.arange(len(centroids) + 1))
    for index in range(len(centroids)):
        members = order[bounds[index] : bounds[index + 1]]
        if len(members):
            moved[index] = vectors[members].mean(axis=0)
    return normalize(moved)


def kmeans_steps(
    vectors: np.ndarray,
    k: int,
    seed: int = 0,
    iterations: int = 40,
    centroids: np.ndarray | None = None,
):
    """Spherical k-means, yielding ``(centroids, labels)`` after every iteration.

    Yielding the intermediate states is what lets a caller show the map settling
    instead of a spinner: each yielded pair is a real assignment of the corpus,
    not an interpolation. Deterministic for a given seed and starting position,
    and stops early once the assignment stops changing.
    """
    if not len(vectors):
        return
    if centroids is None:
        centroids = seed_centroids(vectors, k, seed)
    centroids = normalize(np.asarray(centroids, dtype=np.float32))
    labels = np.full(len(vectors), -1, dtype=np.int64)
    for _ in range(max(1, iterations)):
        assigned = assign(vectors, centroids)
        settled = np.array_equal(assigned, labels)
        labels = assigned
        centroids = _recentre(vectors, labels, centroids)
        yield centroids, labels
        if settled:
            return


def kmeans(vectors: np.ndarray, k: int, seed: int = 0, iterations: int = 40) -> np.ndarray:
    """Spherical k-means with k-means++ seeding, returning a label per row.

    Deterministic for a given seed so a map can be rebuilt and compared. Written
    out here rather than pulled from scikit-learn because the ingest extra that
    provides it is optional and the serving path must not depend on it.
    """
    labels = np.zeros(len(vectors), dtype=np.int64)
    for _, labels in kmeans_steps(vectors, k, seed=seed, iterations=iterations):
        pass
    return labels


class RunningClusters:
    """Mini-batch spherical k-means: centroids that exist before the scan ends.

    The full corpus cannot be clustered until it has all arrived, but a map that
    only appears at the end cannot be watched. Each batch is assigned to the
    current centroids and each centroid is moved towards the mean of what it
    just received, weighted by how much it has seen — the standard mini-batch
    update. These centroids are provisional and are replaced by full-corpus
    k-means once the scan finishes; nothing is reported as final until then.
    """

    def __init__(self, k: int, seed: int = 0):
        self.k = max(1, k)
        self.seed = seed
        self.centroids: np.ndarray | None = None
        self.counts: np.ndarray | None = None

    def update(self, vectors: np.ndarray) -> np.ndarray | None:
        if not len(vectors):
            return self.centroids
        if self.centroids is None:
            if len(vectors) < self.k:
                return None  # too few rows to seed k distinct centroids honestly
            self.centroids = normalize(seed_centroids(vectors, self.k, self.seed))
            self.counts = np.zeros(len(self.centroids), dtype=np.float64)
        labels = assign(vectors, self.centroids)
        sums = np.zeros_like(self.centroids, dtype=np.float32)
        np.add.at(sums, labels, vectors)
        counts = np.bincount(labels, minlength=len(self.centroids)).astype(np.float64)
        assert self.counts is not None
        seen = self.counts + counts
        rate = np.divide(counts, seen, out=np.zeros_like(seen), where=seen > 0)[:, None]
        self.centroids = normalize(
            self.centroids * (1 - rate)
            + np.divide(sums, np.clip(counts, 1, None)[:, None]) * rate
        )
        self.counts = seen
        return self.centroids


def fit_projection(vectors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A deterministic 2-D basis for drawing the map: PCA over centered vectors.

    Returns ``(mean, basis)`` so centroids and idea vectors can be projected
    through the same fit via :func:`project_with`. SVD is deterministic for a
    given input, but the sign of each component is arbitrary, so every axis is
    flipped until its largest-magnitude weight is positive — without that the
    same map could render mirrored between builds.
    """
    mean = vectors.mean(axis=0, keepdims=True).astype(np.float32)
    _, _, vt = np.linalg.svd(vectors - mean, full_matrices=False)
    basis = np.zeros((2, vectors.shape[1]), dtype=np.float32)
    rows = min(2, len(vt))  # a single document yields only one axis
    basis[:rows] = vt[:rows]
    for component in basis:
        if component[np.argmax(np.abs(component))] < 0:
            component *= -1
    return mean, basis


def project_with(mean: np.ndarray, basis: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    """Project ``vectors`` through a basis fitted by :func:`fit_projection`."""
    return (np.atleast_2d(np.asarray(vectors, dtype=np.float32)) - mean) @ basis.T


def project(vectors: np.ndarray) -> np.ndarray:
    """2-D coordinates for ``vectors`` under a basis fitted on themselves."""
    if not len(vectors):
        return np.zeros((0, 2), dtype=np.float32)
    mean, basis = fit_projection(vectors)
    return project_with(mean, basis, vectors)


def combine(positive: list[np.ndarray], negative: list[np.ndarray]) -> np.ndarray | None:
    """Embedding arithmetic: a starting point plus added contexts, minus removed ones.

    ``start - context + context`` is the document-level analogue of word2vec
    analogy: the result points at the neighbourhood the combination implies,
    and the corpus decides whether anything actually sits there. The sum is
    renormalized because only direction carries meaning in a cosine space.
    ``None`` when the terms cancel out — a zero-length vector has no direction,
    and placing it would land somewhere meaningless.
    """
    total = np.zeros_like(np.asarray(positive[0], dtype=np.float64))
    for vector in positive:
        total += np.asarray(vector, dtype=np.float64)
    for vector in negative:
        total -= np.asarray(vector, dtype=np.float64)
    norm = np.linalg.norm(total)
    if norm < 1e-6:
        return None
    return (total / norm).astype(np.float32)


def _bucket(document: dict) -> str:
    return str(document.get("query_bucket") or document.get("bucket") or "inconclusive")


def _is_primary(document: dict) -> bool:
    """Reviews restate other people's results, so they cannot evidence a region."""
    return document.get("is_review") is not True and document.get("record_kind", "study") == "study"


def label_region(counts: Counter, attempts: int) -> str:
    """Name the kind of absence a region represents, most specific rule first.

    ``unread`` is the label for a region whose documents carry no readable
    outcome at all — registrations, retractions and unreadable reports. It exists
    because the fallback must not be ``active``: measured against the live index,
    every region held only ``inconclusive``/``failed``/``unreported`` documents,
    and calling those "active" would assert a literature that was never read.
    """
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
    if (nulls + effects) / attempts < READABLE_SHARE:
        return "unread"
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


def group(labels: np.ndarray, clusters: int) -> list[np.ndarray]:
    """Row indices per cluster. One sort rather than a scan per cluster: the
    obvious ``labels == index`` loop is O(rows × clusters), which is hours of
    Python over a corpus of millions."""
    order = np.argsort(labels, kind="stable")
    bounds = np.searchsorted(labels[order], np.arange(clusters + 1))
    return [order[bounds[index] : bounds[index + 1]] for index in range(clusters)]


def describe_regions(
    documents: list[dict], vectors: np.ndarray, labels: np.ndarray, clusters: int
) -> list[dict]:
    """Describe each occupied cluster by what happened in the documents it holds.

    Cluster indices are the region ids, and an empty cluster yields no region, so
    a region keeps its identity between two runs that share centroids — which is
    what lets a partially built map be redrawn rather than replaced.
    """
    described = []
    for index, members in enumerate(group(labels, clusters)):
        if not len(members):
            continue
        rows = vectors[members]
        centroid = normalize(rows.mean(axis=0, keepdims=True))[0]
        held = [documents[row] for row in members.tolist()]
        primary = [doc for doc in held if _is_primary(doc)]
        counts = Counter(_bucket(doc) for doc in primary)
        years = [doc["year"] for doc in held if isinstance(doc.get("year"), int)]
        citations = [doc.get("cited_by_count") or 0 for doc in primary]
        described.append(
            {
                "id": int(index),
                "size": len(held),
                "attempts": len(primary),
                "centroid": [float(value) for value in centroid],
                "label": label_region(counts, len(primary)),
                "bucketCounts": dict(counts),
                "medianYear": int(np.median(years)) if years else None,
                "medianCitations": float(np.median(citations)) if citations else None,
                "coherence": float((rows @ centroid).mean()),
                "exemplars": _exemplars(held),
            }
        )
    return described


def build_regions(
    documents: list[dict],
    regions: int = 24,
    seed: int = 0,
    vectors: np.ndarray | None = None,
) -> dict:
    """Cluster embedded documents and describe each cluster by what happened in it.

    Documents without a stored vector are counted in ``skipped`` rather than
    dropped silently: a region map over a partly unembedded corpus describes the
    embedded part only, and saying so is the difference between a map and a claim.

    ``vectors`` lets a caller that already holds the corpus as one normalized
    matrix — the streaming build does — skip rebuilding it from the documents,
    which would cost a second copy of several gigabytes.
    """
    corpus, matrix = _corpus(documents, vectors)
    skipped = len(documents) - len(corpus)
    if not len(matrix):
        return {"version": MAP_VERSION, "regions": [], "documents": 0, "skipped": skipped}
    clusters = max(1, min(regions, len(matrix)))
    labels = kmeans(matrix, clusters, seed=seed)
    return {
        "version": MAP_VERSION,
        "regions": describe_regions(corpus, matrix, labels, clusters),
        "documents": len(corpus),
        "skipped": skipped,
    }


def _corpus(
    documents: list[dict], vectors: np.ndarray | None = None
) -> tuple[list[dict], np.ndarray]:
    """Documents paired with their unit vectors, from the dicts or from a matrix."""
    if vectors is not None:
        return documents, vectors
    embedded = [(doc, vector) for doc in documents if (vector := _vector(doc)) is not None]
    if not embedded:
        return [], np.zeros((0, 0), dtype=np.float32)
    return (
        [doc for doc, _ in embedded],
        normalize(np.vstack([vector for _, vector in embedded])),
    )


def nearest_neighbors(
    vector: np.ndarray, documents: list[dict], vectors: np.ndarray, limit: int = 1
) -> list[dict]:
    """The ``limit`` closest sampled papers to a point, closest first.

    Callers show these so a bare cosine never has to stand alone: the list of
    real papers near a point is the legible form of "how occupied is this spot".
    """
    similarity = vectors @ vector
    limit = max(1, min(limit, len(similarity)))
    # argpartition first: a full sort of a corpus-sized array to read five rows
    # off the top is seconds of wasted work on the live index.
    top = np.argpartition(-similarity, limit - 1)[:limit]
    order = top[np.argsort(-similarity[top])]
    return [
        {
            "id": documents[index].get("id", ""),
            "title": documents[index].get("title", ""),
            "year": documents[index].get("year"),
            "bucket": _bucket(documents[index]),
            "cosine": float(similarity[index]),
        }
        for index in order
    ]


def _nearest(vector: np.ndarray, documents: list[dict], vectors: np.ndarray) -> dict:
    return nearest_neighbors(vector, documents, vectors, limit=1)[0]


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
    vectors: np.ndarray | None = None,
) -> list[dict]:
    """Bands between neighbouring regions that the indexed corpus barely occupies.

    Occupancy is counted, not measured by distance: the two regions' own members
    are given to whichever of the two centroids and their midpoint they are
    closest to, and the band is open when the midpoint keeps almost nothing while
    both parents keep plenty. Pairs whose midpoint is better described by some
    third region are skipped — that band is occupied, by a whole literature.

    Distance alone cannot express any of this: a midpoint is always near its own
    parents, so an absolute cosine threshold either rejects every gap or accepts
    every one, and a fixed-radius ball around each point holds nothing at all in
    a real high-dimensional corpus (measured against the live index: every such
    ball was empty, so no pair could ever have been a gap).

    ``support`` is the thinner parent core: a band between two substantial
    literatures is a combination many people were positioned to try and did not.
    The parent labels carry the warning that matters — an open band between two
    null-saturated regions is unexplored *because the neighbours failed*.
    """
    corpus, vectors = _corpus(documents, vectors)
    if not corpus or len(regions) < 2:
        return []
    centroids = normalize(np.vstack([region["centroid"] for region in regions]).astype(np.float32))
    if neighbour_cosine is None:
        neighbour_cosine = neighbour_threshold(centroids)
    assigned = assign(vectors, centroids)
    gaps = []
    for left in range(len(regions)):
        for right in range(left + 1, len(regions)):
            separation = float(np.clip(centroids[left] @ centroids[right], -1.0, 1.0))
            if separation < neighbour_cosine:
                continue
            midpoint = normalize(((centroids[left] + centroids[right]) / 2)[None, :])[0]
            to_midpoint = centroids @ midpoint
            others = np.delete(to_midpoint, [left, right])
            if len(others) and others.max() >= min(to_midpoint[left], to_midpoint[right]):
                continue  # a third literature already sits in the band
            members = vectors[(assigned == left) | (assigned == right)]
            owner = (
                members @ np.vstack([centroids[left], centroids[right], midpoint]).T
            ).argmax(axis=1)
            support = min(int((owner == 0).sum()), int((owner == 1).sum()))
            if not support:
                continue
            band = int((owner == 2).sum())
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
    nearest_limit: int = 5,
    vectors: np.ndarray | None = None,
) -> dict:
    """Locate an idea on the map: how redundant it is and what surrounds it.

    ``redundancy`` is the cosine to the single closest indexed paper. It is the
    honest headline: most proposals are a near-duplicate of something already
    published, and the nearest papers are shown so the claim can be checked by
    reading them.
    """
    corpus, vectors = _corpus(documents, vectors)
    query = normalize(np.asarray(vector, dtype=np.float32)[None, :])[0]
    if not corpus:
        return {
            "redundancy": None,
            "nearest": None,
            "neighbors": [],
            "region": None,
            "nearestGap": None,
        }
    neighbors = nearest_neighbors(query, corpus, vectors, limit=nearest_limit)
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
        "redundancy": neighbors[0]["cosine"],
        "nearest": neighbors[0],
        "neighbors": neighbors[1:],
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
    vectors: np.ndarray | None = None,
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
    corpus, matrix = _corpus(documents, vectors)
    dated = [
        index for index, doc in enumerate(corpus) if isinstance(doc.get("year"), int)
    ]
    before = np.array(
        [index for index in dated if corpus[index]["year"] < cutoff_year], dtype=np.int64
    )
    after = np.array(
        [index for index in dated if corpus[index]["year"] >= cutoff_year], dtype=np.int64
    )
    past = [corpus[index] for index in before.tolist()]
    future = [corpus[index] for index in after.tolist()]
    built = build_regions(
        past, regions=regions, seed=seed, vectors=matrix[before] if len(before) else None
    )
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
    corpus, vectors = _corpus(future, matrix[after] if len(after) else None)
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
