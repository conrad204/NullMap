import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    class Repo:
        def __init__(self):
            self.saved = []

        async def save_contribution(self, doc):
            self.saved.append(doc)

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


def test_contribution_saved_as_draft_excluded_from_studies(client):
    response = client.post(
        "/contributions",
        data={
            "title": "A pilot trial",
            "description": "A shelved controlled pilot experiment.",
            "outcome": "inconclusive",
            "ownershipAcknowledged": "true",
        },
        files={"files": ("../../notes.txt", b"original source notes", "text/plain")},
    )
    assert response.status_code == 201
    stored = app.state.repository.saved[-1]
    assert stored["record_kind"] == "contribution" and stored["status"] == "draft"
    assert stored["attachments"][0]["name"] == "notes.txt"


def test_contribution_requires_rights_acknowledgment(client):
    response = client.post(
        "/contributions",
        data={
            "title": "A pilot trial",
            "description": "A shelved controlled pilot experiment.",
            "outcome": "inconclusive",
            "ownershipAcknowledged": "false",
        },
    )
    assert response.status_code == 422


def test_upload_limit_enforced(client, monkeypatch):
    from app.main import settings

    monkeypatch.setattr(settings, "max_upload_bytes", 10)
    response = client.post(
        "/contributions",
        data={
            "title": "A pilot trial",
            "description": "A shelved controlled pilot experiment.",
            "outcome": "inconclusive",
            "ownershipAcknowledged": "true",
        },
        files={"files": ("notes.txt", b"longer than limit", "text/plain")},
    )
    assert response.status_code == 413


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
