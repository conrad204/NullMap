import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest

from app.ingest import flatten_trial, link_studies, normalize_work, reconstruct_abstract
from app.ingest.__main__ import parser, run, transform_file
from app.ingest.fetch import fetch_pages, request_json
from app.ingest.snapshot import scan_snapshot

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "ctgov_cases.json").read_text())


def trial():
    return {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Test intervention"},
            "statusModule": {"overallStatus": "COMPLETED", "primaryCompletionDateStruct": {"date": "2020-06-20"}},
            "designModule": {"studyType": "INTERVENTIONAL", "enrollmentInfo": {"count": 200, "type": "ACTUAL"}},
            "conditionsModule": {"conditions": ["Depression"]},
            "armsInterventionsModule": {"armGroups": [
                {"label": "Treatment", "type": "EXPERIMENTAL"},
                {"label": "Placebo", "type": "PLACEBO_COMPARATOR"}],
                "interventions": [{"name": "Vitamin D"}]},
            "referencesModule": {"references": [{"pmid": "1234", "type": "BACKGROUND"}]},
        },
        "hasResults": True,
        "resultsSection": {"outcomeMeasuresModule": {"outcomeMeasures": [{
            "type": "PRIMARY", "title": "Depression symptoms", "unitOfMeasure": "scale points",
            "groups": [{"id": "OG000", "title": "Treatment"}, {"id": "OG001", "title": "Placebo"}],
            "denoms": [{"units": "Participants", "counts": [
                {"groupId": "OG000", "value": "80"}, {"groupId": "OG001", "value": "90"}]}],
            "analyses": [{"groupIds": ["OG000", "OG001"], "paramType": "Mean Difference",
                          "paramValue": "0.2", "pValue": "0.2", "ciLowerLimit": "-0.3",
                          "ciUpperLimit": "0.7", "ciPctValue": "95", "ciNumSides": "TWO_SIDED"}]
        }]}},
    }


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_twenty_synthetic_registry_cases(case):
    record = trial()
    outcome = record["resultsSection"]["outcomeMeasuresModule"]["outcomeMeasures"][0]
    outcome["analyses"][0].update(case.get("analysis", {}))
    record["protocolSection"]["statusModule"].update(case.get("status", {}))
    if "enrollment" in case:
        record["protocolSection"]["designModule"]["enrollmentInfo"] = case["enrollment"]
    if case.get("no_denominators"):
        outcome.pop("denoms")
    if case.get("no_results"):
        record.pop("resultsSection")
        record["hasResults"] = False
    flattened = flatten_trial(record)
    for field, expected in case["expected"].items():
        assert flattened[field] == expected


def test_live_vital_primary_comparison_and_factorial_denominators():
    result = flatten_trial(json.loads((FIXTURES / "ctgov_vital_live.json").read_text()))
    assert result["id"] == "NCT01169259"
    assert result["estimate"] == 0.96
    assert result["ci_low"] == 0.88
    assert result["ci_high"] == 1.06
    assert result["p_value"] == 0.47
    assert result["ci_level"] == 0.95
    assert result["effect_type"] == "HR"
    assert result["intervention"] == "Active Vitamin D"
    assert result["comparator"] == "Vitamin D Placebo"
    assert result["n"] == 25871
    assert len(result["registry_analyses"]) >= 4
    assert "analyses[0]" in result["numeric_source"]


def test_primary_only_and_result_references_only():
    record = trial()
    outcomes = record["resultsSection"]["outcomeMeasuresModule"]["outcomeMeasures"]
    secondary = copy.deepcopy(outcomes[0])
    secondary["type"] = "SECONDARY"
    secondary["analyses"][0]["paramValue"] = "99"
    outcomes.insert(0, secondary)
    references = record["protocolSection"]["referencesModule"]["references"]
    references.append({"type": "RESULT", "pmid": "5678"})
    result = flatten_trial(record)
    assert result["estimate"] == 0.2
    assert result["result_pmids"] == ["5678"]
    assert result["has_linked_publication"] is True
    assert result["outcome_index"] == 1


def test_unknown_has_results_and_single_arm_are_not_invented():
    record = trial()
    record.pop("hasResults")
    record.pop("resultsSection")
    record["protocolSection"]["designModule"]["designInfo"] = {"interventionModel": "SINGLE_GROUP"}
    result = flatten_trial(record)
    assert result["has_results"] is None
    assert result["has_control"] is False
    assert result["has_linked_publication"] is False
    assert result["pmids"] == []


def test_single_group_analysis_does_not_become_a_treatment_effect():
    record = trial()
    record["resultsSection"]["outcomeMeasuresModule"]["outcomeMeasures"][0]["analyses"][0]["groupIds"] = ["OG000"]
    result = flatten_trial(record)
    assert result["estimate"] is None
    assert result["registry_analyses"][0]["estimate"] == 0.2


