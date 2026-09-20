import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from elastic_transport import ConnectionError as ElasticConnectionError
from elasticsearch import ApiError
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.gapmap_service import GapMapService
from app.llm import LLMService
from app.models import MapRequest, NoveltyRequest, SearchRequest
from app.novelty import NoveltyEngine
from app.pipeline import SearchPipeline
from app.repository import ElasticRepository

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    repo = ElasticRepository()
    llm = LLMService()
    app.state.repository = repo
    app.state.pipeline = SearchPipeline(repo, llm)
    app.state.novelty = NoveltyEngine(llm, repo=repo)
    app.state.gapmap = GapMapService(repo)
    yield
    await app.state.novelty.fulltext.close()
    await llm.close()
    await repo.close()


app = FastAPI(title="nullMap API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
@app.get("/api/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
@app.get("/api/ready", include_in_schema=False)
async def ready(request: Request):
    try:
        elastic = await request.app.state.repository.health()
    except Exception as exc:
        logger.warning("Elasticsearch readiness: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "elasticsearch": {"connected": False},
                "message": "Elasticsearch is unavailable. Check ELASTIC_URL and credentials or start the local container.",
            },
        )
    return {
        "status": "ready",
        "elasticsearch": elastic,
        "openaiConfigured": bool(settings.openai_api_key),
        "openalexSource": "public_s3_snapshot",
        "openalexSnapshotManifest": settings.openalex_snapshot_manifest,
        "embeddingsEnabled": settings.embeddings_enabled,
        "compressionEnabled": settings.compression_enabled and settings.compression_validated,
    }


def search_error(exc: Exception) -> str:
    if isinstance(exc, (ApiError, ElasticConnectionError)):
        return "Elasticsearch could not complete the search. Check the backend connection and index setup."
    return "The evidence search could not complete. Please retry or inspect the backend logs."


@app.post("/search")
@app.post("/api/search", include_in_schema=False)
async def search(body: SearchRequest, request: Request):
    try:
        return await request.app.state.pipeline.search(body)
    except Exception as exc:
        logger.error("Search failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail=search_error(exc)) from None


@app.post("/search/stream")
@app.post("/api/search/stream", include_in_schema=False)
async def search_stream(body: SearchRequest, request: Request):
    async def stream():
        queue: asyncio.Queue = asyncio.Queue(maxsize=16)

        async def progress(payload):
            await queue.put(("progress", payload))

        async def run():
            try:
                result = await request.app.state.pipeline.search(body, progress)
                await queue.put(("result", result))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Streamed search failed: %s", type(exc).__name__)
                await queue.put(("error", {"message": search_error(exc)}))

        task = asyncio.create_task(run())
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: {event}\ndata: {json.dumps(payload, allow_nan=False)}\n\n"
                if event in ("result", "error"):
                    break
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/novelty")
@app.post("/api/novelty", include_in_schema=False)
async def novelty(body: NoveltyRequest, request: Request):
    try:
        return await request.app.state.novelty.assess(body.hypothesis, body.scan, body.read)
    except Exception as exc:
        logger.error("Novelty assessment failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="The novelty assessment could not complete. Please retry or inspect the backend logs.",
        ) from None


@app.post("/map")
@app.post("/api/map", include_in_schema=False)
async def gap_map(body: MapRequest, request: Request):
    try:
        return await request.app.state.gapmap.assess(
            idea=body.idea,
            arithmetic=body.arithmetic,
            cutoff_year=body.cutoffYear,
            refresh=body.refresh,
        )
    except Exception as exc:
        logger.error("Gap map failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="The evidence map could not be built. Please retry or inspect the backend logs.",
        ) from None


@app.get("/studies/{study_id:path}")
@app.get("/api/studies/{study_id:path}", include_in_schema=False)
async def study_detail(study_id: str, request: Request):
    study = await request.app.state.repository.get(study_id)
    if not study or study.get("record_kind") != "study":
        raise HTTPException(status_code=404, detail="Study not found")
    return {k: v for k, v in study.items() if k not in ("embedding", "attachments")}


if Path(settings.frontend_dist).is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
