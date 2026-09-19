ClinicalTrials.gov fixture provenance

`ctgov_vital_live.json` is the complete public API v2 response for NCT01169259,
downloaded during implementation on 2026-09-19 from
https://clinicaltrials.gov/api/v2/studies/NCT01169259.

The first two primary outcomes and their selected comparisons were inspected
against the source JSON: invasive cancer, active vitamin D versus vitamin D
placebo, HR 0.96 (95% CI 0.88–1.06), p=0.47; major cardiovascular events,
HR 0.97 (95% CI 0.85–1.12), p=0.69. Each comparison has N=25,871.
Four factorial groups must not be added together, which would double-count N.
This is a schema/number check by the implementing agent, not independent human
clinical review.

`ctgov_cases.json` contains **20 synthetic edge cases**, each applied to an
explicit minimal v2-shaped record in `test_ingest.py`. They exercise zero versus
missing values, p-value inequalities, supported effect scales, incomplete and
non-95% intervals, enrollment type, status and partial dates. These are NOT 20
hand-reviewed live trials. Additional tests cover primary/secondary selection,
control groups, RESULT-only publication links, and deterministic deduplication.

Before a sponsor submission claiming 20 hand-checked trials, have a human review
20 live records and save the reviewed expectations and reviewer attribution.
