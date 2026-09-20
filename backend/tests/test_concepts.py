import asyncio
import math

import pytest

from app.concepts import ConceptsCancelOut, ConceptVectorService, EmbeddingsUnavailable
from app.config import Settings
from app.models import ConceptSearchRequest
from tests.test_gapmap import unit

TERMS = {
    "kidney disease": unit(0.0),
    "diabetes": unit(1.2),
    "dialysis": unit(0.3),
}

DOCUMENTS = [
    {"id": "W1", "embedding": unit(0.0), "title": "Kidney cohort", "year": 2019, "source": "ctgov"},
    {"id": "W2", "embedding": unit(1.2), "title": "Diabetes cohort", "year": 2021},
    {"id": "W3", "embedding": unit(0.6), "title": "Both", "year": 2020, "query_bucket": "effect"},
]


class FakeRepository:
    """Ranks the documents the way Elasticsearch would, and reports ES scores."""

    def __init__(self, documents: list[dict] | None = None):
        self.documents = DOCUMENTS if documents is None else documents
        self.filters: dict | None = None

    async def knn_studies(self, vector, limit: int, filters: dict | None = None) -> list[dict]:
        self.filters = filters
        scored = [
            (sum(a * b for a, b in zip(vector, doc["embedding"])), doc) for doc in self.documents
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            dict(doc, cosine=2 * ((1 + cosine) / 2) - 1) for cosine, doc in scored[:limit]
        ]


def service(repo: FakeRepository | None = None, **overrides) -> ConceptVectorService:
    built = ConceptVectorService(repo or FakeRepository(), config=Settings(**overrides))
    built.embed = lambda text: _resolved(TERMS[text])
    return built


def request(positive, negative=(), **fields) -> ConceptSearchRequest:
    return ConceptSearchRequest(positive=list(positive), negative=list(negative), **fields)


def test_positive_concepts_rank_their_own_neighbourhood_first():
    result = asyncio.run(service().search(request(["kidney disease"])))
    assert result["version"] == "concepts-v1"
    assert result["concepts"] == [{"text": "kidney disease", "sign": "positive"}]
    assert [match["id"] for match in result["matches"]] == ["W1", "W3", "W2"]
    assert result["matches"][0]["cosine"] == pytest.approx(1.0, abs=1e-3)
    assert result["warnings"] == []


def test_a_negative_concept_pushes_its_own_papers_down():
    positives = asyncio.run(service().search(request(["kidney disease", "diabetes"])))
    subtracted = asyncio.run(service().search(request(["kidney disease"], ["diabetes"])))
    assert [match["id"] for match in positives["matches"]][0] == "W3"
    assert [match["id"] for match in subtracted["matches"]] == ["W1", "W3", "W2"]
    assert subtracted["matches"][-1]["id"] == "W2"


def test_every_match_carries_a_cosine_per_concept_in_the_order_supplied():
    result = asyncio.run(
        service().search(request(["kidney disease", "dialysis"], ["diabetes"]))
    )
    concepts = result["matches"][0]["concepts"]
    assert [(c["text"], c["sign"]) for c in concepts] == [
        ("kidney disease", "positive"),
        ("dialysis", "positive"),
        ("diabetes", "negative"),
    ]
    match = next(m for m in result["matches"] if m["id"] == "W2")
    diabetes = next(c for c in match["concepts"] if c["text"] == "diabetes")
    assert diabetes["cosine"] == pytest.approx(1.0, abs=1e-3)


def test_the_elasticsearch_score_is_reported_as_a_cosine():
    """A document at 90 degrees scores 0.5 in ES; the response must say 0, not 0.5."""
    repo = FakeRepository([{"id": "W9", "embedding": unit(math.pi / 2), "title": "Orthogonal"}])
    result = asyncio.run(service(repo).search(request(["kidney disease"])))
    assert result["matches"][0]["cosine"] == pytest.approx(0.0, abs=1e-3)


def test_match_fields_follow_the_stored_bucket_and_source_mapping():
    result = asyncio.run(service().search(request(["kidney disease"])))
    by_id = {match["id"]: match for match in result["matches"]}
    assert by_id["W1"]["source"] == "clinicaltrials" and by_id["W2"]["source"] == "openalex"
    assert by_id["W3"]["verdict"] == "effect" and by_id["W1"]["verdict"] == "inconclusive"
    assert by_id["W1"]["citations"] == 0 and by_id["W1"]["year"] == 2019


def test_concepts_that_cancel_out_leave_the_search_without_a_direction():
    with pytest.raises(ConceptsCancelOut):
        asyncio.run(service().search(request(["kidney disease"], ["kidney disease"])))


def test_disabled_embeddings_stop_the_search_instead_of_guessing():
    built = ConceptVectorService(FakeRepository(), config=Settings(embeddings_enabled=False))
    with pytest.raises(EmbeddingsUnavailable):
        asyncio.run(built.search(request(["kidney disease"])))


def test_resolve_embeds_each_distinct_term_once_and_returns_the_vectors():
    built = service()
    calls: list[str] = []

    def embed(text):
        calls.append(text)
        return _resolved(TERMS[text])

    built.embed = embed
    combined, vectors = asyncio.run(
        built.resolve(["kidney disease", "diabetes"], ["kidney disease"])
    )
    assert calls == ["kidney disease", "diabetes"]
    assert set(vectors) == {"kidney disease", "diabetes"}
    assert float((combined**2).sum()) == pytest.approx(1.0, abs=1e-5)


def test_only_an_active_filter_set_reaches_the_index():
    repo = FakeRepository()
    asyncio.run(service(repo).search(request(["kidney disease"], filters={})))
    assert repo.filters is None
    asyncio.run(service(repo).search(request(["kidney disease"], filters={"yearFrom": 2015})))
    assert repo.filters["yearFrom"] == 2015


def _resolved(value):
    future: asyncio.Future = asyncio.Future()
    future.set_result(value)
    return future
