"""Public S3 corpus build. Run --plan first, then --run on the batch machine."""

import argparse
import asyncio
import json
from pathlib import Path

from app.config import settings
from app.ingest.__main__ import parser as ingest_parser
from app.ingest.__main__ import run as ingest
from app.ingest.fetch import atomic_json, fetch_pages
from app.ingest.materialize import materialize
from app.ingest.scope import CTGOV_QUERY, SCOPE_VERSION
from app.ingest.snapshot import fingerprint, load_manifest, plan_manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    action = result.add_mutually_exclusive_group()
    action.add_argument("--plan", action="store_true", help="Read only the manifest (the default)")
    action.add_argument("--run", action="store_true", help="Scan, normalize, embed, link and index")
    result.add_argument("--manifest", default=settings.openalex_snapshot_manifest)
    result.add_argument("--data-dir", type=Path, default=Path("data/research-s3"))
    result.add_argument("--max-files", type=int, help="Explicit partial snapshot for smoke tests")
    result.add_argument(
        "--largest-first",
        action="store_true",
        help="Order parts by descending size so a partial --max-files run covers more of the corpus",
    )
    result.add_argument("--max-snapshot-bytes", type=int, default=5_000_000_000)
    result.add_argument(
        "--max-records", type=int, help="Explicit partial record cap; default has no cap"
    )
    result.add_argument(
        "--registry-limit", type=int, help="Explicit partial registry cap; default exhausts cursor"
    )
    result.add_argument("--skip-registry", action="store_true")
    result.add_argument("--skip-embeddings", action="store_true")
    result.add_argument(
        "--work-ids", type=Path, help="Reference backfill from S3; requires --skip-registry"
    )
    result.add_argument("--batch-size", type=int, default=100)
    result.add_argument("--min-free-bytes", type=int, default=1_000_000_000)
    return result


async def bootstrap(args) -> dict:
    if args.work_ids and not args.skip_registry:
        raise ValueError("Reference backfill requires --skip-registry")
    if args.registry_limit is not None and args.registry_limit <= 0:
        raise ValueError("registry-limit must be positive")
    if args.batch_size <= 0:
        raise ValueError("batch-size must be positive")
    if args.max_records is not None and args.max_records <= 0:
        raise ValueError("max-records must be positive")
    if args.max_snapshot_bytes <= 0 or args.min_free_bytes < 0:
        raise ValueError("Snapshot budget must be positive and free-space threshold nonnegative")
    manifest = await load_manifest(args.manifest)
    plan = plan_manifest(manifest, max_files=args.max_files, largest_first=args.largest_first)
    summary = {k: v for k, v in plan.items() if k != "files"}
    summary.update(
        source="public_s3",
        scope="reference_backfill" if args.work_ids else SCOPE_VERSION,
        registry_scope="all statuses, study types and dates for the hypertension/kidney condition union",
        target_index=settings.elastic_index,
        note="Complete means all matches under the declared scope in the pinned snapshot, not all research worldwide.",
    )
    if not args.run:
        return summary
    if plan["physical_bytes_budgeted"] > args.max_snapshot_bytes:
        raise ValueError(
            f"Plan requires {plan['physical_bytes_budgeted']} physical bytes; set --max-snapshot-bytes after inspecting capacity on the batch host"
        )
    from app.embeddings import get_embedder
    from app.repository import ElasticRepository

    args.data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.data_dir / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError("Snapshot changed; use a new data directory for the new release")
    atomic_json(manifest_path, manifest)
    report_path = args.data_dir / "corpus-summary.json"
    summary.update(complete=False, stage="starting", stages={})
    atomic_json(report_path, summary)
    repository = ElasticRepository()
    embedder = (
        None
        if args.skip_embeddings
        else get_embedder(settings.embedding_model, settings.embedding_device)
    )

    def progress(stage):
        def emit(state):
            summary["stage"] = stage
            summary["stages"][stage] = state
            atomic_json(report_path, summary)
            print(json.dumps({"stage": stage, "records": state["records"]}), flush=True)

        return emit

    try:
        await repository.ensure_index()
        registry = None
        if not args.skip_registry:
            registry_path = args.data_dir / "ctgov.jsonl"
            registry = await fetch_pages(
                "ctgov",
                registry_path,
                query=CTGOV_QUERY,
                filters="",
                limit=args.registry_limit,
                resume=True,
            )
            summary["registry"] = registry
            await materialize(
                registry_path,
                repository,
                embedder=embedder,
                batch_size=args.batch_size,
                progress=progress("registry_index"),
            )
        paper_path = args.data_dir / "openalex.jsonl"
        commands = [
            "snapshot",
            "--manifest",
            str(manifest_path),
            "--output",
            str(paper_path),
            "--profile",
            "hypertension-kidney",
            "--resume",
            "--max-bytes",
            str(args.max_snapshot_bytes),
            "--min-free-bytes",
            str(args.min_free_bytes),
        ]
        for flag, value in (
            ("--max-files", args.max_files),
            ("--limit", args.max_records),
            ("--work-ids", args.work_ids),
        ):
            if value is not None:
                commands.extend([flag, str(value)])
        summary["stage"] = "snapshot_scan"
        atomic_json(report_path, summary)
        snapshot = await ingest(ingest_parser().parse_args(commands))
        summary["snapshot"] = snapshot
        if "://" in args.manifest and snapshot["selected_parts_complete"]:
            if fingerprint(await load_manifest(args.manifest)) != plan["manifest_sha256"]:
                snapshot["complete_snapshot_scope"] = False
                snapshot["manifest_changed_during_scan"] = True
                atomic_json(paper_path.with_suffix(".jsonl.checkpoint.json"), snapshot)
                raise RuntimeError(
                    "Public snapshot changed during the scan; result is not a complete release"
                )
        await materialize(
            paper_path,
            repository,
            embedder=embedder,
            batch_size=args.batch_size,
            progress=progress("snapshot_index"),
        )
        summary.update(
            stage="finished",
            complete=bool(
                snapshot["complete_snapshot_scope"] and registry is not None and registry["done"]
            ),
            index=await repository.health(),
        )
        if args.work_ids:
            summary["reference_scan_complete"] = snapshot["complete_snapshot_scope"]
        atomic_json(report_path, summary)
        return summary
    except Exception as exc:
        summary.update(stage="interrupted", complete=False, error=type(exc).__name__)
        atomic_json(report_path, summary)
        raise
    finally:
        await repository.close()


def main():
    args = parser().parse_args()
    try:
        print(json.dumps(asyncio.run(bootstrap(args)), indent=2))
    except (ValueError, RuntimeError, FileExistsError, TypeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
