"""Serves the gap map: sample the index once, describe it, place ideas on it.

The map is a property of the corpus, not of a query, so it is computed from a
deterministic sample and cached until asked to refresh. Placing an idea then
costs one query embedding and two matrix products, which is what makes the
"where does this land" answer instant while still being computed over thousands
of papers rather than the ten a chat model can read.
"""

from __future__ import annotations

import asyncio

import numpy as np

from app.config import Settings, settings
from app.gapmap import (
    _bucket,
    _corpus,
    build_regions,
    calibrate,
    combine,
    find_gaps,
    fit_projection,
    normalize,
    place,
    project_with,
)
from app.models import MapArithmetic


def _public(region: dict) -> dict:
    """Region without its centroid: a 384-float vector is not UI payload."""
    return {key: value for key, value in region.items() if key != "centroid"}


def _layout(documents: list[dict], regions: list[dict]) -> dict | None:
    """2-D positions for the sampled documents and each region centroid.

    Membership is recomputed rather than returned by build_regions — k-means
    assignment is argmax over centroid cosines, so this is exact. The fitted
    basis is kept with the map so a placed idea lands in the same plane.
    """
    corpus, vectors = _corpus(documents)
    if not len(vectors) or not regions:
        return None
    mean, basis = fit_projection(vectors)
    coords = project_with(mean, basis, vectors)
    centroids = np.vstack([region["centroid"] for region in regions]).astype(np.float32)
    assigned = (vectors @ centroids.T).argmax(axis=1)
    for region, (x, y) in zip(regions, project_with(mean, basis, centroids)):
        region["x"], region["y"] = float(x), float(y)
    points = []
    for index, doc in enumerate(corpus):
        title = str(doc.get("title") or "")
        points.append(
            {
                "id": str(doc.get("id", "")),
                "x": float(coords[index, 0]),
                "y": float(coords[index, 1]),
                "region": regions[int(assigned[index])]["id"],
                "bucket": _bucket(doc),
                "title": title if len(title) <= 120 else f"{title[:119]}…",
                "year": doc.get("year") if isinstance(doc.get("year"), int) else None,
            }
        )
    return {"points": points, "mean": mean, "basis": basis}


class GapMapService:
    def __init__(self, repository, config: Settings = settings):
        self.repo = repository
        self.config = config
        self.embedder = None
        self._built: dict | None = None
        self._lock = asyncio.Lock()

    async def embed(self, text: str) -> list[float] | None:
        if not self.config.embeddings_enabled:
            return None
        if self.embedder is None:
            from app.embeddings import get_embedder

            self.embedder = get_embedder(self.config.embedding_model, self.config.embedding_device)
        return await asyncio.to_thread(self.embedder.embed_query, text)

    async def build(self, refresh: bool = False) -> dict:
        async with self._lock:
            if self._built is not None and not refresh:
                return self._built
            sample = await self.repo.sample_embedded(
                limit=self.config.gapmap_sample, seed=self.config.gapmap_seed
            )
            documents = sample["documents"]
            built = await asyncio.to_thread(
                build_regions,
                documents,
                self.config.gapmap_regions,
                self.config.gapmap_seed,
            )
            gaps = await asyncio.to_thread(find_gaps, documents, built["regions"])
            layout = await asyncio.to_thread(_layout, documents, built["regions"])
            self._built = {
                "documents": documents,
                "regions": built["regions"],
                "gaps": gaps,
                "points": layout["points"] if layout else [],
                "basis": (layout["mean"], layout["basis"]) if layout else None,
                "coverage": {
                    "sampled": built["documents"],
                    "corpus": sample["corpus"],
                    "regions": len(built["regions"]),
                },
                "version": built["version"],
            }
            return self._built

    def _decorate(
        self, placement: dict, vector: np.ndarray | list[float], built: dict
    ) -> dict:
        """Attach the same-plane 2-D point and strip the internal centroid."""
        placement["region"] = _public(placement["region"]) if placement["region"] else None
        if built["basis"] is not None:
            mean, basis = built["basis"]
            coords = project_with(
                mean, basis, normalize(np.asarray(vector, dtype=np.float32)[None, :])
            )
            placement["point"] = {"x": float(coords[0, 0]), "y": float(coords[0, 1])}
        return placement

    async def _place_expression(
        self, expression: MapArithmetic, built: dict, warnings: list[str]
    ) -> dict | None:
        """Place ``start − remove + add``: embed each phrase, combine, locate.

        The combined vector is treated like any other point on the map — what
        comes back are the real papers nearest it, so the honesty of the answer
        does not depend on how cleanly the analogy worked.
        """
        terms = [expression.start, *expression.remove, *expression.add]
        vectors = await asyncio.gather(*(self.embed(term) for term in terms))
        if any(vector is None for vector in vectors):
            warnings.append("Embeddings are disabled, so the expression could not be placed.")
            return None
        n_remove = len(expression.remove)
        combined = combine(
            [vectors[0], *vectors[1 + n_remove :]], vectors[1 : 1 + n_remove]
        )
        if combined is None:
            warnings.append(
                "The expression cancels itself out; the combined vector has no direction."
            )
            return None
        placement = await asyncio.to_thread(
            place, combined, built["documents"], built["regions"], built["gaps"]
        )
        placement["expression"] = {
            "start": expression.start,
            "remove": expression.remove,
            "add": expression.add,
        }
        return self._decorate(placement, combined, built)

    async def assess(
        self,
        idea: str | None = None,
        arithmetic: MapArithmetic | None = None,
        cutoff_year: int | None = None,
        refresh: bool = False,
    ) -> dict:
        built = await self.build(refresh=refresh)
        warnings: list[str] = []
        coverage = built["coverage"]
        if coverage["corpus"] > coverage["sampled"]:
            warnings.append(
                f"The map describes a random sample of {coverage['sampled']} of "
                f"{coverage['corpus']} embedded studies, not the whole index."
            )
        placement = None
        if arithmetic is not None:
            placement = await self._place_expression(arithmetic, built, warnings)
        elif idea:
            vector = await self.embed(idea)
            if vector is None:
                warnings.append("Embeddings are disabled, so the idea could not be placed.")
            else:
                placement = await asyncio.to_thread(
                    place, vector, built["documents"], built["regions"], built["gaps"]
                )
                placement = self._decorate(placement, vector, built)
        calibration = None
        if cutoff_year is not None:
            calibration = await asyncio.to_thread(
                calibrate,
                built["documents"],
                cutoff_year,
                self.config.gapmap_regions,
                self.config.gapmap_seed,
            )
        return {
            "version": built["version"],
            "coverage": coverage,
            "regions": [_public(region) for region in built["regions"]],
            "gaps": built["gaps"],
            "points": built["points"],
            "placement": placement,
            "calibration": calibration,
            "warnings": warnings,
        }
