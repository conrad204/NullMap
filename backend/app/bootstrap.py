"""Reproducible real-data demo bootstrap: ``python -m app.bootstrap --per-topic 100``."""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path

from app.classifier import WEAK_CLASSIFIER_VERSION, weak_classify
from app.config import settings
from app.ingest import flatten_trial, link_studies, normalize_work
from app.ingest.__main__ import parser as ingest_parser
from app.ingest.__main__ import read_jsonl, run, transform_file
from app.ingest.fetch import atomic_json, fetch_pages, fetch_work
from app.ingest.openalex import NORMALIZER_VERSION

TOPICS = {
    "vitamin-d": "vitamin D depression",
    "omega-3": "omega-3 cognitive decline",
    "knee": "arthroscopic surgery knee osteoarthritis",
}
HISTORICAL_FILTER = "type:article|review,has_abstract:true,primary_topic.field.id:27"
# Source DOIs verified against NEJM, JAMA and PubMed. These are source lookups,
# not hand-entered effects; a landmark with no source abstract is reported/skipped.
LANDMARKS = {
    "moseley-2002": "https://doi.org/10.1056/NEJMoa013259",
    "kirkley-2008": "https://doi.org/10.1056/NEJMoa0708333",
    "sihvonen-2013": "https://doi.org/10.1056/NEJMoa1305189",
    "vital-dep-2020": "https://doi.org/10.1001/jama.2020.10224",
    "areds2-cognition-2015": "https://doi.org/10.1001/jama.2015.9677",
    "opal-cognition-2010": "https://doi.org/10.3945/ajcn.2009.29121",
    "mapt-cognition-2017": "pmid:28359749",
}


def _fingerprint(paths: list[Path], extra: str = "bootstrap-v1") -> str:
    digest = hashlib.sha256(f"{extra}:{NORMALIZER_VERSION}:{WEAK_CLASSIFIER_VERSION}".encode())
    for path in paths:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()[:16]


