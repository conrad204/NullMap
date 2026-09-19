"""Scientific integration contracts across retrieval, linking, extraction and reporting."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.llm import Usage, indexed_to_extraction, validate_extraction
from app.models import Extraction, IndexedExtraction, Pico, SearchRequest
from app.pipeline import SearchPipeline
from app.statistics import assign_bucket


@pytest.fixture(autouse=True)
def inline_worker(monkeypatch):
    # These integration tests verify scientific contracts, not thread scheduling.
    # Keep CPU work deterministic and avoid executor shutdown in restricted CI.
    async def execute(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr("app.pipeline.asyncio.to_thread", execute)


def config(**kwargs):
    return Settings(
        _env_file=None, openai_api_key="", embeddings_enabled=False, **kwargs
    )


def paper(identifier="paper", **kwargs):
    return {
        "id": identifier,
        "source": "openalex",
        "title": "Vitamin D trial",
        "abstract": "A randomized trial studied depression outcomes.",
        "authors": [],
        "year": 2025,
        "population": "Adults",
        "intervention": "Vitamin D",
        "comparator": "Placebo",
        "outcome": "Depression severity",
        "result_label": "null",
        "effect_type": None,
        "estimate": None,
        "ci_low": None,
        "ci_high": None,
        "pmids": [],
        "nct_ids": [],
        **kwargs,
    }


def pico(**kwargs):
    return Pico(
        population="Adults",
        intervention="Vitamin D",
        comparator="Placebo",
        outcome="Depression severity",
        synonyms=[],
        studyDesigns=["RCT"],
        sesoi=0.2,
        sesoiRationale="Editable planning assumption",
        effectType="SMD",
        **kwargs,
    )


class MemoryRepository:
    def __init__(self, documents=(), hits=None, aggregation=None):
        self.documents = {row["id"]: deepcopy(row) for row in documents}
        self.hit_ids = hits if hits is not None else list(self.documents)
        self.fixed_aggregation = aggregation
        self.cache_writes = []
        self.link_writes = []
        self.aggregate_query = None
        self.aggregate_parameters = None
        self.read_batches = []

    async def ensure_index(self):
        pass

    async def get(self, identifier):
        row = self.documents.get(identifier)
        return deepcopy(row) if row else None

    async def get_many(self, identifiers):
        self.read_batches.append(list(identifiers))
        return [
            deepcopy(self.documents[identifier])
            for identifier in identifiers
            if identifier in self.documents
        ]

    async def retrieve(self, query, vector):
        return [deepcopy(self.documents[identifier]) for identifier in self.hit_ids], "bm25"

    async def registry_sweep(self, query):
        return [deepcopy(row) for row in self.documents.values() if row.get("source") == "ctgov"]

    async def cache_extraction(self, identifier, extraction):
        self.cache_writes.append((identifier, deepcopy(extraction)))
        self.documents[identifier].update(extraction)

    async def bulk_upsert(self, documents):
        for row in documents:
            self.documents[row["id"]] = deepcopy(row)

    async def persist_links(self, documents):
        self.link_writes.extend(deepcopy(documents))
        for row in documents:
            self.documents[row["id"]] = deepcopy(row)
            for linked in row.get("linked_papers", []):
                if linked["id"] in self.documents:
                    self.documents[linked["id"]]["record_kind"] = "linked_publication"

    async def aggregate(self, query, sesoi, effect_type):
        self.aggregate_query = query
        self.aggregate_parameters = (sesoi, effect_type)
        if self.fixed_aggregation is not None:
            return deepcopy(self.fixed_aggregation)
        counts = dict.fromkeys(
            ("effect", "credible_null", "inconclusive", "failed", "unreported"), 0
        )
        rows = [
            row
            for row in self.documents.values()
            if not row.get("is_review") and row.get("record_kind") != "linked_publication"
        ]
        for row in rows:
            counts[assign_bucket(row, sesoi, effect_type)["bucket"]] += 1
        return {
            "total": len(rows),
            "bucketCounts": counts,
            "yearCounts": [],
            "nullTerms": [],
            "fileDrawer": {"completed": 0, "unreported": 0, "overdue": 0, "share": None},
            "spin": {"eligible": 0, "disagreements": 0},
        }


class FakeLLM:
    def __init__(self, parsed=None, extraction=None, extraction_error=None, parse_error=None):
        self.parsed = parsed or pico()
        self.extraction = extraction or {}
        self.extraction_error = extraction_error
        self.parse_error = parse_error
        self.extract_calls = 0
        self.narrations = []

    async def parse(self, request, usage):
        if self.parse_error:
            raise self.parse_error
        return self.parsed

    async def extract(self, study, usage):
        self.extract_calls += 1
        if self.extraction_error:
            raise self.extraction_error
        return deepcopy(self.extraction)

    async def narrate(self, table, usage):
        self.narrations.append(deepcopy(table))
        return SimpleNamespace(summary="The structured evidence remains uncertain.", drivers=[])


def test_empty_no_key_search_returns_coverage_gap_without_fabricated_probability():
    async def exercise():
        repo, llm = MemoryRepository(), FakeLLM(parse_error=RuntimeError("no configured key"))
        pipeline = SearchPipeline(repo, llm, config())
        stages = []

        async def progress(event):
            stages.append(event["stage"])

        result = await pipeline.search(
            SearchRequest(idea="Does vitamin D reduce depression?"), progress
        )
        assert result["totalScanned"] == 0 and result["papers"] == []
        assert result["estimate"]["pSuccess"] is None
        assert result["estimate"]["expectedValue"] is None
        assert result["statistics"]["pools"] == []
        assert "coverage gap" in result["summary"]
        assert llm.extract_calls == 0 and llm.narrations == []
        assert result["costs"]["calls"] == 0
        assert stages == ["keywords", "searching", "classifying", "estimating"]

    asyncio.run(exercise())


def test_full_match_counts_are_not_replaced_by_retrieved_page_counts():
    aggregate = {
        "total": 120,
        "bucketCounts": {
            "effect": 50,
            "credible_null": 20,
            "inconclusive": 30,
            "failed": 10,
            "unreported": 10,
        },
        "yearCounts": [],
        "nullTerms": [],
        "fileDrawer": {"completed": 60, "unreported": 20, "overdue": 10, "share": 1 / 3},
        "spin": {"eligible": 0, "disagreements": 0},
    }
    repo = MemoryRepository([paper()], aggregation=aggregate)
    result = asyncio.run(
        SearchPipeline(repo, FakeLLM(), config()).search(
            SearchRequest(idea="Does vitamin D reduce depression?")
        )
    )
    assert len(result["papers"]) == 1
    assert result["totalScanned"] == 120
    assert result["bucketCounts"] == aggregate["bucketCounts"]
    assert result["statistics"]["fileDrawer"] == aggregate["fileDrawer"]
    assert result["estimate"]["pSuccess"] is None
    assert "120 primary studies" in result["summary"]


def test_review_only_retrieval_does_not_deny_primary_matches_beyond_the_page():
    aggregate = {
        "total": 12,
        "bucketCounts": {
            "effect": 0,
            "credible_null": 0,
            "inconclusive": 12,
            "failed": 0,
            "unreported": 0,
        },
        "yearCounts": [],
        "nullTerms": [],
        "fileDrawer": {"completed": 0, "unreported": 0, "share": None},
        "spin": {"eligible": 0, "disagreements": 0},
    }
    repo = MemoryRepository([paper("review", is_review=True)], aggregation=aggregate)
    result = asyncio.run(
        SearchPipeline(repo, FakeLLM(), config()).search(
            SearchRequest(idea="Does vitamin D reduce depression?")
        )
    )
    assert result["papers"] == [] and result["totalScanned"] == 12
    assert "No indexed primary studies matched" not in result["summary"]
    assert "12" in result["summary"]


def test_linked_paper_and_registry_count_once_and_preserve_registry_numbers():
    published = paper(
        "publication",
        pmids=["123"],
        nct_ids=["NCT00000001"],
        result_label="positive",
        estimate=0.5,
        ci_low=0.3,
        ci_high=0.7,
        effect_type="SMD",
        n=999,
    )
    registered = paper(
        "trial",
        source="ctgov",
        pmids=[],
        result_pmids=["123"],
        nct_ids=["NCT00000001"],
        estimate=0.02,
        ci_low=-0.1,
        ci_high=0.14,
        effect_type="SMD",
        n=200,
    )
    repo = MemoryRepository([published, registered])
    result = asyncio.run(
        SearchPipeline(repo, FakeLLM(), config()).search(
            SearchRequest(idea="Does vitamin D reduce depression?")
        )
    )
    assert result["totalScanned"] == 1 and len(result["papers"]) == 1
    row = result["papers"][0]
    assert row["id"] == "trial" and row["sampleSize"] == 200
    assert row["verdict"] == "credible_null"
    assert row["effectSize"]["value"] == 0.02
    assert len(repo.link_writes) == 1
    assert "trial" in str(repo.aggregate_query)


def test_publication_covering_two_trials_does_not_collapse_distinct_trial_rows():
    publication = paper("publication", pmids=["123"], nct_ids=["NCT00000001", "NCT00000002"])
    trials = [
        paper(
            f"trial-{i}",
            source="ctgov",
            result_pmids=["123"],
            nct_ids=[f"NCT0000000{i}"],
            n=100 * i,
        )
        for i in [1, 2]
    ]
    repo = MemoryRepository([publication, *trials])
    result = asyncio.run(
        SearchPipeline(repo, FakeLLM(), config()).search(
            SearchRequest(idea="Does vitamin D reduce depression?")
        )
    )
    assert result["totalScanned"] == 2
    assert {row["id"] for row in result["papers"]} == {"trial-1", "trial-2"}
    assert {row["sampleSize"] for row in result["papers"]} == {100, 200}


def test_registry_and_review_rows_never_trigger_paper_extraction():
    async def exercise():
        rows = [
            paper("review", is_review=True),
            paper("registry", source="ctgov"),
            paper("merged", source="merged", estimate=0.1),
        ]
        llm, repo = FakeLLM(), MemoryRepository(rows)
        settings = config()
        settings.openai_api_key = "test"
        pipeline = SearchPipeline(repo, llm, settings)
        for row in rows:
            assert await pipeline.extract_one(row, Usage(), []) == row
        assert llm.extract_calls == 0

    asyncio.run(exercise())


def test_rejected_extraction_is_cached_as_text_only_without_repeated_payment():
    async def exercise():
        row = paper()
        repo, llm = (
            MemoryRepository([row]),
            FakeLLM(extraction_error=ValueError("unsupported quote")),
        )
        settings = config()
        settings.openai_api_key = "test"
        pipeline = SearchPipeline(repo, llm, settings)
        usage, warnings = Usage(), []
        first = await pipeline.extract_one(row, usage, warnings)
        second = await pipeline.extract_one(row, usage, warnings)
        assert first["extraction_status"] == second["extraction_status"] == "rejected"
        assert first["evidence_tier"] == "text_only"
        assert first["bucket"] == "inconclusive"
        assert llm.extract_calls == 1 and usage.extraction_cache_hits == 1
        assert len(repo.cache_writes) == 1

    asyncio.run(exercise())


def test_failed_new_version_keeps_complete_prior_verified_facts_and_provenance():
    async def exercise():
        row = paper(
            effect_type="SMD",
            estimate=0.02,
            ci_low=-0.1,
            ci_high=0.14,
            extracted_at="2026-09-01T00:00:00Z",
            extraction_version="v1",
            extraction_status="verified",
            extraction_evidence={"estimate": "prior quote"},
        )
        repo, llm = (
            MemoryRepository([row]),
            FakeLLM(extraction_error=ValueError("unsupported quote")),
        )
        settings = config(extraction_cache_version="v2")
        settings.openai_api_key = "test"
        warnings = []
        result = await SearchPipeline(repo, llm, settings).extract_one(row, Usage(), warnings)
        assert result["estimate"] == 0.02 and result["outcome"] == row["outcome"]
        assert result["extraction_status"] == "retained_verified"
        assert result["extraction_facts_version"] == "v1"
        assert result["extraction_version"] == "v2"
        assert any("previous verified" in warning for warning in warnings)

    asyncio.run(exercise())


def test_new_extraction_does_not_relabel_old_endpoint_numbers():
    async def exercise():
        row = paper(
            effect_type="SMD",
            estimate=0.02,
            ci_low=-0.1,
            ci_high=0.14,
            extracted_at="2026-09-01T00:00:00Z",
            extraction_version="v1",
            extraction_status="verified",
        )
        fresh = {
            **validate_extraction(
                extraction(outcome={"value": "Fatigue severity", "quote": "Fatigue was measured."}),
                "Fatigue was measured.",
            ),
            "outcome": "Fatigue severity",
            "extracted_at": "2026-09-19T00:00:00Z",
            "extraction_version": "v2",
            "extraction_status": "verified",
            "extraction_evidence": {"outcome": "Fatigue was measured."},
        }
        repo, llm = MemoryRepository([row]), FakeLLM(extraction=fresh)
        settings = config(extraction_cache_version="v2")
        settings.openai_api_key = "test"
        result = await SearchPipeline(repo, llm, settings).extract_one(row, Usage(), [])
        assert result["outcome"] == "Fatigue severity"
        assert result.get("estimate") is None
        assert result.get("ci_low") is None and result.get("ci_high") is None
        assert result["bucket"] == "inconclusive"

    asyncio.run(exercise())


def test_narration_receives_computed_structured_table_without_abstracts():
    rows = [
        paper(
            f"pool-{i}",
            abstract="UNTRUSTED_ABSTRACT_CONTENT must never enter narration.",
            effect_type="SMD",
            estimate=0.05,
            ci_low=-0.05,
            ci_high=0.15,
            outcome_unit="SD",
            n=200,
        )
        for i in range(3)
    ]
    settings = config(extraction_limit=0)
    settings.openai_api_key = "test"
    llm, repo = FakeLLM(), MemoryRepository(rows)
    result = asyncio.run(
        SearchPipeline(repo, llm, settings).search(
            SearchRequest(idea="Does vitamin D reduce depression?")
        )
    )
    assert llm.extract_calls == 0 and len(llm.narrations) == 1
    table = llm.narrations[0]
    assert "UNTRUSTED_ABSTRACT_CONTENT" not in str(table)
    assert table["counts"] == result["bucketCounts"]
    assert table["assurance"] is not None
    assert result["estimate"]["pSuccess"] is not None


def test_insufficient_numeric_evidence_uses_computed_summary_not_model_interpretation():
    settings = config(extraction_limit=0)
    settings.openai_api_key = "test"
    llm = FakeLLM()
    result = asyncio.run(
        SearchPipeline(MemoryRepository([paper(result_label="positive")]), llm, settings).search(
            SearchRequest(idea="Does vitamin D reduce depression?")
        )
    )
    assert llm.narrations == []
    assert "assurance and expected value are not estimated" in result["summary"]
    assert result["papers"][0]["evidenceTier"] == "text_only"


def test_hidden_review_reference_resolves_to_distinct_canonical_trials():
    async def exercise():
        review = paper("review", is_review=True, referenced_works=["publication"])
        hidden = paper(
            "publication",
            record_kind="linked_publication",
            canonical_ids=["trial-1", "trial-2"],
            embedding=[1.0, 0.0],
        )
        trials = [
            paper(f"trial-{i}", source="merged", embedding=[1.0, 0.0], nct_ids=[f"NCT0000000{i}"])
            for i in [1, 2]
        ]
        repo = MemoryRepository([review, hidden, *trials])
        result = await SearchPipeline(repo, FakeLLM(), config()).expand([review], [1.0, 0.0], [])
        assert {row["id"] for row in result} == {"trial-1", "trial-2"}
        assert all(row.get("record_kind") != "linked_publication" for row in result)

    asyncio.run(exercise())


def test_reference_expansion_filters_low_similarity_and_duplicate_references():
    async def exercise():
        review = paper("review", is_review=True, referenced_works=["near", "far", "near"])
        near = paper("near", embedding=[1.0, 0.0])
        far = paper("far", embedding=[0.0, 1.0])
        repo = MemoryRepository([review, near, far])
        result = await SearchPipeline(repo, FakeLLM(), config()).expand([review], [1.0, 0.0], [])
        assert [row["id"] for row in result] == ["near"]
        assert repo.read_batches[0] == ["near", "far"]

    asyncio.run(exercise())


def test_reference_expansion_preserves_condition_screening_after_vector_ranking():
    async def exercise():
        review = paper("review", is_review=True, referenced_works=["knee", "ankle", "weak"])
        repo = MemoryRepository(
            [
                review,
                paper("knee", embedding=[0.9, 0.1]),
                paper("ankle", embedding=[1.0, 0.0]),
                paper("weak", embedding=[0.4, 0.9165]),
            ]
        )
        repo.screen_population = AsyncMock(return_value={"knee"})
        query_pico = {"population": "patients with knee osteoarthritis"}
        result = await SearchPipeline(repo, FakeLLM(), config()).expand(
            [review], [1.0, 0.0], [], query_pico
        )
        assert [row["id"] for row in result] == ["knee"]
        repo.screen_population.assert_awaited_once_with(["knee", "ankle"], query_pico)

    asyncio.run(exercise())


def test_missing_snapshot_references_never_trigger_external_fetch(monkeypatch):
    import httpx

    async def no_network(*args, **kwargs):
        pytest.fail("Search must resolve OpenAlex references only from the snapshot index")

    monkeypatch.setattr(httpx.AsyncClient, "get", no_network)

    async def exercise():
        review = paper("review", is_review=True, referenced_works=["known", "missing"])
        known = paper("known", embedding=[1.0, 0.0])
        warnings = []
        result = await SearchPipeline(MemoryRepository([review, known]), FakeLLM(), config()).expand(
            [review], [1.0, 0.0], warnings
        )
        assert [row["id"] for row in result] == ["known"]
        assert any("1 review references" in message for message in warnings)

    asyncio.run(exercise())


def extraction(**kwargs):
    return Extraction.model_validate({**dict.fromkeys(Extraction.model_fields), **kwargs})


def test_p_value_operator_is_attached_to_the_extracted_p_not_another_endpoint():
    text = "Secondary outcome p = 0.4; the primary outcome p < 0.05."
    result = validate_extraction(extraction(p_value={"value": 0.05, "quote": text}), text)
    assert result["p_value_operator"] == "<"


def test_one_sided_interval_cannot_be_used_as_two_sided_equivalence_evidence():
    text = "The difference was 0.10, with a one-sided 95% interval 0.05 to 0.15."
    extracted = validate_extraction(
        extraction(
            estimate={"value": 0.1, "quote": text},
            ci_low={"value": 0.05, "quote": text},
            ci_high={"value": 0.15, "quote": text},
            ci_level={"value": 0.95, "quote": text},
            effect_type={"value": "MD", "quote": text},
        ),
        text,
    )
    assert assign_bucket(extracted, sesoi=0.2, effect_type="MD")["bucket"] != "credible_null"


def test_indexed_evidence_uses_exact_original_sentence_and_rejects_out_of_bounds():
    sentences = ["Background text.", "Among 1,500 adults, the effect was −0.10."]
    data = {
        **dict.fromkeys(IndexedExtraction.model_fields),
        "n": {"value": 1500, "sentence_index": 1},
        "estimate": {"value": -0.1, "sentence_index": 1},
    }
    converted = indexed_to_extraction(IndexedExtraction.model_validate(data), sentences)
    assert converted.estimate.quote == sentences[1]
    validated = validate_extraction(converted, " ".join(sentences))
    assert validated["estimate"] == -0.1 and validated["n"] == 1500
    data["n"]["sentence_index"] = 2
    with pytest.raises(ValueError, match="Invalid evidence sentence"):
        indexed_to_extraction(IndexedExtraction.model_validate(data), sentences)


def test_indexed_evidence_still_rejects_fabricated_value_in_real_sentence():
    data = {
        **dict.fromkeys(IndexedExtraction.model_fields),
        "n": {"value": 9000, "sentence_index": 0},
    }
    converted = indexed_to_extraction(
        IndexedExtraction.model_validate(data), ["There were 200 participants."]
    )
    with pytest.raises(ValueError, match="Number absent"):
        validate_extraction(converted, "There were 200 participants.")
