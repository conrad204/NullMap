"""Paired compression accuracy and real cold/warm cost measurements.

python -m app.benchmark compression --input data/normalized.jsonl --limit 20
python -m app.benchmark search --idea 'Does vitamin D reduce depression?'
"""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

import httpx

from app.config import Settings, settings
from app.ingest.__main__ import read_jsonl
from app.llm import LLMService, Usage

FIELDS = ("n", "estimate", "ci_low", "ci_high", "p_value", "effect_type", "outcome")


async def compression_benchmark(path: Path, limit: int) -> dict:
    config = Settings(**settings.model_dump())
    config.compression_enabled = True
    config.compression_validated = True  # Experiment only; never changes live configuration.
    if not config.ttc_api_key or not config.openai_api_key:
        raise ValueError("OpenAI and TTC keys are required for the paired benchmark")
    llm = LLMService(config)
    rows = []
    try:
        for study in read_jsonl(path):
            if len(rows) >= limit:
                break
            if (
                not study.get("abstract")
                or study.get("is_review")
                or study.get("source") != "openalex"
            ):
                continue
            pair = {"id": study["id"]}
            for compressed in (False, True):
                usage = Usage()
                label = "compressed" if compressed else "original"
                started = perf_counter()
                try:
                    result = await llm.extract(study, usage, compression=compressed)
                    pair[label] = {
                        "verified": True,
                        "facts": {key: result.get(key) for key in FIELDS},
                    }
                except Exception as exc:
                    pair[label] = {"verified": False, "failure": type(exc).__name__, "facts": {}}
                pair[label]["usage"] = usage.summary([study["abstract"]], config)
                pair[label]["wallMs"] = round((perf_counter() - started) * 1000)
            pair["exactFactAgreement"] = pair["original"]["facts"] == pair["compressed"]["facts"]
            rows.append(pair)
    finally:
        await llm.close()
    passed = sum(
        r["original"]["verified"] and r["compressed"]["verified"] and r["exactFactAgreement"]
        for r in rows
    )
    return {
        "kind": "paired_compression",
        "studies": len(rows),
        "verifiedExactAgreements": passed,
        "agreementRate": passed / len(rows) if rows else None,
        "enableRecommended": bool(rows) and passed == len(rows) and config.ttc_input_price > 0,
        "limitation": "Agreement with uncompressed model extraction is not human-validated clinical accuracy. Review every difference and include actual TTC pricing before enabling compression.",
        "rows": rows,
    }


async def search_benchmark(idea: str, url: str) -> dict:
    rows = []
    async with httpx.AsyncClient(timeout=300) as client:
        for label in ("first", "repeat"):
            started = perf_counter()
            response = await client.post(
                f"{url.rstrip('/')}/search", json={"idea": idea, "sesoi": 0.2}
            )
            response.raise_for_status()
            result = response.json()
            rows.append(
                {
                    "run": label,
                    "wallMs": round((perf_counter() - started) * 1000),
                    "matched": result["totalScanned"],
                    "retrieval": result["retrieval"],
                    "costs": result["costs"],
                    "warnings": result["warnings"],
                }
            )
    return {
        "kind": "measured_repeated_search",
        "idea": idea,
        "runs": rows,
        "note": "The first run is cold only for documents not previously extracted. Inspect cache hits.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    compression = sub.add_parser("compression")
    compression.add_argument("--input", type=Path, required=True)
    compression.add_argument("--limit", type=int, default=20)
    search = sub.add_parser("search")
    search.add_argument("--idea", required=True)
    search.add_argument("--url", default="http://127.0.0.1:8000")
    for command in (compression, search):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if getattr(args, "limit", 1) <= 0:
        parser.error("--limit must be positive")
    result = asyncio.run(
        compression_benchmark(args.input, args.limit)
        if args.command == "compression"
        else search_benchmark(args.idea, args.url)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(f"Benchmark written to {args.output}")


if __name__ == "__main__":
    main()
