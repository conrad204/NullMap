"""Serves the gap map: sample the index once, describe it, place ideas on it.

The map is a property of the corpus, not of a query, so it is computed from a
deterministic sample and cached until asked to refresh. Placing an idea then
costs one query embedding and two matrix products, which is what makes the
"where does this land" answer instant while still being computed over thousands
of papers rather than the ten a chat model can read.
"""

from __future__ import annotations

import asyncio

from app.config import Settings, settings
from app.gapmap import build_regions, calibrate, find_gaps, place


def _public(region: dict) -> dict:
    """Region without its centroid: a 384-float vector is not UI payload."""
    return {key: value for key, value in region.items() if key != "centroid"}


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
            self._built = {
                "documents": documents,
                "regions": built["regions"],
                "gaps": gaps,
                "coverage": {
                    "sampled": built["documents"],
                    "corpus": sample["corpus"],
                    "regions": len(built["regions"]),
                },
                "version": built["version"],
            }
            return self._built

    async def assess(
        self, idea: str | None = None, cutoff_year: int | None = None, refresh: bool = False
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
        if idea:
            vector = await self.embed(idea)
            if vector is None:
                warnings.append("Embeddings are disabled, so the idea could not be placed.")
            else:
                placement = await asyncio.to_thread(
                    place, vector, built["documents"], built["regions"], built["gaps"]
                )
                placement["region"] = (
                    _public(placement["region"]) if placement["region"] else None
                )
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
            "placement": placement,
            "calibration": calibration,
            "warnings": warnings,
        }
