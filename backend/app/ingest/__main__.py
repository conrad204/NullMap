"""Run with ``python -m app.ingest --help`` from backend/."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from app.ingest.fetch import atomic_json, fetch_pages
from app.ingest.scope import PROFILES


def read_jsonl(path: Path):
    with path.open() as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise TypeError("record is not an object")
                yield row
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                raise ValueError(f"Invalid JSONL at {path.name}:{line_number}") from exc


def transform_file(source: Path, output: Path, transform, *, resume: bool,
                   stage: str, batch_size: int = 100) -> dict:
    """Transform batches with output truncation and an input-record checkpoint."""
    if source.resolve() == output.resolve():
        raise ValueError("Input and output paths must differ")
    signature = hashlib.sha256(json.dumps([str(source.resolve()), source.stat().st_size,
                                          source.stat().st_mtime_ns, stage]).encode()).hexdigest()
    checkpoint = output.with_suffix(output.suffix + ".checkpoint.json")
    state = {"signature": signature, "input_records": 0, "output_records": 0, "bytes": 0, "done": False}
    if resume and checkpoint.exists():
        state = json.loads(checkpoint.read_text())
        if state.get("signature") != signature:
            raise ValueError("Input or transformation changed since checkpoint")
    elif output.exists():
        raise FileExistsError("Output already exists; use --resume or choose a new path")
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists():
        if state["bytes"]:
            raise ValueError("Checkpoint output is missing")
        output.touch()
    if output.stat().st_size < state["bytes"]:
        raise ValueError("Checkpoint output is truncated")
    if output.stat().st_size != state["bytes"]:
        with output.open("r+b") as handle:
            handle.truncate(state["bytes"])
    atomic_json(checkpoint, state)
    if state["done"]:
        return state
    skipped = state["input_records"]
    batch = []

    def commit(rows):
        result = transform(rows)
        with output.open("ab") as handle:
            for row in result:
                handle.write((json.dumps(row, ensure_ascii=False) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
            state["bytes"] = handle.tell()
        state["input_records"] += len(rows)
        state["output_records"] += len(result)
        atomic_json(checkpoint, state)

    for index, row in enumerate(read_jsonl(source)):
        if index < skipped:
            continue
        batch.append(row)
        if len(batch) >= batch_size:
            commit(batch)
            batch = []
    if batch:
        commit(batch)
    state["done"] = True
    atomic_json(checkpoint, state)
    return state


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="NullMap resumable ingestion; all intermediate stages use JSONL")
    commands = root.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="Download ClinicalTrials.gov records")
    fetch.add_argument("source", choices=["ctgov"])
    fetch.add_argument("--output", type=Path, required=True)
    fetch.add_argument("--query", default="")
    fetch.add_argument("--filter", default=None)
    fetch.add_argument("--limit", type=int, default=1000)
    fetch.add_argument("--all", action="store_true", help="Continue until the registry cursor is exhausted")
    fetch.add_argument("--page-size", type=int, default=1000)
    fetch.add_argument("--resume", action="store_true")
    for name in ("normalize", "classify", "embed"):
        command = commands.add_parser(name)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--resume", action="store_true")
        command.add_argument("--batch-size", type=int, default=100)
        if name == "classify":
            command.add_argument("--model", type=Path)
    link = commands.add_parser("link", help="Merge normalized JSONL files by PMID and NCT")
    link.add_argument("--input", type=Path, nargs="+", required=True)
    link.add_argument("--output", type=Path, required=True)
    link.add_argument("--resume", action="store_true")
    train = commands.add_parser("train-classifier")
    train.add_argument("--input", type=Path, required=True)
    train.add_argument("--output", type=Path, required=True)
    train.add_argument("--label-field", default="human_label")
    label = commands.add_parser("label", help="Create instrumented structured LLM labels for training")
    label.add_argument("--input", type=Path, required=True)
    label.add_argument("--output", type=Path, required=True)
    label.add_argument("--limit", type=int, default=100)
    label.add_argument("--batch-size", type=int, default=10)
    label.add_argument("--resume", action="store_true")
    index = commands.add_parser("index")
    index.add_argument("--input", type=Path, required=True)
    index.add_argument("--batch-size", type=int, default=200)
    index.add_argument("--resume", action="store_true")
    index.add_argument("--sesoi", type=float, default=0.2)
    index.add_argument("--effect-type", default="SMD")
    pagerank = commands.add_parser(
        "pagerank",
        help="Recompute within-corpus citation PageRank and update authority rank features",
    )
    pagerank.add_argument("--damping", type=float, default=0.85)
    pagerank.add_argument("--iterations", type=int, default=30)
    pagerank.add_argument("--batch-size", type=int, default=500)
    references = commands.add_parser("reference-ids", help="Collect missing review-reference IDs for S3 backfill")
    references.add_argument("--input", type=Path, nargs="+", required=True)
    references.add_argument("--output", type=Path, required=True)
    tei = commands.add_parser("tei", help="Extract null-hypothesis evidence from GROBID TEI XML full text")
    tei.add_argument("--input", type=Path, nargs="+", required=True, help="TEI .xml files or directories")
    tei.add_argument("--output", type=Path, required=True)
    tei.add_argument("--source", default="openalex")
    snapshot = commands.add_parser("snapshot", help="Read the public OpenAlex S3 Parquet snapshot")
    snapshot.add_argument("--input", nargs="+", default=[])
    snapshot.add_argument("--manifest", help="Public S3 manifest URL or a pinned local manifest; defaults to public works")
    snapshot.add_argument("--max-files", type=int, help="Select at most this many manifest files before enforcing the byte budget")
    snapshot.add_argument("--largest-first", action="store_true", help="Order parts by descending size; a partial run then covers more of the corpus")
    snapshot.add_argument("--skip-files", type=int, default=0, help="Skip this many leading manifest files, to continue after an earlier --max-files run in a new output directory")
    snapshot.add_argument("--output", type=Path, default=Path("data/snapshot/works.jsonl"))
    snapshot.add_argument("--max-bytes", type=int, default=5_000_000_000)
    snapshot.add_argument("--limit", type=int, help="Optional explicit row cap; absent means exhaust selected parts")
    snapshot.add_argument("--topic", default="", help="Optional additional topic-name filter")
    snapshot.add_argument("--profile", choices=list(PROFILES), default="hypertension-kidney", help="The -pubmed profile keeps only PubMed-indexed works, which registry linking and full text need")
    snapshot.add_argument("--work-ids", type=Path, help="Backfill these newline-separated W IDs from S3; overrides topic/profile filtering")
    snapshot.add_argument("--min-free-bytes", type=int, default=1_000_000_000)
    snapshot.add_argument("--plan", action="store_true", help="Inspect the manifest without reading Parquet")
    snapshot.add_argument("--resume", action="store_true")
    return root


async def run(args) -> dict:
    from app.config import settings

    if getattr(args, "batch_size", 1) <= 0:
        raise ValueError("batch-size must be positive")
    if args.command == "fetch":
        state = await fetch_pages(args.source, args.output,
                                  query=args.query, filters=args.filter, limit=None if args.all else args.limit,
                                  resume=args.resume, page_size=args.page_size)
        return {"records": state["count"], "source_exhausted": state["done"], "output": str(args.output)}
    if args.command == "label":
        from app.ingest.label import label_file
        return await label_file(args.input, args.output, limit=args.limit,
                                batch_size=args.batch_size, resume=args.resume)
    if args.command == "normalize":
        from app.ingest import flatten_trial, normalize_work

        def transform(rows):
            return [result for row in rows if (result := (
                flatten_trial(row) if "protocolSection" in row else normalize_work(row))) is not None]
    elif args.command == "classify":
        from app.classifier import ResultClassifier
        classifier = ResultClassifier(args.model or settings.classifier_model_path or None)

        def transform(rows):
            return [{**row, **classifier.classify(row)} if row.get("source") == "openalex" else row
                    for row in rows]
    elif args.command == "embed":
        from app.embeddings import get_embedder
        embedder = get_embedder(settings.embedding_model, settings.embedding_device)

        def transform(rows):
            texts = [" ".join(filter(None, [row.get("title", ""), row.get("abstract", ""),
                                            row.get("intervention", ""), row.get("outcome", "")])) for row in rows]
            vectors = embedder.encode(texts)
            return [{**row, "embedding": vector, "embedding_model": settings.embedding_model}
                    for row, vector in zip(rows, vectors, strict=True)]
    elif args.command == "link":
        from app.ingest.linker import link_studies
        signature = hashlib.sha256(json.dumps([
            [str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns] for path in args.input
        ]).encode()).hexdigest()
        checkpoint = args.output.with_suffix(args.output.suffix + ".checkpoint.json")
        if args.output.exists():
            if args.resume and checkpoint.exists():
                state = json.loads(checkpoint.read_text())
                if state.get("signature") == signature and state.get("done"):
                    return state
            raise FileExistsError("Link output exists with no matching completed checkpoint")
        rows = link_studies([row for path in args.input for row in read_jsonl(path)])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        with temporary.open("w") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        temporary.replace(args.output)
        state = {"records": len(rows), "signature": signature, "done": True}
        atomic_json(checkpoint, state)
        return state
    elif args.command == "train-classifier":
        from app.classifier import train_classifier
        return train_classifier(list(read_jsonl(args.input)), args.output, label_field=args.label_field)
    elif args.command == "reference-ids":
        from app.ingest.openalex import normalize_work
        known, referenced = set(), set()
        for path in args.input:
            for row in read_jsonl(path):
                study = normalize_work(row) if "source" not in row else row
                known.add(str(study["id"]).rsplit("/", 1)[-1])
                if study.get("is_review"):
                    referenced.update(str(x).rsplit("/", 1)[-1] for x in study.get("referenced_works", []))
        missing = sorted(referenced - known)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text("".join(identifier + "\n" for identifier in missing))
        temporary.replace(args.output)
        return {"missing_reference_ids": len(missing), "output": str(args.output)}
    elif args.command == "tei":
        from app.ingest.tei import extract_tei_files
        return extract_tei_files(args.input, args.output, source=args.source)
    elif args.command == "snapshot":
        from app.ingest.snapshot import fingerprint, load_manifest, plan_manifest, scan_snapshot
        if args.input and args.manifest:
            raise ValueError("Choose --input local files or --manifest, not both")
        location = args.manifest or settings.openalex_snapshot_manifest
        if args.input:
            manifest = {"files": [{"url": str(Path(x).resolve()), "size_bytes": Path(x).stat().st_size} for x in args.input]}
        else:
            manifest = await load_manifest(location)
        plan = plan_manifest(manifest, max_files=args.max_files,
                             largest_first=getattr(args, "largest_first", False),
                             skip_files=getattr(args, "skip_files", 0))
        if args.plan:
            return {k: v for k, v in plan.items() if k != "files"}
        pinned = args.output.with_suffix(args.output.suffix + ".manifest.json")
        if pinned.exists() and fingerprint(json.loads(pinned.read_text())) != plan["manifest_sha256"]:
            raise ValueError("The snapshot manifest changed; use a new output directory for this release")
        atomic_json(pinned, manifest)
        files = [part["url"] for part in plan["files"]]
        work_ids = {line.strip() for line in args.work_ids.read_text().splitlines() if line.strip()} if args.work_ids else None
        result = await asyncio.to_thread(
            scan_snapshot, files, args.output, max_bytes=args.max_bytes, limit=args.limit,
            topic=args.topic, profile=args.profile,
            remote_sizes={part["url"]: part["size_bytes"] for part in plan["files"]},
            resume=args.resume, snapshot_id=plan["manifest_sha256"], snapshot_date=plan["snapshot_date"],
            all_parts_selected=plan["all_parts_selected"], work_ids=work_ids,
            min_free_bytes=args.min_free_bytes,
            progress=lambda state: print(json.dumps({"stage": "snapshot", "part": state["part"], "parts": state["selected_parts"], "records": state["records"]}), flush=True),
        )
        if not args.input and "://" in location and result["selected_parts_complete"]:
            if fingerprint(await load_manifest(location)) != plan["manifest_sha256"]:
                result["complete_snapshot_scope"] = False
                result["manifest_changed_during_scan"] = True
                atomic_json(args.output.with_suffix(args.output.suffix + ".checkpoint.json"), result)
                raise RuntimeError("Public snapshot changed during the scan; result is not a complete release")
        return result
    elif args.command == "index":
        from app.repository import ElasticRepository
        from app.statistics import assign_bucket
        repository = ElasticRepository()
        signature = hashlib.sha256(json.dumps([str(args.input.resolve()), args.input.stat().st_size,
                                              args.input.stat().st_mtime_ns, settings.elastic_index,
                                              args.sesoi, args.effect_type]).encode()).hexdigest()
        checkpoint = args.input.with_suffix(args.input.suffix + ".index-checkpoint.json")
        state = {"signature": signature, "records": 0}
        if args.resume and checkpoint.exists():
            state = json.loads(checkpoint.read_text())
            if state.get("signature") != signature:
                raise ValueError("Index checkpoint does not match input or settings")
        skipped = state["records"]
        batch = []
        merged = []
        try:
            await repository.ensure_index()
            for position, row in enumerate(read_jsonl(args.input)):
                if row.get("source") == "merged" and row.get("linked_papers"):
                    merged.append(row)
                if position < skipped:
                    continue
                row = {**row, **assign_bucket(row, sesoi=args.sesoi, effect_type=args.effect_type)}
                batch.append(row)
                if len(batch) >= args.batch_size:
                    await repository.bulk_upsert(batch)
                    state["records"] += len(batch)
                    atomic_json(checkpoint, state)
                    batch = []
            if batch:
                await repository.bulk_upsert(batch)
                state["records"] += len(batch)
                atomic_json(checkpoint, state)
            # A linked JSONL removes duplicate paper rows, but the index can still
            # contain papers from earlier runs. Persist canonical redirects after
            # every batch is durable, then checkpoint this migration separately.
            if state.get("links_version") != 1:
                await repository.persist_links(merged)
                state["links_version"] = 1
                atomic_json(checkpoint, state)
            return state
        finally:
            await repository.close()
    elif args.command == "pagerank":
        from app.ingest.pagerank import update_authority
        from app.repository import ElasticRepository
        repository = ElasticRepository()
        try:
            return await update_authority(
                repository,
                damping=args.damping,
                iterations=args.iterations,
                batch_size=args.batch_size,
            )
        finally:
            await repository.close()
    else:
        raise ValueError("Unsupported command")
    stage = args.command
    if args.command == "normalize":
        from app.ingest.openalex import NORMALIZER_VERSION
        stage += ":" + NORMALIZER_VERSION
    if args.command == "classify":
        from app.classifier import WEAK_CLASSIFIER_VERSION
        model_path = args.model or settings.classifier_model_path
        stage += ":" + (hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
                        if model_path else WEAK_CLASSIFIER_VERSION)
        # Trained models still use the weak lexicon for evidence and rare-class fallbacks.
        if model_path:
            stage += ":" + WEAK_CLASSIFIER_VERSION
    if args.command == "embed":
        stage += ":" + settings.embedding_model
    return transform_file(args.input, args.output, transform, resume=args.resume,
                          stage=stage, batch_size=args.batch_size)


def main():
    arguments = parser().parse_args()
    try:
        print(json.dumps(asyncio.run(run(arguments)), indent=2))
    except (ValueError, RuntimeError, FileExistsError, TypeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
