"""Cursor fetchers with bounded retries and crash-safe append/checkpoint ordering."""

import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

import httpx

OPENALEX_URL = "https://api.openalex.org/works"
CTGOV_URL = "https://clinicaltrials.gov/api/v2/studies"
OPENALEX_FILTER = "type:article|review,has_abstract:true,from_publication_date:2015-01-01,primary_topic.field.id:27"
CTGOV_FILTER = "AREA[StudyType]INTERVENTIONAL AND AREA[OverallStatus](COMPLETED OR TERMINATED OR WITHDRAWN OR SUSPENDED)"


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        json.dump(data, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


async def request_json(client: httpx.AsyncClient, url: str, params: dict,
                       retries: int = 5, sleep: Callable = asyncio.sleep) -> dict:
    """Never include request URLs (which can contain API keys) in raised errors."""
    for attempt in range(retries + 1):
        try:
            response = await client.get(url, params=params)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == retries:
                    raise RuntimeError(f"Source API unavailable after retries (HTTP {response.status_code})")
                retry_after = response.headers.get("retry-after", "")
                delay = min(120, float(retry_after)) if retry_after.isdigit() else min(60, 2 ** attempt)
                await sleep(delay)
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"Source API rejected request (HTTP {response.status_code}); check filters and credentials")
            data = response.json()
            if not isinstance(data, dict):
                raise TypeError("Source API returned an unexpected response shape")
            return data
        except (httpx.TransportError, ValueError):
            if attempt == retries:
                raise RuntimeError("Source API transport or JSON error after retries") from None
            await sleep(min(60, 2 ** attempt))
    raise RuntimeError("Unreachable retry state")


async def fetch_pages(source: str, output: Path, *, api_key: str = "", query: str = "",
                      filters: str | None = None, limit: int = 1000, resume: bool = False,
                      page_size: int | None = None, sort: str = "publication_date:desc",
                      client: httpx.AsyncClient | None = None) -> dict:
    if source not in {"openalex", "ctgov"}:
        raise ValueError("source must be openalex or ctgov")
    if limit <= 0:
        raise ValueError("limit must be positive (choose an explicit download budget)")
    maximum = 100 if source == "openalex" else 1000
    page_size = min(page_size or maximum, maximum)
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    filters = filters if filters is not None else (OPENALEX_FILTER if source == "openalex" else CTGOV_FILTER)
    signature_parts = [source, query, filters, page_size]
    if source == "openalex" and sort != "publication_date:desc":
        signature_parts.append(sort)
    signature = hashlib.sha256(json.dumps(signature_parts).encode()).hexdigest()
    checkpoint = output.with_suffix(output.suffix + ".checkpoint.json")
    state = {"signature": signature, "source": source, "cursor": "*" if source == "openalex" else None,
             "count": 0, "bytes": 0, "done": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    if resume and checkpoint.exists():
        state = json.loads(checkpoint.read_text())
        if state.get("signature") != signature:
            raise ValueError("Resume parameters differ from the checkpoint")
        if not output.exists() or output.stat().st_size < state["bytes"]:
            raise ValueError("Checkpoint output is missing or truncated")
    elif output.exists():
        raise FileExistsError("Output already exists; use --resume or select a new output")
    if not output.exists():
        output.touch()
    # Roll back any lines written after the last committed checkpoint.
    if output.stat().st_size != state["bytes"]:
        with output.open("r+b") as handle:
            handle.truncate(state["bytes"])
    atomic_json(checkpoint, state)
    if state["done"] or state["count"] >= limit:
        return state
    owned = client is None
    client = client or httpx.AsyncClient(timeout=60, follow_redirects=True)
    try:
        while state["count"] < limit and not state["done"]:
            size = min(page_size, limit - state["count"])
            if source == "openalex":
                params = {"filter": filters, "per_page": size, "cursor": state["cursor"],
                          "sort": sort}
                if api_key:
                    params["api_key"] = api_key
                if query:
                    params["search"] = query
                data = await request_json(client, OPENALEX_URL, params)
                rows = data.get("results")
                next_cursor = data.get("meta", {}).get("next_cursor")
            else:
                params = {"format": "json", "pageSize": size, "filter.advanced": filters}
                if state["cursor"]:
                    params["pageToken"] = state["cursor"]
                if query:
                    params["query.term"] = query
                data = await request_json(client, CTGOV_URL, params)
                rows = data.get("studies")
                next_cursor = data.get("nextPageToken")
            if not isinstance(rows, list):
                raise TypeError("Source API response has no record list")
            if rows and next_cursor and next_cursor == state["cursor"]:
                raise RuntimeError("Source API repeated its paging cursor")
            with output.open("ab") as handle:
                for row in rows:
                    handle.write((json.dumps(row, ensure_ascii=False) + "\n").encode())
                handle.flush()
                os.fsync(handle.fileno())
                state["bytes"] = handle.tell()
            state.update(count=state["count"] + len(rows), cursor=next_cursor,
                         done=not rows or not next_cursor)
            atomic_json(checkpoint, state)
    finally:
        if owned:
            await client.aclose()
    return state


async def fetch_work(identifier: str, *, api_key: str = "", client: httpx.AsyncClient | None = None) -> dict:
    """Free singleton lookup for PMID/DOI links and review references."""
    from urllib.parse import quote

    identifier = identifier.removeprefix("https://openalex.org/")
    owned = client is None
    client = client or httpx.AsyncClient(timeout=45)
    try:
        return await request_json(client, OPENALEX_URL + "/" + quote(identifier, safe=""),
                                  {"api_key": api_key} if api_key else {})
    finally:
        if owned:
            await client.aclose()
