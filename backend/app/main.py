import asyncio
import base64
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from elastic_transport import ConnectionError as ElasticConnectionError
from elasticsearch import ApiError
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.llm import LLMService
from app.models import Bucket, NoveltyRequest, SearchRequest
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


@app.get("/studies/{study_id:path}")
@app.get("/api/studies/{study_id:path}", include_in_schema=False)
async def study_detail(study_id: str, request: Request):
    study = await request.app.state.repository.get(study_id)
    if not study or study.get("record_kind") != "study":
        raise HTTPException(status_code=404, detail="Study not found")
    return {k: v for k, v in study.items() if k not in ("embedding", "attachments")}


@app.post("/contributions", status_code=201)
@app.post("/api/contributions", status_code=201, include_in_schema=False)
async def contribute(
    request: Request,
    title: Annotated[str, Form(min_length=4, max_length=300)],
    description: Annotated[str, Form(min_length=20, max_length=20000)],
    outcome: Annotated[Bucket, Form()],
    ownershipAcknowledged: Annotated[bool, Form()],
    files: Annotated[list[UploadFile] | None, File()] = None,
):
    if not ownershipAcknowledged:
        raise HTTPException(
            status_code=422, detail="Confirm that you have permission to contribute this material."
        )
    if not title.strip() or not description.strip():
        raise HTTPException(status_code=422, detail="Title and description cannot be blank.")
    if len(files or []) > settings.max_upload_files:
        raise HTTPException(
            status_code=413, detail=f"At most {settings.max_upload_files} files are allowed."
        )
    attachments = []
    total = 0
    for upload in files or []:
        try:
            content = await upload.read(settings.max_upload_bytes + 1)
        finally:
            await upload.close()
        total += len(content)
        if total > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Combined uploads must be 5 MB or smaller.")
        attachments.append(
            {
                "name": (upload.filename or "attachment").replace("\\", "/").split("/")[-1][:200],
                "contentType": upload.content_type or "application/octet-stream",
                "bytes": len(content),
                "contentBase64": base64.b64encode(content).decode(),
            }
        )
    identifier = f"c_{uuid4().hex}"
    received = datetime.now(UTC).isoformat()
    contribution = {
        "id": identifier,
        "record_kind": "contribution",
        "source": "user",
        "title": title.strip(),
        "abstract": description.strip(),
        "bucket": outcome,
        "ownership_acknowledged": True,
        "received_at": received,
        "status": "draft",
        "attachments": attachments,
    }
    try:
        await request.app.state.repository.save_contribution(contribution)
    except Exception as exc:
        logger.error("Contribution storage failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="The draft could not be saved. Please retry."
        ) from None
    return {"contributionId": identifier, "status": "ready", "receivedAt": received}


if Path(settings.frontend_dist).is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
