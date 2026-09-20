"""Anonymous public-S3 Parquet scans with pinned manifests and resumable output.

Run this batch code on the data/compute host. DuckDB reads selected columns over
HTTPS range requests; it never downloads the full snapshot onto a browser client.
"""

import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.ingest.fetch import atomic_json, request_json
from app.ingest.scope import PROFILES, SCOPE_VERSION, coarse_predicate, matches_work

PUBLIC_MANIFEST = "https://openalex.s3.amazonaws.com/data/parquet/works/manifest.json"
PUBLIC_HOSTS = {"openalex.s3.amazonaws.com", "openalex.s3.us-east-1.amazonaws.com"}
PROJECTED_FIELDS = (
    "id",
    "title",
    "display_name",
    "abstract_inverted_index",
    "topics",
    "type",
    "publication_year",
    "publication_date",
    "ids",
    "pmid",
    "doi",
    "referenced_works",
    "is_retracted",
    "cited_by_count",
    "primary_location",
    "authorships",
    "updated_date",
)


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def public_url(value: str) -> str:
    """Force anonymous HTTPS to the public bucket, never a signed/paid/API endpoint."""
    parsed = urlparse(value)
    if parsed.scheme == "s3" and parsed.netloc == "openalex":
        value = "https://openalex.s3.amazonaws.com" + parsed.path
        parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc not in PUBLIC_HOSTS
        or not parsed.path.startswith("/data/parquet/works/")
        or parsed.query
        or parsed.fragment
        or "/../" in parsed.path
    ):
        raise ValueError("Expected an unsigned public OpenAlex works Parquet S3 URL")
    return value


