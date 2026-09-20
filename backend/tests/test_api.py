import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    class Repo:
        async def health(self):
            return {"connected": True, "studies": 12, "index": "test"}

    class Pipeline:
        async def search(self, request, progress=None):
            if progress:
                await progress({"stage": "searching", "message": "Searching"})
            return {"idea": request.idea, "papers": []}

    app.state.repository = Repo()
    app.state.pipeline = Pipeline()
    return TestClient(app)


@pytest.mark.parametrize(
    "body",
    [
        {"idea": "short"},
        {"idea": "A valid clinical question?", "sesoi": 0},
        {"idea": "A valid clinical question?", "plannedN": 2},
        {"idea": "A valid clinical question?", "alpha": 1},
        {"idea": "A valid clinical question?", "effectType": "nonsense"},
        {"idea": "A valid clinical question?", "unexpected": True},
        {"idea": "A valid clinical question?", "filters": {"yearFrom": 2020, "yearTo": 2010}},
        {"idea": "A valid clinical question?", "filters": {"minCitations": 5, "maxCitations": 1}},
        {"idea": "A valid clinical question?", "filters": {"minCitations": -1}},
        {"idea": "A valid clinical question?", "filters": {"citedBy": 5}},
    ],
)
def test_invalid_plan_rejected(client, body):
    assert client.post("/search", json=body).status_code == 422


def test_streamed_progress_and_final_event(client):
    response = client.post("/search/stream", json={"idea": "Does vitamin D reduce depression?"})
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: progress\n" in response.text and "event: result\n" in response.text


def test_streamed_errors_do_not_expose_provider_details(client):
    async def fail(*args):
        raise RuntimeError("API_KEY=secret")

    app.state.pipeline = SimpleNamespace(search=fail)
    response = client.post("/search/stream", json={"idea": "Does vitamin D reduce depression?"})
    assert "event: error" in response.text and "secret" not in response.text


def test_extraction_cache_serializes_concurrent_requests():
    from app.config import Settings
    from app.llm import Usage
    from app.pipeline import SearchPipeline

    async def run():
        doc = {"id": "work1", "source": "openalex", "abstract": "A source abstract."}

        class Repo:
            async def get(self, key):
                return dict(doc)

            async def cache_extraction(self, key, extraction):
                doc.update(extraction)

        class LLM:
            calls = 0

            async def extract(self, study, usage):
                self.calls += 1
                await asyncio.sleep(0.01)
                return {"extracted_at": "2026-09-19T00:00:00Z", "extraction_version": "v1"}

        llm = LLM()
        pipeline = SearchPipeline(
            Repo(),
            llm,
            Settings(_env_file=None, openai_api_key="test", extraction_cache_version="v1"),
        )
        usage = Usage()
        await asyncio.gather(
            pipeline.extract_one(doc, usage, []), pipeline.extract_one(doc, usage, [])
        )
        assert llm.calls == 1 and usage.extraction_cache_hits == 1

    asyncio.run(run())


def _map_service():
    from app.config import Settings
    from app.gapmap_service import GapMapService

    class Repo:
        def _documents(self):
            from tests.test_gapmap import cluster

            return cluster("null", 0.0, "reported_null", 8) + cluster("effect", 0.8, "effect", 8)

        async def count_embedded(self):
            return len(self._documents())

        async def scan_embedded(self, batch_size=2000, slices=8, limit=0):
            for document in self._documents():
                yield [document]

    return GapMapService(
        Repo(), config=Settings(_env_file=None, gapmap_regions=2, gapmap_seed=2)
    )


def test_map_route_returns_the_service_payload(client):
    app.state.gapmap = _map_service()
    body = client.post("/map", json={}).json()
    assert body["coverage"] == {
        "clustered": 16,
        "corpus": 16,
        "regions": 2,
        "drawn": 16,
        "complete": True,
    }
    assert sorted(region["label"] for region in body["regions"]) == ["active", "null_saturated"]


def test_map_stream_sends_partial_states_before_the_finished_map(client):
    app.state.gapmap = _map_service()
    with client.stream("POST", "/map/stream", json={}) as response:
        assert response.status_code == 200
        events = [line for line in response.iter_lines() if line.startswith("event:")]
    assert events[-1] == "event: result"
    assert "event: progress" in events


def test_map_failures_do_not_expose_provider_details(client):
    async def fail(**kwargs):
        raise RuntimeError("ELASTIC_API_KEY=secret")

    app.state.gapmap = SimpleNamespace(assess=fail, stream=fail)
    response = client.post("/map", json={})
    assert response.status_code == 503 and "secret" not in response.text
    streamed = client.post("/map/stream", json={}).text
    assert "event: error" in streamed and "secret" not in streamed


def test_map_rejects_unknown_fields(client):
    assert client.post("/map", json={"idea": "a measurable idea", "scan": 3}).status_code == 422


def test_concept_search_route_returns_the_service_payload(client):
    from app.concepts import ConceptVectorService
    from app.config import Settings

    class Repo:
        async def knn_studies(self, vector, limit, filters=None):
            return [
                {
                    "id": "W1",
                    "title": "Kidney cohort",
                    "year": 2019,
                    "cosine": 0.6,
                    "embedding": [1.0, 0.0, 0.0],
                }
            ]

    built = ConceptVectorService(Repo(), config=Settings(_env_file=None))
    built.embed = _resolved_embedding
    app.state.concepts = built
    body = client.post("/concepts/search", json={"positive": ["chronic kidney disease"]}).json()
    assert body["version"] == "concepts-v1"
    assert body["concepts"] == [{"text": "chronic kidney disease", "sign": "positive"}]
    assert body["matches"][0]["id"] == "W1" and body["matches"][0]["cosine"] == 0.6
    assert body["matches"][0]["concepts"] == [
        {"text": "chronic kidney disease", "sign": "positive", "cosine": 1.0}
    ]
    assert body["warnings"] == []


def test_concept_search_maps_domain_failures_to_their_own_status(client):
    from app.concepts import ConceptsCancelOut, EmbeddingsUnavailable

    async def cancel(body):
        raise ConceptsCancelOut("The concepts cancel each other out.")

    async def unavailable(body):
        raise EmbeddingsUnavailable("Embeddings are disabled.")

    app.state.concepts = SimpleNamespace(search=cancel)
    assert client.post("/concepts/search", json={"positive": ["ckd"]}).status_code == 422
    app.state.concepts = SimpleNamespace(search=unavailable)
    assert client.post("/concepts/search", json={"positive": ["ckd"]}).status_code == 503


def test_concept_search_failures_do_not_expose_provider_details(client):
    async def fail(body):
        raise RuntimeError("ELASTIC_API_KEY=secret")

    app.state.concepts = SimpleNamespace(search=fail)
    response = client.post("/concepts/search", json={"positive": ["ckd"]})
    assert response.status_code == 503 and "secret" not in response.text


def test_concept_search_rejects_unknown_fields(client):
    assert client.post("/concepts/search", json={"positive": []}).status_code == 422
    assert client.post("/concepts/search", json={"positive": ["ckd"], "k": 3}).status_code == 422


def _resolved_embedding(text):
    future: asyncio.Future = asyncio.Future()
    future.set_result([1.0, 0.0, 0.0])
    return future