def test_sparse_and_json_openalex_inverted_indexes():
    inverted = {"result": [1000000000], "A": [0, True, -1], "null": [4], "bad": ["2"]}
    assert reconstruct_abstract(inverted) == "A null result"
    assert reconstruct_abstract(json.dumps(inverted)) == "A null result"
    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract("not json") == ""
    assert reconstruct_abstract({"key": ["A", "result"], "value": [[0], [1]]}) == "A result"


def test_normalize_openalex_and_drop_missing_abstract():
    work = {"id": "https://openalex.org/W123", "title": "A systematic review", "publication_year": 2024,
            "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/12345/"},
            "abstract_inverted_index": {"Trial": [0], "nct01169259": [1]},
            "referenced_works": ["https://openalex.org/W456"]}
    normalized = normalize_work(work)
    assert normalized["id"] == "W123"
    assert normalized["is_review"] is True
    assert normalized["nct_ids"] == ["NCT01169259"]
    assert normalized["pmids"] == ["12345"]
    assert normalized["referenced_works"] == ["W456"]
    assert normalize_work({"id": "W123"}) is None


@pytest.mark.parametrize("title", [
    "Omega-3 supplements: a Scoping Review",
    "Depression interventions: a narrative review",
    "An umbrella review of exercise trials",
    "Knee interventions: a literature review",
    "A systematic review and meta-analysis",
    "Evidence from meta analyses of supplementation",
    "Clinical practice guidelines for knee osteoarthritis",
    "Consensus recommendations for clinical trial design",
    "Recommendations for treating depressive symptoms",
    "Guidelines on prevention of cognitive decline",
])
def test_nonprimary_titles_are_not_treated_as_original_trials(title):
    work = {"id": "W1", "title": title, "type": "article",
            "abstract_inverted_index": {"Trial": [0], "results": [1]}}
    assert normalize_work(work)["is_review"] is True


@pytest.mark.parametrize("title", [
    "Peer review improves reporting in a randomized trial",
    "Clinician review of laboratory results: a randomized trial",
    "An intervention to improve adherence to treatment recommendations",
])
def test_incidental_review_or_recommendation_words_do_not_hide_primary_studies(title):
    work = {"id": "W1", "title": title, "type": "article",
            "abstract_inverted_index": {"Trial": [0], "results": [1]}}
    assert normalize_work(work)["is_review"] is False


def test_linker_preserves_registry_numbers_and_ignores_background_and_reviews():
    registry = flatten_trial(trial())
    registry["result_pmids"] = ["5678"]
    paper = {"id": "W1", "source": "openalex", "title": "Positive paper", "abstract": "Significantly improved.",
             "pmids": ["5678"], "nct_ids": [], "estimate": 1.5, "result_label": "positive"}
    background = {"id": "W2", "source": "openalex", "pmids": ["1234"]}
    review = {"id": "W3", "source": "openalex", "is_review": True, "nct_ids": ["NCT00000001"]}
    rows = link_studies([registry, paper, background, review])
    assert len(rows) == 3
    merged = next(row for row in rows if row["id"] == registry["id"])
    assert merged["source"] == "merged"
    assert merged["estimate"] == 0.2
    assert merged["has_linked_publication"] is True
    assert merged["possible_abstract_spin"] is True
    assert merged["url"].endswith("NCT00000001")


def test_multitrial_paper_does_not_collapse_distinct_trials():
    first = flatten_trial(trial())
    second = {**copy.deepcopy(first), "id": "NCT00000002", "nct_ids": ["NCT00000002"]}
    paper = {"id": "W1", "source": "openalex", "nct_ids": [first["id"], second["id"]], "result_label": "null"}
    results = link_studies([first, second, paper])
    assert len(results) == 2
    assert all(row["source"] == "merged" for row in results)
    assert results[0]["nct_ids"] != results[1]["nct_ids"]


def test_protocol_nct_mention_is_not_proof_of_reporting():
    registry = flatten_trial(trial())
    protocol = {"id": "W1", "source": "openalex", "nct_ids": [registry["id"]],
                "result_label": "no_result_stated"}
    rows = link_studies([registry, protocol])
    assert len(rows) == 2
    assert rows[0]["has_linked_publication"] is False


def test_registry_effect_does_not_display_paper_claim_as_numeric_evidence():
    registry = flatten_trial(trial())
    paper = {"id": "W1", "source": "openalex", "nct_ids": [registry["id"]],
             "result_label": "positive", "evidence_span": "A different outcome improved."}
    merged = link_studies([registry, paper])[0]
    assert merged["evidence_span"] == ""
    assert merged["numeric_source"].startswith("resultsSection")
    assert merged["linked_papers"][0]["evidence_span"] == paper["evidence_span"]


