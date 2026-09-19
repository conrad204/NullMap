from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .models import AnalysisConfig
from .pipeline import run_pipeline, studies_from_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evidence redundancy analysis")
    parser.add_argument("input_csv")
    parser.add_argument("--output-dir", default="analysis_output")
    parser.add_argument("--sesoi-low", type=float, required=True)
    parser.add_argument("--sesoi-high", type=float, required=True)
    parser.add_argument("--alpha", type=float, default=0.05)
    args = parser.parse_args()

    config = AnalysisConfig(alpha=args.alpha, sesoi_low=args.sesoi_low, sesoi_high=args.sesoi_high)
    report = run_pipeline(studies_from_csv(args.input_csv), config)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    pd.DataFrame(report["studies"]).to_csv(output / "study_audit.csv", index=False)
    pd.DataFrame(report["clusters_primary"]).to_json(output / "cluster_summary.json", orient="records", indent=2)
    print(f"Wrote {output / 'report.json'}, {output / 'study_audit.csv'}, and {output / 'cluster_summary.json'}")


if __name__ == "__main__":
    main()

