"""Bounded normalization/embedding/linking directly into the shared ES index."""

import asyncio
import json
from pathlib import Path

from elasticsearch.helpers import async_scan

from app.classifier import WEAK_CLASSIFIER_VERSION, weak_classify
from app.config import Settings, settings
from app.ingest.ctgov import flatten_trial
from app.ingest.fetch import atomic_json
from app.ingest.linker import link_studies
from app.ingest.openalex import NORMALIZER_VERSION, normalize_work
from app.ingest.snapshot import fingerprint


async def link_indexed_batch(repository, documents: list[dict]) -> None:
    """Link across batches and sources; distinct registered trials stay distinct."""
    pmids = sorted({str(pmid) for doc in documents for pmid in doc.get("pmids", [])})
    nct_ids = sorted({nct for doc in documents for nct in doc.get("nct_ids", [])})
    result_pmids = sorted({str(pmid) for doc in documents for pmid in doc.get("result_pmids", [])})
    clauses = []
    for name, values in (("pmids", result_pmids), ("result_pmids", pmids), ("nct_ids", nct_ids)):
        if values:
            clauses.append({"terms": {name: values}})
    if not clauses:
        return
    current = await repository.get_many([doc["id"] for doc in documents], include_vectors=False)
    query = {
        "query": {
            "bool": {
                "filter": [{"term": {"record_kind": "study"}}],
                "should": clauses,
                "minimum_should_match": 1,
                "must_not": [{"term": {"is_review": True}}],
            }
        },
        "_source": {"excludes": ["embedding", "attachments"]},
    }
    related = [
        hit["_source"]
        async for hit in async_scan(
            repository.client,
            index=repository.index,
            query=query,
            size=500,
        )
    ]
    linked = link_studies([*related, *current])
    merged = [doc for doc in linked if doc.get("source") == "merged"]
    await repository.persist_links(merged)


async def materialize(
    path: Path,
    repository,
    *,
    embedder=None,
    batch_size: int = 100,
    config: Settings = settings,
    progress=None,
) -> dict:
    """A committed byte offset avoids rereading a million rows on resume.

    No vector JSONL intermediate or all-corpus in-memory linker is created.
    A crash before checkpoint repeats idempotent writes, never loses a batch.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    source_checkpoint = path.with_suffix(path.suffix + ".checkpoint.json")
    source_identity = (
        json.loads(source_checkpoint.read_text())["signature"]
        if source_checkpoint.exists()
        else [path.stat().st_size, path.stat().st_mtime_ns]
    )
    signature = fingerprint(
        [
            str(path.resolve()),
            source_identity,
            repository.index,
            config.elastic_url,
            NORMALIZER_VERSION,
            WEAK_CLASSIFIER_VERSION,
            config.embedding_model if embedder is not None else "no_vectors",
        ]
    )
    checkpoint = path.with_suffix(path.suffix + ".materialize.json")
    state = {
        "signature": signature,
        "offset": 0,
        "records": 0,
        "metadata_only": 0,
        "embedded": 0,
        "complete": False,
    }
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text())
        if state["signature"] != signature:
            raise ValueError(
                "Source, target index or model changed; use a new materialization checkpoint"
            )
    if path.stat().st_size < state["offset"]:
        raise ValueError("Materialized source was truncated")
    if state["complete"] and path.stat().st_size == state["offset"]:
        return state
    state["complete"] = False
    with path.open("rb") as handle:
        handle.seek(state["offset"])
        while True:
            batch = []
            while len(batch) < batch_size:
                line = handle.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                raw = json.loads(line)
                doc = flatten_trial(raw) if "protocolSection" in raw else normalize_work(raw)
                if doc["source"] == "openalex":
                    doc.update(weak_classify(doc["abstract"], is_review=doc["is_review"]))
                batch.append(doc)
            if not batch:
                break
            if embedder is not None:
                texts = [
                    " ".join(
                        str(doc.get(key) or "")
                        for key in ("title", "abstract", "intervention", "outcome")
                    )
                    for doc in batch
                ]
                vectors = await asyncio.to_thread(embedder.encode, texts)
                for doc, vector in zip(batch, vectors, strict=True):
                    doc.update(embedding=vector, embedding_model=config.embedding_model)
            await repository.bulk_upsert(batch)
            await link_indexed_batch(repository, batch)
            state.update(
                offset=handle.tell(),
                records=state["records"] + len(batch),
                metadata_only=state["metadata_only"]
                + sum(doc.get("abstract_available") is False for doc in batch),
                embedded=state["embedded"] + (len(batch) if embedder is not None else 0),
            )
            atomic_json(checkpoint, state)
            if progress:
                progress(dict(state))
        state["offset"] = handle.tell()
    state["complete"] = True
    atomic_json(checkpoint, state)
    return state