def _write_rows(path: Path, rows: list[dict]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


async def bootstrap(*, per_topic: int = 100, cited_per_topic: int = 30,
                    data_dir: Path = Path("data"), embeddings: bool = True,
                    landmarks: bool = True) -> dict:
    if per_topic < 1 or cited_per_topic < 0:
        raise ValueError("per-topic must be positive and cited-per-topic cannot be negative")
    raw_dir = data_dir / "raw"
    stages = data_dir / "bootstrap-stages"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stages.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    fetch_counts = []
    for name, query in TOPICS.items():
        for source in ("ctgov", "openalex"):
            path = raw_dir / f"{name}-{source}.jsonl"
            state = await fetch_pages(source, path, api_key=settings.openalex_api_key,
                                      query=query, limit=per_topic, resume=True)
            files.append(path)
            row = {"topic": name, "source": source, "selection": "recent",
                   "records": state["count"], "source_exhausted": state["done"]}
            fetch_counts.append(row)
            print(json.dumps({"stage": "fetch", **row}), flush=True)
        if cited_per_topic:
            path = raw_dir / f"{name}-openalex-cited.jsonl"
            state = await fetch_pages("openalex", path, api_key=settings.openalex_api_key,
                                      query=query, filters=HISTORICAL_FILTER,
                                      limit=cited_per_topic, sort="cited_by_count:desc", resume=True)
            files.append(path)
            row = {"topic": name, "source": "openalex", "selection": "most_cited_all_years",
                   "records": state["count"], "source_exhausted": state["done"]}
            fetch_counts.append(row)
            print(json.dumps({"stage": "fetch", **row}), flush=True)
    landmark_status = []
    if landmarks:
        landmark_rows = []
        for name, identifier in LANDMARKS.items():
            path = raw_dir / f"landmark-{name}.json"
            try:
                if path.exists():
                    work = json.loads(path.read_text())
                else:
                    work = await fetch_work(identifier, api_key=settings.openalex_api_key)
                    atomic_json(path, work)
                normalized = normalize_work(work)
                landmark_status.append({"name": name, "source": identifier,
                                        "id": normalized["id"] if normalized else work.get("id"),
                                        "abstract_available": normalized is not None})
                if normalized:
                    landmark_rows.append(work)
            except (RuntimeError, ValueError, TypeError):
                landmark_status.append({"name": name, "source": identifier,
                                        "abstract_available": False, "lookup_failed": True})
            print(json.dumps({"stage": "landmark", **landmark_status[-1]}), flush=True)
        landmark_hash = hashlib.sha256(json.dumps(landmark_rows, sort_keys=True).encode()).hexdigest()[:16]
        path = stages / f"landmarks-{landmark_hash}.jsonl"
        _write_rows(path, landmark_rows)
        files.append(path)
    normalized_paths = []
    for source in files:
        target = stages / f"normalized-{source.stem}-{_fingerprint([source])}.jsonl"

        def normalize(rows):
            output = []
            for row in rows:
                study = flatten_trial(row) if "protocolSection" in row else normalize_work(row)
                if study is not None:
                    if study["source"] == "openalex":
                        study.update(weak_classify(study["abstract"], is_review=study["is_review"]))
                    output.append(study)
            return output

        transform_file(source, target, normalize, resume=True,
                       stage=f"bootstrap-normalize-{NORMALIZER_VERSION}-{WEAK_CLASSIFIER_VERSION}")
        normalized_paths.append(target)
    digest = _fingerprint(normalized_paths)
    normalized_path = stages / f"studies-{digest}.jsonl"
    unique = {row["id"]: row for path in normalized_paths for row in read_jsonl(path)}
    _write_rows(normalized_path, list(unique.values()))
    print(json.dumps({"stage": "normalize_and_classify", "unique_records": len(unique)}), flush=True)
    prepared_path = normalized_path
    if embeddings:
        model_digest = hashlib.sha256(settings.embedding_model.encode()).hexdigest()[:8]
        prepared_path = stages / f"embedded-{digest}-{model_digest}.jsonl"
        await run(ingest_parser().parse_args([
            "embed", "--input", str(normalized_path), "--output", str(prepared_path),
            "--batch-size", "32", "--resume",
        ]))
        print(json.dumps({"stage": "embed", "records": len(unique), "model": settings.embedding_model}), flush=True)
    linked_path = stages / f"linked-{_fingerprint([prepared_path])}.jsonl"
    if not linked_path.exists():
        _write_rows(linked_path, link_studies(list(read_jsonl(prepared_path))))
    linked = list(read_jsonl(linked_path))
    result = await run(ingest_parser().parse_args([
        "index", "--input", str(linked_path), "--batch-size", "100", "--resume",
    ]))
    summary = {
        "fetched_slices": fetch_counts, "landmarks": landmark_status,
        "weak_classifier_version": WEAK_CLASSIFIER_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "unique_before_linking": len(unique), "canonical_studies": len(linked),
        "source_counts": dict(Counter(row["source"] for row in linked)),
        "weak_result_labels": dict(Counter(row["result_label"] for row in linked)),
        "reviews": sum(bool(row.get("is_review")) for row in linked),
        "embedded_records": sum(bool(row.get("embedding")) for row in linked),
        "indexed_records": result["records"], "index": settings.elastic_index,
        "canonical_jsonl": str(linked_path),
        "scope": "Bounded topic slices plus explicit landmark lookups; not exhaustive clinical coverage.",
    }
    atomic_json(data_dir / "bootstrap-summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-topic", type=int, default=100)
    parser.add_argument("--cited-per-topic", type=int, default=30)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--skip-embeddings", action="store_true", help="Use BM25 only")
    parser.add_argument("--skip-landmarks", action="store_true")
    args = parser.parse_args()
    try:
        summary = asyncio.run(bootstrap(per_topic=args.per_topic, cited_per_topic=args.cited_per_topic,
                                        data_dir=args.data_dir, embeddings=not args.skip_embeddings,
                                        landmarks=not args.skip_landmarks))
        print(json.dumps(summary, indent=2))
    except (ValueError, RuntimeError, TypeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
