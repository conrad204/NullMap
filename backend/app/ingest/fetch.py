"""ClinicalTrials.gov paging and shared atomic checkpoint helpers.

OpenAlex is read exclusively from the public S3 snapshot in snapshot.py.
"""

import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

import httpx

CTGOV_URL = "https://clinicaltrials.gov/api/v2/studies"
CTGOV_FILTER = "AREA[StudyType]INTERVENTIONAL AND AREA[OverallStatus](COMPLETED OR TERMINATED OR WITHDRAWN OR SUSPENDED)"


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        json.dump(data, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


async def request_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    retries: int = 5,
    sleep: Callable = asyncio.sleep,
) -> dict:
    """Bounded retries; exception messages never include URLs or credentials."""
    for attempt in range(retries + 1):
        try:
            response = await client.get(url, params=params)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == retries:
                    raise RuntimeError(
                        f"Source unavailable after retries (HTTP {response.status_code})"
                    )
                retry_after = response.headers.get("retry-after", "")
                delay = (
                    min(60, float(retry_after)) if retry_after.isdigit() else min(60, 2**attempt)
                )
                await sleep(delay)
                continue
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Source rejected request (HTTP {response.status_code}); check source parameters"
                )
            data = response.json()
            if not isinstance(data, dict):
                raise TypeError("Source returned an unexpected response shape")
            return data
        except (httpx.TransportError, ValueError):
            if attempt == retries:
                raise RuntimeError("Source transport or JSON error after retries") from None
            await sleep(min(60, 2**attempt))
    raise RuntimeError("Unreachable retry state")


async def fetch_pages(
    source: str,
    output: Path,
    *,
    query: str = "",
    filters: str | None = None,
    limit: int | None = 1000,
    resume: bool = False,
    page_size: int = 1000,
    client: httpx.AsyncClient | None = None,
) -> dict:
    if source != "ctgov":
        raise ValueError(
            "Only ctgov uses API paging; use the snapshot command for OpenAlex public S3"
        )
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive or None for source exhaustion")
    if not 1 <= page_size <= 1000:
        raise ValueError("page_size must be between 1 and 1000")
    filters = CTGOV_FILTER if filters is None else filters
    signature = hashlib.sha256(json.dumps([source, query, filters, page_size]).encode()).hexdigest()
    checkpoint = output.with_suffix(output.suffix + ".checkpoint.json")
    state = {
        "signature": signature,
        "source": source,
        "cursor": None,
        "count": 0,
        "bytes": 0,
        "done": False,
        "total_count": None,
    }
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
    if output.stat().st_size != state["bytes"]:
        with output.open("r+b") as handle:
            handle.truncate(state["bytes"])
    atomic_json(checkpoint, state)
    if state["done"] or (limit is not None and state["count"] >= limit):
        return state
    owned = client is None
    client = client or httpx.AsyncClient(timeout=60, follow_redirects=True)
    try:
        while not state["done"] and (limit is None or state["count"] < limit):
            size = page_size if limit is None else min(page_size, limit - state["count"])
            params = {"format": "json", "pageSize": size, "countTotal": "true"}
            if filters:
                params["filter.advanced"] = filters
            if query:
                params["query.term"] = query
            if state["cursor"]:
                params["pageToken"] = state["cursor"]
            data = await request_json(client, CTGOV_URL, params)
            rows, next_cursor = data.get("studies"), data.get("nextPageToken")
            if not isinstance(rows, list):
                raise TypeError("Source response has no study list")
            if rows and next_cursor and next_cursor == state["cursor"]:
                raise RuntimeError("Source repeated its paging cursor")
            with output.open("ab") as handle:
                for row in rows:
                    handle.write((json.dumps(row, ensure_ascii=False) + "\n").encode())
                handle.flush()
                os.fsync(handle.fileno())
                state["bytes"] = handle.tell()
            state.update(
                count=state["count"] + len(rows),
                cursor=next_cursor,
                done=not rows or not next_cursor,
                total_count=data.get("totalCount", state.get("total_count")),
            )
            atomic_json(checkpoint, state)
    finally:
        if owned:
            await client.aclose()
    return state