def test_fetch_retry_checkpoint_and_resume(tmp_path):
    calls = []
    def handler(request):
        calls.append(dict(request.url.params))
        cursor = request.url.params.get("cursor")
        if cursor == "*":
            return httpx.Response(200, json={"results": [{"id": "W1"}], "meta": {"next_cursor": "next"}})
        return httpx.Response(200, json={"results": [{"id": "W2"}], "meta": {"next_cursor": None}})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            output = tmp_path / "raw.jsonl"
            await fetch_pages("openalex", output, limit=1, page_size=1, client=client)
            with output.open("a") as handle:
                handle.write('partial crash write')
            state = await fetch_pages("openalex", output, limit=2, page_size=1, resume=True, client=client)
            assert state["count"] == 2
            assert [json.loads(line)["id"] for line in output.read_text().splitlines()] == ["W1", "W2"]
            assert calls[-1]["cursor"] == "next"
    asyncio.run(scenario())


def test_transport_retry_redacts_secrets():
    attempts = []
    async def no_sleep(delay):
        pass
    def handler(request):
        attempts.append(1)
        return httpx.Response(429 if len(attempts) == 1 else 200, json={"ok": True}, headers={"retry-after": "0"})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await request_json(client, "https://example.com", {"api_key": "secret"}, sleep=no_sleep) == {"ok": True}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(401))) as client:
            with pytest.raises(RuntimeError) as exc:
                await request_json(client, "https://example.com", {"api_key": "secret"})
            assert "secret" not in str(exc.value)
    asyncio.run(scenario())


def test_transform_checkpoint_prevents_duplicate_rows(tmp_path):
    source, output = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    source.write_text('{"id":"1"}\n{"id":"2"}\n')
    state = transform_file(source, output, lambda rows: rows, resume=False, stage="test", batch_size=1)
    assert state["output_records"] == 2
    original_mtime = output.stat().st_mtime_ns
    assert transform_file(source, output, lambda rows: rows, resume=True, stage="test") == state
    assert len(output.read_text().splitlines()) == 2
    assert output.stat().st_mtime_ns == original_mtime


def test_citation_sort_is_sent_and_cannot_reuse_date_cursor(tmp_path):
    observed = []
    def handler(request):
        observed.append(request.url.params["sort"])
        return httpx.Response(200, json={"results": [{"id": "W1"}], "meta": {"next_cursor": "next"}})

    async def scenario():
        output = tmp_path / "cited.jsonl"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_pages("openalex", output, sort="cited_by_count:desc", limit=1, client=client)
            original_mtime = output.stat().st_mtime_ns
            await fetch_pages("openalex", output, sort="cited_by_count:desc", limit=1,
                              resume=True, client=client)
            assert output.stat().st_mtime_ns == original_mtime
            assert observed == ["cited_by_count:desc"]
            with pytest.raises(ValueError, match="parameters"):
                await fetch_pages("openalex", output, limit=2, resume=True, client=client)
    asyncio.run(scenario())


def test_index_resume_archives_consumed_papers_even_with_legacy_completed_checkpoint(tmp_path, monkeypatch):
    import app.repository
    calls = {"upserts": 0, "links": []}

    class Repository:
        async def ensure_index(self):
            pass

        async def bulk_upsert(self, rows):
            calls["upserts"] += len(rows)

        async def persist_links(self, rows):
            calls["links"].append([row["id"] for row in rows])

        async def close(self):
            pass

    monkeypatch.setattr(app.repository, "ElasticRepository", Repository)
    path = tmp_path / "linked.jsonl"
    path.write_text(json.dumps({"id": "NCT00000001", "source": "merged", "linked_papers": [{"id": "W1"}]}) + "\n")
    arguments = parser().parse_args(["index", "--input", str(path), "--resume"])
    asyncio.run(run(arguments))
    assert calls == {"upserts": 1, "links": [["NCT00000001"]]}
    checkpoint = path.with_suffix(path.suffix + ".index-checkpoint.json")
    state = json.loads(checkpoint.read_text())
    state.pop("links_version")
    checkpoint.write_text(json.dumps(state))
    asyncio.run(run(arguments))
    assert calls == {"upserts": 1, "links": [["NCT00000001"], ["NCT00000001"]]}
    asyncio.run(run(arguments))
    assert len(calls["links"]) == 2


def test_snapshot_projection_schema_and_budget(tmp_path):
    import duckdb
    path = tmp_path / "works.parquet"
    connection = duckdb.connect()
    connection.execute("CREATE TABLE works AS SELECT 'W1' id, 'Null trial' title, 2024 publication_year, 'article' AS \"type\", 'medicine' topics, '{\"No\":[0],\"difference\":[1]}' abstract_inverted_index, 'ignored' huge_unused_column")
    connection.execute("COPY works TO ? (FORMAT PARQUET)", [str(path)])
    connection.close()
    with pytest.raises(ValueError, match="exceeding budget"):
        scan_snapshot([str(path)], tmp_path / "fail.jsonl", max_bytes=1)
    result = scan_snapshot([str(path)], tmp_path / "out.jsonl")
    assert result["records"] == 1
    assert "huge_unused_column" not in result["columns"]
    assert normalize_work(json.loads((tmp_path / "out.jsonl").read_text()))["abstract"] == "No difference"
