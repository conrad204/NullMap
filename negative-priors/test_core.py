"""Runnable end-to-end demo: parse two messy failures, index them, check a new protocol.

    docker run -d -p 9200:9200 -e discovery.type=single-node -e xpack.security.enabled=false \
        docker.elastic.co/elasticsearch/elasticsearch:8.15.0
    python test_core.py
"""

from __future__ import annotations

import json
import sys

from core import (
    check_prior_risk,
    connect,
    get_embedder,
    index_to_elastic,
    parse_raw_experiment,
    reset_index,
)
from schema import Experiment, PriorCheck

RAW_SNIPPETS: list[tuple[str, str]] = [
    (
        "nb-2026-03-11-kinase-panel.txt",
        """
        3/11 - bench notes, plate 4 (rerun of 3/06)
        Hypothesis: NP-114 inhibits JAK2 autophosphorylation in HEK293 lysate.
        compound: NP-114, dose: 25 uM, solvent: DMSO 1%, incubation: 90 min, temp: 37
        readout: pJAK2 AlphaLISA, n = 6 wells/condition
        Signal in treated wells was indistinguishable from vehicle control. delta = 2.1%
        which is within noise for this plate. p = 0.62, cohen's d = 0.04.
        Not a dosing problem - repeated at 25 uM after the 10 uM run on 3/06 gave the same
        thing. Calling this a null; no effect at any dose we can solubilize.
        """,
    ),
    (
        "csv-export-solubility-screen.csv",
        """
        run_id,note
        SOL-221,"Aim: formulate NP-114 at 100 uM in PBS for the whole-cell assay.
        compound: NP-114, concentration: 100 uM, buffer: PBS pH 7.4, temp: 25
        Compound crashed out of solution within ~4 min - visible precipitate on the meniscus,
        OD600: 0.42 from scatter alone. Viability: 31 percent in treated wells vs 94 in vehicle,
        almost certainly the precipitate shearing cells rather than pharmacology.
        DMSO stock was fine at 10 mM. Aqueous solubility is the blocker; assay unusable above 20 uM."
        """,
    ),
]

NEW_PROTOCOL = (
    "Proposed protocol: dose HEK293 cells with NP-114 at 100 uM in aqueous PBS and measure "
    "JAK2 phosphorylation to confirm target engagement."
)


def show_experiment(exp: Experiment) -> None:
    payload = exp.model_dump()
    vector = payload.pop("embedding")
    print(json.dumps(payload, indent=2))
    print(f"  embedding: dim={len(vector)} head={[round(x, 4) for x in vector[:5]]}\n")


def show_check(check: PriorCheck) -> None:
    print(f"query   : {check.query}")
    print(f"verdict : {check.verdict}   (risk_score={check.risk_score})\n")
    for label, group in (("INTERNAL (our own runs)", check.internal), ("OPENALEX (published)", check.published)):
        print(f"  {label}")
        if not group:
            print("    (none)")
        for m in group:
            stats = ", ".join(
                f"{k}={v}" for k, v in (("p", m.p_value), ("d", m.effect_size), ("year", m.year))
                if v is not None
            )
            print(f"    [{m.score:>6.4f}] {m.outcome_type:<13} {m.title[:88]}")
            if stats:
                print(f"             {stats}")
            if m.snippet:
                print(f"             {' '.join(m.snippet.split())[:110]}")
        print()


def main() -> int:
    client = connect()
    if not client.ping():
        print("elasticsearch is not reachable at localhost:9200 -- start it first (see docstring)")
        return 1

    print("=" * 100)
    print(f"negative-priors :: embedder={get_embedder().backend}  es={client.info()['version']['number']}")
    print("=" * 100)

    reset_index(client)

    print("\n[1] PARSE ------------------------------------------------------------------------\n")
    experiments = []
    for source, raw in RAW_SNIPPETS:
        exp = parse_raw_experiment(raw, source=source)
        experiments.append(exp)
        show_experiment(exp)

    print("[2] INDEX ------------------------------------------------------------------------\n")
    for exp in experiments:
        print(f"  indexed {index_to_elastic(client, exp)}  <- {exp.source}")

    print("\n[3] PRIOR CHECK ------------------------------------------------------------------\n")
    show_check(check_prior_risk(client, NEW_PROTOCOL, top_k=5))

    print("[4] CONTROL QUERY (unrelated proposal, should not flag) --------------------------\n")
    show_check(
        check_prior_risk(
            client,
            "Proposed protocol: measure circadian clock gene expression in zebrafish retina.",
            top_k=3,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
