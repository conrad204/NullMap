"""Concept-vector search: nearest papers to added concepts minus removed ones.

The document-level analogue of ``king − man + woman``. Each concept is embedded
once, the vectors are combined into one direction, and the index is searched by
nearest neighbor to it. Every match carries its cosine to each supplied concept
so the answer stays legible: what pulled a paper in, and what should have pushed
it away. Nothing here scores novelty or likelihood — only cosines and counts.
"""

from __future__ import annotations

import asyncio

import numpy as np

from app.config import Settings, settings
from app.embeddings import QueryEmbedder
from app.gapmap import _bucket, combine
from app.models import ConceptSearchRequest

VERSION = "concepts-v1"


def aim(
    query: list[float], positive: list[list[float]], negative: list[list[float]]
) -> list[float] | None:
    """Point a question's own vector at the concepts kept and away from those pushed off.

    The question is simply one more positive term, so a tagged search is the
    same search: retrieval still matches the question's keywords, and the tags
    only move the vector half of the ranking. ``None`` when the terms cancel the
    question out, which has no direction to search in.
    """
    combined = combine(
        [np.asarray(query, dtype=np.float32), *(np.asarray(v, dtype=np.float32) for v in positive)],
        [np.asarray(v, dtype=np.float32) for v in negative],
    )
    return None if combined is None else [float(value) for value in combined]


class ConceptError(Exception):
    """A concept search that cannot honestly return matches."""

    status = 503


class EmbeddingsUnavailable(ConceptError):
    status = 503


class ConceptsCancelOut(ConceptError):
    status = 422


def _unit(values) -> np.ndarray | None:
    vector = np.asarray(values, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return None if norm < 1e-6 else vector / norm


def _source(document: dict) -> str:
    source = document.get("source") or "openalex"
    return "clinicaltrials" if source == "ctgov" else str(source)


class ConceptVectorService:
    def __init__(self, repository, config: Settings = settings):
        self.repo = repository
        self.config = config
        self.embedder = QueryEmbedder(config)

    async def embed(self, text: str) -> list[float] | None:
        return await self.embedder.embed(text)

    async def resolve(
        self, positive: list[str], negative: list[str]
    ) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Embed each distinct concept once and combine them into one direction.

        The per-term vectors come back with the combination so a caller can
        explain a result against each concept, or reuse the direction for a
        query of its own without paying for the embeddings twice.
        """
        terms = list(dict.fromkeys([*positive, *negative]))
        try:
            vectors = await asyncio.gather(*(self.embed(term) for term in terms))
        except Exception as exc:
            raise EmbeddingsUnavailable(
                f"The embedding model could not be loaded ({type(exc).__name__}), "
                "so the concepts could not be turned into vectors."
            ) from exc
        if any(vector is None for vector in vectors):
            raise EmbeddingsUnavailable(
                "Embeddings are disabled, so the concepts could not be turned into vectors."
            )
        by_term = {
            term: np.asarray(vector, dtype=np.float32) for term, vector in zip(terms, vectors)
        }
        combined = combine(
            [by_term[term] for term in positive], [by_term[term] for term in negative]
        )
        if combined is None:
            raise ConceptsCancelOut(
                "The concepts cancel each other out, so the search has no direction."
            )
        return combined, by_term

    async def search(self, request: ConceptSearchRequest) -> dict:
        combined, by_term = await self.resolve(request.positive, request.negative)
        filters = (
            request.filters.model_dump()
            if request.filters is not None and request.filters.active
            else None
        )
        hits = await self.repo.knn_studies(
            [float(value) for value in combined], limit=request.limit, filters=filters
        )
        signed = [(term, "positive") for term in request.positive]
        signed += [(term, "negative") for term in request.negative]
        warnings: list[str] = []
        matches = []
        for hit in hits:
            vector = _unit(hit.get("embedding") or [])
            if vector is None and not warnings:
                warnings.append(
                    "Some matches were stored without a vector, so their per-concept "
                    "cosines are missing."
                )
            matches.append(
                {
                    "id": str(hit.get("id", "")),
                    "title": hit.get("title", ""),
                    "year": hit.get("year") if isinstance(hit.get("year"), int) else None,
                    "url": hit.get("url") or "",
                    "source": _source(hit),
                    "verdict": _bucket(hit),
                    "citations": hit.get("cited_by_count") or 0,
                    "cosine": round(float(hit.get("cosine", 0.0)), 4),
                    "concepts": [
                        {
                            "text": term,
                            "sign": sign,
                            "cosine": round(float(vector @ by_term[term]), 4),
                        }
                        for term, sign in signed
                    ]
                    if vector is not None
                    else [],
                }
            )
        return {
            "version": VERSION,
            "concepts": [{"text": term, "sign": sign} for term, sign in signed],
            "matches": matches,
            "warnings": warnings,
        }