async def load_manifest(location: str, *, client: httpx.AsyncClient | None = None) -> dict:
    if "://" in location:
        url = public_url(location)
        owned = client is None
        client = client or httpx.AsyncClient(timeout=60)
        try:
            data = await request_json(client, url, {})
        finally:
            if owned:
                await client.aclose()
    else:
        data = json.loads(Path(location).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise ValueError("Expected an OpenAlex works manifest containing a files array")
    if data.get("entity", "works") != "works" or data.get("format", "parquet") != "parquet":
        raise ValueError("Only the works Parquet manifest is supported")
    return data


def plan_manifest(
    manifest: dict, *, max_files: int | None = None, largest_first: bool = False
) -> dict:
    """Select manifest parts to scan.

    Parts are listed in update-date order and vary in size by four orders of
    magnitude, while per-part scan time is dominated by fixed overhead. For an
    explicitly partial run, ``largest_first`` therefore covers far more of the
    corpus per unit of time. It never changes which parts exist, only their
    order, so a full run is unaffected.
    """
    if max_files is not None and max_files <= 0:
        raise ValueError("max-files must be positive")
    parts = []
    seen = set()
    for entry in manifest["files"]:
        location = entry.get("url", "")
        if "://" in location:
            location = public_url(location)
        elif location:
            location = str(Path(location).resolve())
        size = int(entry.get("size_bytes") or entry.get("meta", {}).get("content_length") or 0)
        if not location.endswith(".parquet") or size <= 0 or location in seen:
            raise ValueError("Manifest requires unique Parquet paths and positive file sizes")
        seen.add(location)
        parts.append({"url": location, "size_bytes": size})
    if not parts:
        raise ValueError("Manifest has no Parquet files")
    ordered = sorted(parts, key=lambda part: -part["size_bytes"]) if largest_first else parts
    selected = ordered[:max_files] if max_files is not None else ordered
    total_bytes = sum(part["size_bytes"] for part in parts)
    budgeted = sum(part["size_bytes"] for part in selected)
    return {
        "manifest_sha256": fingerprint(manifest),
        "snapshot_date": manifest.get("date"),
        "total_parts": len(parts),
        "selected_parts": len(selected),
        "selection_order": "largest_first" if largest_first else "manifest",
        "physical_bytes_budgeted": budgeted,
        "selected_bytes_fraction": round(budgeted / total_bytes, 6) if total_bytes else 0.0,
        "all_parts_selected": len(parts) == len(selected),
        "files": selected,
        "note": "Physical file sizes are a conservative scan budget, not bytes actually transferred. Partitions are update dates, not topics. A partial selection is a subset of update partitions, not a random sample of the corpus.",
    }


def _connection(extension_directory: Path):
    import duckdb

    extension_directory.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(config={"extension_directory": str(extension_directory.resolve())})
    connection.execute("SET memory_limit='1GB'")
    connection.execute("SET TimeZone='UTC'")
    # Stable row order lets a batch checkpoint resume within a pinned part.
    connection.execute("SET threads=1")
    connection.execute("SET preserve_insertion_order=true")
    return connection


def scan_snapshot(
    files: list[str],
    output: Path,
    *,
    max_bytes: int = 5_000_000_000,
    limit: int | None = None,
    topic: str = "",
    profile: str = "all",
    remote_sizes: dict[str, int] | None = None,
    resume: bool = False,
    snapshot_id: str = "",
    snapshot_date: str | None = None,
    all_parts_selected: bool = False,
    work_ids: set[str] | None = None,
    batch_size: int = 1000,
    min_free_bytes: int = 1_000_000_000,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    if (
        not files
        or max_bytes <= 0
        or batch_size <= 0
        or min_free_bytes < 0
        or (limit is not None and limit <= 0)
    ):
        raise ValueError("Provide files, positive budgets and a positive optional limit")
    if profile not in PROFILES:
        raise ValueError("Unknown corpus profile")
    if work_ids is not None and (
        not work_ids or any(not re.fullmatch(r"W\d+", x) for x in work_ids)
    ):
        raise ValueError("Reference IDs must be a nonempty set of W identifiers")
    sources, identities, total = [], [], 0
    for filename in files:
        if any(character in filename for character in "*?["):
            raise ValueError("Snapshot scan requires explicit files, not globs or signed URLs")
        if "://" in filename:
            source = public_url(filename)
            size = (remote_sizes or {}).get(filename) or (remote_sizes or {}).get(source)
            if not size or size <= 0:
                raise ValueError("Remote snapshot files require manifest size_bytes before reading")
            identity = [source, size]
        else:
            path = Path(filename).resolve()
            source, size = str(path), path.stat().st_size
            identity = [source, size, path.stat().st_mtime_ns]
        sources.append(source)
        identities.append(identity)
        total += size
    if total > max_bytes:
        raise ValueError(
            f"Snapshot files total {total} bytes, exceeding budget {max_bytes}; inspect the plan on the batch host"
        )
    signature = fingerprint(
        [identities, snapshot_id, profile, topic, SCOPE_VERSION, sorted(work_ids or [])]
    )
    checkpoint = output.with_suffix(output.suffix + ".checkpoint.json")
    state = {
        "signature": signature,
        "snapshot_id": snapshot_id,
        "snapshot_date": snapshot_date,
        "scope": "reference_backfill" if work_ids is not None else profile,
        "scope_version": SCOPE_VERSION,
        "records": 0,
        "metadata_only": 0,
        "bytes": 0,
        "part": 0,
        "part_rows": 0,
        "selected_parts": len(sources),
        "physical_bytes_budgeted": total,
        "selected_parts_complete": False,
        "complete_snapshot_scope": False,
        "columns": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    if resume and checkpoint.exists():
        state = json.loads(checkpoint.read_text())
        if state.get("signature") != signature:
            raise ValueError("Snapshot, source files or scope changed; use a new output/checkpoint")
        if not output.exists() or output.stat().st_size < state["bytes"]:
            raise ValueError("Checkpoint output is missing or truncated")
    elif output.exists():
        raise FileExistsError("Snapshot output exists; use --resume or choose a new path")
    if not output.exists():
        output.touch()
    if output.stat().st_size != state["bytes"]:
        with output.open("r+b") as handle:
            handle.truncate(state["bytes"])
    atomic_json(checkpoint, state)
    if state["selected_parts_complete"] or (limit is not None and state["records"] >= limit):
        return state
    connection = _connection(output.parent / ".duckdb-extensions")
    try:
        if any(source.startswith("https://") for source in sources):
            # HTTP URLs avoid AWS signing and credential discovery altogether.
            connection.execute("INSTALL httpfs FROM core")
            connection.execute("LOAD httpfs")
        if work_ids is not None:
            connection.execute("CREATE TEMP TABLE wanted(id VARCHAR PRIMARY KEY)")
            connection.executemany(
                "INSERT INTO wanted VALUES (?)", [(x,) for x in sorted(work_ids)]
            )
        while state["part"] < len(sources):
            if shutil.disk_usage(output.parent).free < min_free_bytes:
                raise RuntimeError(
                    "Insufficient free space on the batch host; checkpoint preserved"
                )
            source = sources[state["part"]]
            relation = connection.read_parquet(source)
            columns = set(relation.columns)
            if "id" not in columns or not {"title", "display_name"} & columns:
                raise ValueError(
                    "Unexpected snapshot schema: id and title/display_name are required"
                )
            projected = [field for field in PROJECTED_FIELDS if field in columns]
            state["columns"] = sorted(set(state["columns"]) | set(projected))
            predicate = (
                "regexp_replace(id, '^.*/', '') IN (SELECT id FROM wanted)"
                if work_ids is not None
                else coarse_predicate(columns, profile)
            )
            # Native TIMESTAMPTZ conversion otherwise requires an unrelated
            # Python timezone package. ISO strings also serialize consistently.
            expressions = [
                f'CAST("{field}" AS VARCHAR) AS "{field}"'
                if field in {"publication_date", "updated_date"}
                else f'"{field}"'
                for field in projected
            ]
            selected = relation.filter(predicate).project(", ".join(expressions))
            if state["part_rows"]:
                selected = selected.limit(2**63 - 1, offset=state["part_rows"])
            cursor = selected.execute()
            while rows := cursor.fetchmany(batch_size):
                if shutil.disk_usage(output.parent).free < min_free_bytes:
                    raise RuntimeError(
                        "Insufficient free space on the batch host; checkpoint preserved"
                    )
                reached_limit = False
                with output.open("ab") as handle:
                    for row in rows:
                        document = dict(zip(projected, row, strict=True))
                        state["part_rows"] += 1
                        if work_ids is None and not matches_work(document, profile, topic):
                            continue
                        document["snapshot_provenance"] = {
                            "source": "public_s3",
                            "manifest_sha256": snapshot_id,
                            "snapshot_date": snapshot_date,
                            "part": source,
                            "scope": "reference_backfill" if work_ids is not None else profile,
                        }
                        handle.write(
                            (json.dumps(document, ensure_ascii=False, default=str) + "\n").encode()
                        )
                        state["records"] += 1
                        if not document.get("abstract_inverted_index"):
                            state["metadata_only"] += 1
                        if limit is not None and state["records"] >= limit:
                            reached_limit = True
                            break
                    handle.flush()
                    os.fsync(handle.fileno())
                    state["bytes"] = handle.tell()
                atomic_json(checkpoint, state)
                if progress:
                    progress(dict(state))
                if reached_limit:
                    return state  # A row-limited run never claims source exhaustion.
            state.update(part=state["part"] + 1, part_rows=0)
            atomic_json(checkpoint, state)
            if progress:
                progress(dict(state))
        state["selected_parts_complete"] = True
        state["complete_snapshot_scope"] = bool(all_parts_selected)
        atomic_json(checkpoint, state)
        return state
    finally:
        connection.close()
