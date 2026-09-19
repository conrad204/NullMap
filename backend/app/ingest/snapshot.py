"""Projected DuckDB scan with a conservative physical-file byte budget.

Partition dates are update dates, not medical categories. Local files or an
explicit manifest are required; remote wildcard scans of the 665 GB works
snapshot are deliberately not launched implicitly.
"""

import json
from pathlib import Path


def scan_snapshot(files: list[str], output: Path, *, max_bytes: int = 5_000_000_000,
                  limit: int = 1_000_000, topic: str = "medicine",
                  remote_sizes: dict[str, int] | None = None) -> dict:
    import duckdb

    if not files or max_bytes <= 0 or limit <= 0:
        raise ValueError("Provide files, positive max_bytes and a positive row limit")
    total = 0
    for filename in files:
        if any(character in filename for character in "*?["):
            raise ValueError("Snapshot scan requires explicit files, not unbounded globs")
        if filename.startswith(("s3://", "https://")):
            if not remote_sizes or filename not in remote_sizes or remote_sizes[filename] <= 0:
                raise ValueError("Remote snapshot files require manifest size_bytes before reading")
            total += remote_sizes[filename]
        else:
            total += Path(filename).stat().st_size
    if total > max_bytes:
        raise ValueError(f"Snapshot files total {total} bytes, exceeding budget {max_bytes}")
    if output.exists():
        raise FileExistsError("Snapshot output exists; choose a new path")
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit='1GB'")
        relation = connection.read_parquet(files, union_by_name=True)
        columns = set(relation.columns)
        required = {"id", "abstract_inverted_index", "publication_year", "type", "topics"}
        if not required.issubset(columns) or not ({"title", "display_name"} & columns):
            raise ValueError(f"Unexpected snapshot schema; missing {sorted(required - columns)} or title")
        projected = [field for field in ("id", "title", "display_name", "abstract_inverted_index", "topics",
                     "type", "publication_year", "ids", "pmid", "referenced_works", "is_retracted",
                     "cited_by_count", "primary_location", "authorships") if field in columns]
        # SQL literals are bound/escaped; field identifiers come only from the allowlist above.
        predicate = "type IN ('article', 'review') AND abstract_inverted_index IS NOT NULL"
        if topic:
            escaped = topic.lower().replace("'", "''")
            predicate += f" AND lower(CAST(topics AS VARCHAR)) LIKE '%{escaped}%'"
        selected = relation.filter(predicate).project(", ".join('"' + field + '"' for field in projected)).limit(limit)
        cursor = selected.execute()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        count = 0
        with temporary.open("w") as handle:
            while batch := cursor.fetchmany(1000):
                for row in batch:
                    document = dict(zip(projected, row, strict=True))
                    # Validate that the projected abstract encoding is actually rebuildable.
                    from app.ingest.openalex import reconstruct_abstract
                    if not reconstruct_abstract(document["abstract_inverted_index"]):
                        raw = document["abstract_inverted_index"]
                        if isinstance(raw, str):
                            try:
                                raw = json.loads(raw)
                            except json.JSONDecodeError:
                                raise ValueError("Snapshot abstract is not valid inverted-index JSON") from None
                        if raw is None or isinstance(raw, (dict, list)):
                            continue  # Valid encoding, but no recoverable abstract to index.
                        raise ValueError("Snapshot abstract encoding is not a supported inverted index")
                    handle.write(json.dumps(document, default=str) + "\n")
                    count += 1
        temporary.replace(output)
        return {"records": count, "physical_bytes_budgeted": total, "columns": projected}
    finally:
        connection.close()
