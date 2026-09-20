import asyncio
import math
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from elastic_transport import ApiResponseMeta, NodeConfig
from elasticsearch import AuthorizationException, BadRequestError

from app.config import Settings
from app.repository import (
    BUCKETS,
    ElasticRepository,
    _prepare_document,
    bucket_runtime,
    filter_clauses,
    index_mapping,
    lexical_query,
    population_query,
    registry_citation_exemption_query,
)
from app.statistics import assign_bucket


def document(identifier="paper", **kwargs):
    return {
        "id": identifier,
        "source": "openalex",
        "title": "Vitamin D depression trial",
        "abstract": "Vitamin D improved depression in this randomized trial.",
        "year": 2025,
        "publication_date": None,
        "outcome": "Depression severity",
        "effect_type": "SMD",
        "estimate": 0.02,
        "ci_low": -0.1,
        "ci_high": 0.14,
        **kwargs,
    }


def test_mapping_includes_every_runtime_numeric_field_and_actual_spin_flag():
    fields = index_mapping()["properties"]
    for key in ("analysis_ci_low", "analysis_ci_high", "analysis_estimate"):
        assert fields[key]["type"] == "double"
    assert fields["analysis_effect_type"]["type"] == "keyword"
    assert fields["possible_abstract_spin"]["type"] == "boolean"
    assert fields["reporting_due_date"]["type"] == "date"
    assert fields["authority"]["type"] == "rank_feature"
    assert fields["pagerank"]["type"] == "rank_feature"


def test_authority_is_log_scaled_and_never_written_nonpositive():
    prepared = _prepare_document(document(cited_by_count=99))
    assert prepared["authority"] == pytest.approx(math.log1p(99))
    assert "authority" not in _prepare_document(document(cited_by_count=0))
    assert "authority" not in _prepare_document(document())
    # A refresh that drops the count must not leave a stale feature behind.
    stale = _prepare_document(document(), document(cited_by_count=0, authority=2.0))
    assert "authority" not in stale


def test_pagerank_survives_reingest_untouched():
    original = document(pagerank=1.23, cited_by_count=10)
    updated = _prepare_document(document(cited_by_count=99), original)
    assert updated["pagerank"] == 1.23
    assert updated["authority"] == pytest.approx(math.log1p(99))


def test_reingest_retains_verified_cache_only_for_unchanged_abstract():
    original = document(
        extracted_at="2026-09-01T00:00:00Z",
        extraction_status="verified",
        extraction_version="v1",
        extraction_evidence={"estimate": "0.02"},
    )
    source = document(estimate=None, ci_low=None, ci_high=None, cited_by_count=55)
    updated = _prepare_document(source, original)
    assert updated["estimate"] == 0.02
    assert updated["bucket"] == "credible_null"
    assert updated["extraction_status"] == "verified"
    assert updated["cited_by_count"] == 55
    changed = _prepare_document({**source, "abstract": "New abstract content."}, original)
    assert changed["estimate"] is None
    assert changed["extracted_at"] is None
    assert changed["analysis_ci_low"] is None
    assert changed["embedding"] is None


def test_missing_vector_on_source_refresh_keeps_existing_vector_update_untouched():
    original = document(embedding=[1.0, 0.0, 0.0])
    updated = _prepare_document(document(embedding=None), original)
    assert "embedding" not in updated


def test_publication_date_none_uses_real_year_and_unknown_year_stays_missing():
    assert _prepare_document(document())["publication_date"] == "2025-01-01"
    assert _prepare_document(document(year=None))["publication_date"] is None


def test_reingest_does_not_resurrect_linked_publication():
    original = document(record_kind="linked_publication", canonical_ids=["NCT1", "NCT2"])
    updated = _prepare_document(document(), original)
    assert updated["record_kind"] == "linked_publication"
    assert updated["canonical_ids"] == ["NCT1", "NCT2"]


def test_registry_refresh_retains_link_but_refreshes_new_registry_results():
    original = document(
        "trial",
        source="merged",
        has_linked_publication=True,
        linked_papers=[{"id": "paper"}],
        pmids=["123"],
        extracted_at="2026-09-01T00:00:00Z",
        extraction_status="verified",
    )
    incoming = document(
        "trial",
        source="ctgov",
        abstract="Registry text",
        estimate=0.5,
        ci_low=0.3,
        ci_high=0.7,
        numeric_source="resultsSection",
        pmids=[],
    )
    updated = _prepare_document(incoming, original)
    assert updated["source"] == "merged"
    assert updated["has_linked_publication"] is True
    assert updated["abstract"] == original["abstract"]
    assert updated["estimate"] == 0.5
    assert updated["pmids"] == ["123"]


def test_confirmed_missing_reports_and_overdue_dates_are_distinct():
    trial = document(
        "trial",
        source="ctgov",
        overall_status="COMPLETED",
        nct_ids=["NCT00000001"],
        has_results=False,
        has_linked_publication=False,
        primary_completion_date="2024-02-29",
    )
    prepared = _prepare_document(trial)
    assert prepared["reporting_missing"] is True
    assert prepared["reporting_due_date"] == "2025-02-28"
    unknown = _prepare_document({**trial, "has_linked_publication": None})
    assert unknown["reporting_missing"] is False


def test_vector_expansion_mget_explicitly_requests_vectors():
    client = SimpleNamespace(mget=AsyncMock(return_value={"docs": []}))
    repo = ElasticRepository(Settings(_env_file=None), client=client)
    asyncio.run(repo.get_many(["one", "one", "two"]))
    request = client.mget.call_args.kwargs
    assert len(request["docs"]) == 2
    assert request["docs"][0]["_source"] == {"exclude_vectors": False}


def error(error_type, message, status):
    meta = ApiResponseMeta(
        status=status,
        http_version="1.1",
        headers={},
        duration=0,
        node=NodeConfig(scheme="http", host="localhost", port=9200),
    )
    return error_type(message, meta=meta, body={"error": {"reason": message}})


def test_rrf_falls_back_only_for_license_failure_including_403():
    response = {"hits": {"hits": []}}
    client = SimpleNamespace(
        search=AsyncMock(
            side_effect=[
                error(AuthorizationException, "current license is non-compliant for [RRF]", 403),
                response,
                response,
                response,
            ]
        )
    )
    repo = ElasticRepository(Settings(_env_file=None), client=client)
    _, mode = asyncio.run(repo.retrieve(lexical_query({}, "vitamin d"), [1.0, 0.0, 0.0]))
    assert mode == "hybrid_client_rrf"
    # Three legs re-run client-side: BM25, kNN, and citation authority.
    assert client.search.call_count == 4


def test_hybrid_retriever_adds_an_authority_leg_scoped_to_the_query():
    client = SimpleNamespace(search=AsyncMock(return_value={"hits": {"hits": []}}))
    repo = ElasticRepository(Settings(_env_file=None), client=client)
    query = lexical_query({}, "vitamin d")
    _, mode = asyncio.run(repo.retrieve(query, [1.0, 0.0, 0.0]))
    assert mode == "hybrid_rrf"
    retrievers = client.search.call_args.kwargs["retriever"]["rrf"]["retrievers"]
    assert [next(iter(leg)) for leg in retrievers] == ["standard", "knn", "standard"]
    authority = retrievers[2]["standard"]["query"]
    assert authority["bool"]["filter"] == [query]
    fields = [clause["rank_feature"]["field"] for clause in authority["bool"]["should"]]
    assert fields == ["authority", "pagerank"]
    assert authority["bool"]["minimum_should_match"] == 1


def test_bm25_retrieval_is_fused_with_the_authority_ranking():
    seen = []

    async def search(**kwargs):
        seen.append(kwargs)
        return {"hits": {"hits": []}}

    repo = ElasticRepository(Settings(_env_file=None), client=SimpleNamespace(search=search))
    _, mode = asyncio.run(repo.retrieve({"match_all": {}}, None))
    assert mode == "bm25"
    assert len(seen) == 2
    assert seen[0]["query"] == {"match_all": {}}
    authority = seen[1]["query"]
    assert authority["bool"]["filter"] == [{"match_all": {}}]
    assert "rank_feature" in str(authority["bool"]["should"])


@pytest.mark.parametrize(
    "exception",
    [
        error(AuthorizationException, "unauthorized index permission", 403),
        error(BadRequestError, "malformed rrf retriever", 400),
    ],
)
def test_authorization_and_query_errors_are_not_silently_hidden(exception):
    client = SimpleNamespace(search=AsyncMock(side_effect=exception))
    repo = ElasticRepository(Settings(_env_file=None), client=client)
    with pytest.raises(type(exception)):
        asyncio.run(repo.retrieve({"match_all": {}}, [1.0, 0.0, 0.0]))
    assert client.search.call_count == 1


@pytest.mark.skipif(
    not os.getenv("NULLMAP_TEST_ELASTIC_URL"), reason="Opt-in real Elasticsearch test"
)
def test_real_elasticsearch_runtime_parity_cache_and_linked_counts():
    async def exercise():
        config = Settings(
            _env_file=None,
            elastic_url=os.environ["NULLMAP_TEST_ELASTIC_URL"],
            elastic_local=True,
            elastic_index=f"nullmap-test-{uuid4().hex}",
            embedding_dimensions=3,
        )
        repo = ElasticRepository(config)
        try:
            await repo.ensure_index()
            raw = [
                document("null"),
                document("effect", estimate=0.4, ci_low=0.1, ci_high=0.7),
                document("trivial", estimate=0.08, ci_low=0.02, ci_high=0.14),
                document("wide", ci_low=-0.6, ci_high=0.64),
                document("ninety", estimate=0, ci_low=-0.18, ci_high=0.18, ci_level=0.9),
                document("p", estimate=0.1, ci_low=None, ci_high=None, p_value=0.05),
                document(
                    "censored",
                    estimate=0.1,
                    ci_low=None,
                    ci_high=None,
                    p_value=0.01,
                    p_value_operator="<",
                    result_label="null",
                ),
                document("ratio", effect_type="OR", estimate=1.02, ci_low=0.9, ci_high=1.1),
                document("one_sided", ci_sides="ONE_SIDED"),
                document(
                    "text_positive",
                    estimate=None,
                    ci_low=None,
                    ci_high=None,
                    result_label="positive",
                ),
                document("text_null", estimate=None, ci_low=None, ci_high=None,
                         result_label="null"),
                # A quoted statement from the report outranks the lexicon label.
                document("stated_null", estimate=None, ci_low=None, ci_high=None,
                         result_label="positive", reported_result="null", has_control=True),
                # Read, "positive" quoted, but no control arm: a case report earns no verdict.
                document("case_report", estimate=None, ci_low=None, ci_high=None,
                         result_label="positive", reported_result="positive",
                         extraction_status="verified"),
                document("registry_stated", estimate=None, ci_low=None, ci_high=None,
                         source="merged", reported_result="null", nct_ids=["NCT00000009"]),
                # Arm-level summaries only: every derivable scale must agree with Python.
                document("arms_means", estimate=None, ci_low=None, ci_high=None, effect_type=None,
                         mean_intervention=10.1, mean_comparator=10.0, sd_intervention=4.0,
                         sd_comparator=4.0, n_intervention=900, n_comparator=900),
                document("arms_events", estimate=None, ci_low=None, ci_high=None, effect_type=None,
                         events_intervention=20, events_comparator=45, n_intervention=100,
                         n_comparator=100, result_label="positive"),
                # A reported HR stays visible to HR queries although SMD is the write-time scale.
                document("hr_with_arms", effect_type="HR", estimate=0.96, ci_low=0.88,
                         ci_high=1.06, events_intervention=793, events_comparator=824,
                         n_intervention=12927, n_comparator=12944),
                document("md_with_arms", effect_type="MD", estimate=0.1, ci_low=-0.3, ci_high=0.5,
                         mean_intervention=10.1, mean_comparator=10.0, sd_intervention=4.0,
                         sd_comparator=4.0, n_intervention=900, n_comparator=900),
                document("failed", is_retracted=True),
                document("review", is_review=True),
                document(
                    "overdue",
                    source="ctgov",
                    overall_status="COMPLETED",
                    has_results=False,
                    has_linked_publication=False,
                    nct_ids=["NCT00000001"],
                    primary_completion_date="2020-01-01",
                ),
                document(
                    "recent",
                    source="ctgov",
                    overall_status="COMPLETED",
                    has_results=False,
                    has_linked_publication=False,
                    nct_ids=["NCT00000002"],
                    primary_completion_date=datetime.now(UTC).date().isoformat(),
                ),
                document(
                    "unknown_reporting",
                    source="ctgov",
                    overall_status="COMPLETED",
                    has_results=False,
                    nct_ids=["NCT00000003"],
                    primary_completion_date="2020-01-01",
                ),
            ]
            assert await repo.bulk_upsert(raw) == len(raw)
            today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            scales = [(0.2, "SMD"), (0.05, "SMD"), (0.2, "logOR"), (1.25, "OR"), (1.25, "HR"),
                      (1.25, "RR"), (3.0, "MD"), (0.5, "MD")]
            for sesoi, scale in scales:
                runtime = bucket_runtime(sesoi, scale)
                response = await repo.client.search(
                    index=repo.index,
                    query={"match_all": {}},
                    size=100,
                    runtime_mappings=runtime,
                    fields=["query_bucket"],
                )
                actual = {
                    hit["_id"]: hit["fields"]["query_bucket"][0] for hit in response["hits"]["hits"]
                }
                expected = {}
                for row in raw:
                    verdict = assign_bucket(row, sesoi, scale)
                    reason = verdict["inconclusive_reason"]
                    expected[row["id"]] = verdict["bucket"] + (f":{reason}" if reason else "")
                # The script must agree on the bucket and on why a row is inconclusive.
                assert actual == expected
            aggregate = await repo.aggregate({"match_all": {}}, 0.2, "SMD")
            assert aggregate["total"] == len(raw) - 1
            assert sum(aggregate["bucketCounts"].values()) == aggregate["total"]
            assert set(aggregate["bucketCounts"]) == set(BUCKETS)
            assert (sum(aggregate["inconclusiveReasons"].values())
                    == aggregate["bucketCounts"]["inconclusive"] > 0)
            assert aggregate["fileDrawer"] == {
                "completed": 3,
                "unreported": 2,
                "overdue": 1,
                "share": 2 / 3,
            }
            # Cache survives a refresh, and changed abstracts invalidate its claims.
            await repo.cache_extraction(
                "null",
                {
                    "estimate": 0.01,
                    "ci_low": -0.05,
                    "ci_high": 0.07,
                    "extracted_at": today.isoformat(),
                    "extraction_status": "verified",
                    "extraction_version": "v1",
                },
            )
            await repo.bulk_upsert([document("null", estimate=None, ci_low=None, ci_high=None)])
            assert (await repo.get("null"))["estimate"] == 0.01
            # Distinct registry trials can be linked to the same paper without collapsing.
            paper = document("linked-paper", embedding=[1.0, 0.0, 0.0], pmids=["999"])
            await repo.bulk_upsert([paper])
            merged = [
                document(
                    f"canonical-{i}",
                    source="merged",
                    nct_ids=[f"NCT0000001{i}"],
                    has_linked_publication=True,
                    has_results=True,
                    pmids=["999"],
                    overall_status="COMPLETED",
                    possible_abstract_spin=i == 0,
                    linked_papers=[{"id": "linked-paper"}],
                )
                for i in range(2)
            ]
            await repo.persist_links(merged)
            archived = await repo.get("linked-paper")
            assert archived["record_kind"] == "linked_publication"
            assert archived["canonical_ids"] == ["canonical-0", "canonical-1"]
            canonical = await repo.get_many(["canonical-0", "canonical-1"])
            assert len(canonical) == 2 and all(row.get("embedding") for row in canonical)
            await repo.bulk_upsert([paper])
            assert (await repo.get("linked-paper"))["record_kind"] == "linked_publication"
            final = await repo.aggregate({"match_all": {}}, 0.2, "SMD")
            assert final["total"] == len(raw) - 1 + 2
            assert final["spin"] == {"eligible": 2, "disagreements": 1}
            hits, _mode = await repo.retrieve(
                lexical_query({}, "Vitamin D depression"), [1.0, 0.0, 0.0]
            )
            assert all(row["id"] != "linked-paper" for row in hits)
        finally:
            await repo.client.indices.delete(index=repo.index, ignore_unavailable=True)
            await repo.close()

    asyncio.run(exercise())


def test_lexical_query_requires_distinct_intervention_and_outcome_concepts():
    query = lexical_query(
        {
            "intervention": "Vitamin D supplementation",
            "outcome": "Change in depressive symptoms or depression severity",
            "population": "Adults",
            "synonyms": ["mood", "cholecalciferol"],
        },
        "Does vitamin D reduce depression?",
    )
    candidate = query["bool"]["should"][0]["bool"]
    assert len(candidate["must"]) == 2
    assert "mood" not in str(candidate["must"])
    assert "depress" in str(candidate["must"][1])
    assert "cholecalciferol" in str(candidate["must"][0])
    assert "Adults" not in str(candidate["must"])


@pytest.mark.skipif(
    not os.getenv("NULLMAP_TEST_ELASTIC_URL"), reason="Opt-in real Elasticsearch test"
)
def test_real_elasticsearch_separates_vitamin_d_depression_from_bone_and_cognition():
    async def exercise():
        settings = Settings(
            _env_file=None,
            elastic_url=os.environ["NULLMAP_TEST_ELASTIC_URL"],
            elastic_local=True,
            elastic_index=f"nullmap-test-{uuid4().hex}",
            embedding_dimensions=3,
        )
        repo = ElasticRepository(settings)
        try:
            rows = [
                document(
                    "depression",
                    title="Vitamin D for depressive disorder",
                    abstract="Vitamin D was studied in depressive disorder.",
                    outcome="Depression",
                ),
                document(
                    "bone",
                    title="Vitamin D and skeletal events in bone malignancy",
                    abstract="Vitamin D was studied in skeletal complications.",
                    outcome="Bone events",
                ),
                document(
                    "cognition",
                    title="Vitamin D and cognitive decline",
                    abstract="Vitamin D was studied in cognitive impairment.",
                    outcome="Cognition",
                ),
                document(
                    "unrelated_intervention",
                    title="Exercise reduces depression",
                    abstract="Exercise improved mood and depression.",
                    outcome="Depression",
                ),
                document(
                    "alias",
                    title="Cholecalciferol for depressed mood",
                    abstract="Cholecalciferol was studied in adults with depressed mood.",
                    outcome="Mood",
                ),
            ]
            await repo.bulk_upsert(rows)
            pico = {
                "intervention": "Vitamin D supplementation",
                "outcome": "Change in depressive symptoms or depression severity",
                "population": "Adults",
                "synonyms": ["mood", "cholecalciferol"],
            }
            query = lexical_query(pico, "Does vitamin D reduce depression?")
            hits, _ = await repo.retrieve(query, None)
            assert {row["id"] for row in hits} == {"depression", "alias"}
            aggregate = await repo.aggregate(query, 0.2, "SMD")
            assert aggregate["total"] == 2
            expanded = lexical_query(pico, "Does vitamin D reduce depression?", ["bone"])
            assert (await repo.aggregate(expanded, 0.2, "SMD"))["total"] == 3
        finally:
            await repo.client.indices.delete(index=repo.index, ignore_unavailable=True)
            await repo.close()

    asyncio.run(exercise())


def test_search_text_folds_accents_and_vitamin_formatting_without_changing_sources():
    original = document(
        title="Arthroscopic débridement",
        abstract="Vitamin D₃ and naïve patients.",
        intervention="VitaminD3",
        outcome="Dépression",
    )
    prepared = _prepare_document(original)
    assert prepared["title"] == original["title"]
    assert prepared["abstract"] == original["abstract"]
    assert prepared["intervention"] == original["intervention"]
    assert prepared["outcome"] == original["outcome"]
    assert "debridement" in prepared["search_text"]
    assert "vitamin d3" in prepared["search_text"]
    assert "depression" in prepared["search_text"]
    assert index_mapping()["properties"]["search_text"] == {"type": "text", "analyzer": "english"}


def test_extraction_cache_patch_refreshes_normalized_retrieval_text():
    original = document()
    client = SimpleNamespace(
        get=AsyncMock(return_value={"_id": original["id"], "_source": original}), update=AsyncMock()
    )
    repo = ElasticRepository(Settings(_env_file=None), client=client)
    asyncio.run(repo.cache_extraction(original["id"], {"outcome": "Dépression and fatigue"}))
    patch = client.update.call_args.kwargs["doc"]
    assert patch["outcome"] == "Dépression and fatigue"
    assert "depression and fatigue" in patch["search_text"]
    assert "abstract" not in patch


def aggregation_response(bucket_rows, direction_rows, total=None):
    counted = sum(row["doc_count"] for row in bucket_rows)
    return {
        "hits": {"total": {"value": total if total is not None else counted}},
        "aggregations": {
            "buckets": {"buckets": bucket_rows},
            "years": {"buckets": []},
            "nulls": {"sample": {"terms": {"buckets": []}}},
            "completed": {"doc_count": 0, "missing": {"doc_count": 0}, "overdue": {"doc_count": 0}},
            "effect_directions": {"directions": {"buckets": direction_rows}},
            "spin_candidates": {"doc_count": 0, "spin": {"doc_count": 0}},
        },
    }


def test_effect_directions_are_counted_over_the_full_match_set_and_partition_the_bucket():
    response = aggregation_response(
        [
            {"key": "effect", "doc_count": 6},
            {"key": "credible_null", "doc_count": 2},
            {"key": "inconclusive:wide_interval", "doc_count": 1},
        ],
        [
            {"key": "favours_intervention", "doc_count": 4},
            {"key": "favours_comparator", "doc_count": 1},
            {"key": "unclear", "doc_count": 1},
        ],
    )
    client = SimpleNamespace(search=AsyncMock(return_value=response))
    repo = ElasticRepository(Settings(_env_file=None), client=client)
    repo._index_ready = True
    aggregate = asyncio.run(repo.aggregate({"match_all": {}}, 0.2, "SMD"))
    assert aggregate["effectDirections"] == {
        "favoursIntervention": 4,
        "favoursComparator": 1,
        "unclear": 1,
    }
    assert sum(aggregate["effectDirections"].values()) == aggregate["bucketCounts"]["effect"]
    directions = client.search.call_args.kwargs["aggs"]["effect_directions"]
    assert directions["filter"] == {"term": {"query_bucket": "effect"}}
    assert directions["aggs"]["directions"]["terms"]["missing"] == "unclear"


def test_effects_without_a_stated_direction_are_folded_into_unclear_not_dropped():
    response = aggregation_response(
        [{"key": "effect", "doc_count": 5}],
        [
            {"key": "favours_intervention", "doc_count": 2},
            # A document with no result_direction arrives under the aggregation's missing value.
            {"key": "unclear", "doc_count": 3},
        ],
    )
    client = SimpleNamespace(search=AsyncMock(return_value=response))
    repo = ElasticRepository(Settings(_env_file=None), client=client)
    repo._index_ready = True
    aggregate = asyncio.run(repo.aggregate({"match_all": {}}, 0.2, "SMD"))
    assert aggregate["effectDirections"] == {
        "favoursIntervention": 2,
        "favoursComparator": 0,
        "unclear": 3,
    }
    assert sum(aggregate["effectDirections"].values()) == aggregate["bucketCounts"]["effect"] == 5


@pytest.mark.skipif(
    not os.getenv("NULLMAP_TEST_ELASTIC_URL"), reason="Opt-in real Elasticsearch test"
)
def test_real_elasticsearch_matches_accented_debridement_and_formatted_vitamin_d3():
    async def exercise():
        settings = Settings(
            _env_file=None,
            elastic_url=os.environ["NULLMAP_TEST_ELASTIC_URL"],
            elastic_local=True,
            elastic_index=f"nullmap-test-{uuid4().hex}",
            embedding_dimensions=3,
        )
        repo = ElasticRepository(settings)
        try:
            rows = [
                document(
                    "debridement",
                    title="Arthroscopic treatment of knee osteoarthritis",
                    abstract="Arthroscopic débridement was compared with sham intervention for knee pain.",
                    outcome="Knee pain",
                ),
                document(
                    "lavage",
                    title="Arthroscopic lavage of the knee",
                    abstract="Arthroscopic lavage was evaluated for knee pain.",
                    outcome="Knee pain",
                ),
                document(
                    "unrelated_lavage",
                    title="Lavage procedure",
                    abstract="Peritoneal lavage was studied, with knee pain assessed as an adverse event.",
                    outcome="Knee pain",
                ),
                document(
                    "vitamin_subscript",
                    title="Vitamin D₃ and dépression",
                    abstract="Vitamin D₃ was tested for dépressive symptoms.",
                    outcome="Mood",
                ),
                document(
                    "vitamin_compact",
                    title="VitaminD3 and depression",
                    abstract="VitaminD3 was tested for depressive symptoms.",
                    outcome="Mood",
                ),
                document(
                    "vitamin_parentheses",
                    title="Vitamin D(3) for depression",
                    abstract="Vitamin D(3) was tested for depression.",
                    outcome="Mood",
                ),
            ]
            await repo.bulk_upsert(rows)
            knee = lexical_query(
                {
                    "intervention": "Arthroscopic surgery (lavage or debridement)",
                    "outcome": "Knee pain",
                },
                "Arthroscopic surgery for knee pain",
            )
            hits, _ = await repo.retrieve(knee, None)
            assert {row["id"] for row in hits} == {"debridement", "lavage"}
            exact = lexical_query(
                {"intervention": "Arthroscopic debridement", "outcome": "Knee pain"},
                "Arthroscopic debridement for knee pain",
            )
            assert {row["id"] for row in (await repo.retrieve(exact, None))[0]} == {"debridement"}
            vitamin = lexical_query(
                {"intervention": "Vitamin D3 supplementation", "outcome": "Depressive symptoms"},
                "Vitamin D3 for depression",
            )
            assert {row["id"] for row in (await repo.retrieve(vitamin, None))[0]} == {
                "vitamin_subscript",
                "vitamin_compact",
                "vitamin_parentheses",
            }
            preserved = await repo.get("debridement")
            assert "débridement" in preserved["abstract"]
            assert "debridement" in preserved["search_text"]
        finally:
            await repo.client.indices.delete(index=repo.index, ignore_unavailable=True)
            await repo.close()

    asyncio.run(exercise())


def test_typed_aliases_stay_within_their_own_mandatory_concept():
    query = lexical_query(
        {
            "intervention": "Fluoxetine",
            "interventionAliases": ["Prozac", "", "treatment"],
            "outcome": "Depression",
            "outcomeAliases": ["Mood", " ", "symptoms"],
            "synonyms": ["osteoporosis"],
        },
        "Does fluoxetine affect depression?",
    )
    candidate = query["bool"]["should"][0]["bool"]
    intervention, outcome = candidate["must"]
    assert intervention["bool"]["minimum_should_match"] == 1
    assert outcome["bool"]["minimum_should_match"] == 1
    assert len(intervention["bool"]["should"]) == len(outcome["bool"]["should"]) == 2
    assert "prozac" in str(intervention) and "mood" not in str(intervention)
    assert "mood" in str(outcome) and "prozac" not in str(outcome)
    assert "osteoporosis" not in str(candidate["must"])


@pytest.mark.skipif(
    not os.getenv("NULLMAP_TEST_ELASTIC_URL"), reason="Opt-in real Elasticsearch test"
)
def test_real_elasticsearch_typed_aliases_preserve_both_required_concepts():
    async def exercise():
        settings = Settings(
            _env_file=None,
            elastic_url=os.environ["NULLMAP_TEST_ELASTIC_URL"],
            elastic_local=True,
            elastic_index=f"nullmap-test-{uuid4().hex}",
            embedding_dimensions=3,
        )
        repo = ElasticRepository(settings)
        try:
            combinations = [
                ("generic_original", "Fluoxetine", "Depression"),
                ("brand_alias", "Prozac", "Mood"),
                ("brand_original", "Prozac", "Depression"),
                ("generic_alias", "Fluoxetine", "Mood"),
                ("brand_wrong_outcome", "Prozac", "Bone density"),
                ("wrong_intervention_alias", "Exercise", "Mood"),
                ("wrong_intervention_original", "Exercise", "Depression"),
                ("generic_wrong_outcome", "Fluoxetine", "Bone density"),
            ]
            await repo.bulk_upsert(
                [
                    document(
                        identifier,
                        title=f"{intervention} and {outcome}",
                        abstract=f"Researchers evaluated {intervention} for {outcome}.",
                        intervention=intervention,
                        outcome=outcome,
                    )
                    for identifier, intervention, outcome in combinations
                ]
            )
            query = lexical_query(
                {
                    "intervention": "Fluoxetine",
                    "interventionAliases": ["Prozac", "", "treatment"],
                    "outcome": "Depression",
                    "outcomeAliases": ["Mood", " ", "symptoms"],
                    "population": "Adults",
                    "synonyms": ["Exercise", "Bone density"],
                },
                "Does fluoxetine affect depression?",
            )
            hits, _ = await repo.retrieve(query, None)
            assert {row["id"] for row in hits} == {
                "generic_original",
                "brand_alias",
                "brand_original",
                "generic_alias",
            }
            aggregate = await repo.aggregate(query, 0.2, "SMD")
            assert aggregate["total"] == 4
            assert sum(aggregate["bucketCounts"].values()) == 4
        finally:
            await repo.client.indices.delete(index=repo.index, ignore_unavailable=True)
            await repo.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "description",
    [
        "Adults",
        "patients",
        "healthy older adults aged 65 years or more",
        "Community-dwelling men and women aged 18-65 years",
        "postmenopausal women",
        "general population",
    ],
)
def test_generic_population_descriptions_do_not_become_mandatory_filters(description):
    assert population_query({"population": description}) is None


def test_explicit_disease_population_guard_cannot_be_bypassed_by_expanded_ids():
    pico = {
        "population": "Patients with symptomatic knee osteoarthritis",
        "intervention": "Arthroscopic lavage or debridement",
        "outcome": "Pain improvement",
    }
    query = lexical_query(
        pico, "Arthroscopic surgery for knee osteoarthritis pain?", ["ankle-paper"]
    )
    assert population_query(pico) in query["bool"]["filter"]
    assert "knee" in str(population_query(pico))
    assert "osteoarthrit" in str(population_query(pico))
    assert "symptomatic" not in str(population_query(pico))
    assert "womac" in str(query).lower()
    generic = lexical_query({**pico, "population": "Adults"}, "Arthroscopic surgery for pain?")
    assert "womac" not in str(generic).lower()


def test_population_screen_skips_network_for_unconstrained_demographics():
    client = SimpleNamespace(search=AsyncMock())
    repo = ElasticRepository(Settings(_env_file=None), client=client)
    assert asyncio.run(
        repo.screen_population(["one", "two", "one"], {"population": "Older adults"})
    ) == {"one", "two"}
    client.search.assert_not_called()


def test_date_bounds_cover_whole_years_and_citation_bounds_exempt_registry_rows():
    clauses = filter_clauses(
        {"yearFrom": 2010, "yearTo": 2020, "minCitations": 5, "maxCitations": 50}
    )
    assert clauses[0] == {
        "range": {"publication_date": {"gte": "2010-01-01", "lte": "2020-12-31"}}
    }
    citations = clauses[1]["bool"]
    assert citations["minimum_should_match"] == 1
    assert {"range": {"cited_by_count": {"gte": 5, "lte": 50}}} in citations["should"]
    # A registry row has no citation count; dropping it would delete the unreported trials.
    assert {"terms": {"source": ["ctgov", "merged"]}} in citations["should"]


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (None, []),
        ({}, []),
        ({"yearFrom": None, "yearTo": None, "minCitations": None, "maxCitations": None}, []),
        ({"yearFrom": 2015}, [{"range": {"publication_date": {"gte": "2015-01-01"}}}]),
        ({"yearTo": 1999}, [{"range": {"publication_date": {"lte": "1999-12-31"}}}]),
    ],
)
def test_each_bound_is_independent_and_an_empty_filter_set_is_not_a_filter(filters, expected):
    assert filter_clauses(filters) == expected


def test_the_exemption_query_counts_registry_rows_the_bound_would_have_removed():
    assert registry_citation_exemption_query({"match_all": {}}, {"yearFrom": 2015}) is None
    assert registry_citation_exemption_query({"match_all": {}}, None) is None
    query = registry_citation_exemption_query({"match_all": {}}, {"minCitations": 5})["bool"]
    assert query["must"] == [{"match_all": {}}]
    assert query["filter"] == [{"terms": {"source": ["ctgov", "merged"]}}]
    assert query["must_not"] == [{"range": {"cited_by_count": {"gte": 5}}}]


def test_a_zero_citation_floor_is_a_bound_and_not_an_absent_one():
    # 0 is falsy but meaningful: it excludes papers with no stored citation count.
    clause = filter_clauses({"minCitations": 0})[0]["bool"]["should"][0]
    assert clause == {"range": {"cited_by_count": {"gte": 0}}}


def test_filters_are_mandatory_clauses_that_expanded_references_cannot_escape():
    pico = {"intervention": "Vitamin D", "outcome": "Depression severity"}
    filters = {"yearFrom": 2015, "minCitations": 5}
    query = lexical_query(pico, "Does vitamin D reduce depression?", ["review-reference"], filters)
    # Expanded ids are an alternative way to match, so the bounds must be filters, not shoulds.
    assert {"ids": {"values": ["review-reference"]}} in query["bool"]["should"]
    for clause in filter_clauses(filters):
        assert clause in query["bool"]["filter"]
    unfiltered = lexical_query(pico, "Does vitamin D reduce depression?", ["review-reference"])
    assert "publication_date" not in str(unfiltered)
    assert "cited_by_count" not in str(unfiltered)


def test_expanded_reference_screening_applies_filters_without_a_population():
    captured = {}

    async def search(**kwargs):
        captured.update(kwargs)
        return {"hits": {"hits": [{"_id": "recent"}]}}

    repo = ElasticRepository(Settings(_env_file=None), client=SimpleNamespace(search=search))
    kept = asyncio.run(repo.screen_population(["recent", "old"], {}, {"yearFrom": 2015}))
    assert kept == {"recent"}
    assert {"range": {"publication_date": {"gte": "2015-01-01"}}} in (
        captured["query"]["bool"]["filter"]
    )


def test_unfiltered_count_uses_the_same_match_set_predicate_as_the_aggregation():
    client = SimpleNamespace(
        count=AsyncMock(return_value={"count": 812}),
        indices=SimpleNamespace(exists=AsyncMock(return_value=True), put_mapping=AsyncMock()),
    )
    repo = ElasticRepository(Settings(_env_file=None), client=client)
    assert asyncio.run(repo.count_studies({"match_all": {}})) == 812
    assert client.count.call_args.kwargs["query"] == ElasticRepository.match_set(
        {"match_all": {}}
    )


@pytest.mark.skipif(
    not os.getenv("NULLMAP_TEST_ELASTIC_URL"), reason="Opt-in real Elasticsearch test"
)
def test_real_elasticsearch_population_guard_scopes_lexical_and_expanded_candidates():
    async def exercise():
        settings = Settings(
            _env_file=None,
            elastic_url=os.environ["NULLMAP_TEST_ELASTIC_URL"],
            elastic_local=True,
            elastic_index=f"nullmap-test-{uuid4().hex}",
            embedding_dimensions=3,
        )
        repo = ElasticRepository(settings)
        try:
            rows = [
                document(
                    "knee",
                    title="Arthroscopic lavage for osteoarthritis of the knee",
                    abstract="Arthroscopic lavage was tested for knee osteoarthritis pain.",
                    population="Knee osteoarthritis",
                    outcome="Pain",
                ),
                document(
                    "elbow",
                    title="Arthroscopic debridement for lateral epicondylitis",
                    abstract="Arthroscopic debridement improved elbow pain in epicondylitis.",
                    population="Elbow epicondylitis",
                    outcome="Pain",
                ),
                document(
                    "ankle",
                    title="Arthroscopic lavage for ankle osteoarthritis",
                    abstract="Arthroscopic lavage was tested for ankle osteoarthritis pain.",
                    population="Ankle osteoarthritis",
                    outcome="Pain",
                ),
                document(
                    "other_knee_disease",
                    title="Arthroscopic lavage for knee rheumatoid arthritis",
                    abstract="Arthroscopic lavage was tested for rheumatoid arthritis knee pain.",
                    population="Knee rheumatoid arthritis",
                    outcome="Pain",
                ),
                document(
                    "related_reference",
                    title="Conservative management of osteoarthritis of the knee",
                    abstract="Medication management was evaluated for osteoarthritis of the knee.",
                    population="Knee osteoarthritis",
                    outcome="Daily activities",
                ),
                document(
                    "womac",
                    title="Arthroscopy for knee osteoarthritis",
                    abstract="Arthroscopic debridement in knee osteoarthritis was assessed with WOMAC.",
                    population="Knee osteoarthritis",
                    outcome="WOMAC composite",
                ),
                document(
                    "hip",
                    title="Arthroscopic lavage for hip osteoarthritis",
                    abstract="Arthroscopic lavage was tested for hip osteoarthritis pain.",
                    population="Hip osteoarthritis",
                    outcome="Pain",
                ),
            ]
            await repo.bulk_upsert(rows)
            pico = {
                "population": "Adults with symptomatic knee osteoarthritis",
                "intervention": "Arthroscopic lavage or debridement",
                "outcome": "Pain improvement",
            }
            query = lexical_query(pico, "Arthroscopic surgery for knee osteoarthritis pain?")
            hits, _ = await repo.retrieve(query, None)
            assert {row["id"] for row in hits} == {"knee", "womac"}
            candidates = ["related_reference", "ankle", "elbow", "other_knee_disease"]
            assert await repo.screen_population(candidates, pico) == {"related_reference"}
            expanded = lexical_query(
                pico, "Arthroscopic surgery for knee osteoarthritis pain?", candidates
            )
            expanded_hits, _ = await repo.retrieve(expanded, None)
            assert {row["id"] for row in expanded_hits} == {"knee", "womac", "related_reference"}
            assert (await repo.aggregate(expanded, 0.2, "SMD"))["total"] == 3
            alternatives = {**pico, "population": "Patients with knee or hip osteoarthritis"}
            assert await repo.screen_population(["knee", "hip", "ankle"], alternatives) == {
                "knee",
                "hip",
            }
        finally:
            await repo.client.indices.delete(index=repo.index, ignore_unavailable=True)
            await repo.close()

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.getenv("NULLMAP_TEST_ELASTIC_URL"), reason="Opt-in real Elasticsearch test"
)
def test_real_elasticsearch_filters_agree_across_retrieval_registry_sweep_and_counts():
    async def exercise():
        settings = Settings(
            _env_file=None,
            elastic_url=os.environ["NULLMAP_TEST_ELASTIC_URL"],
            elastic_local=True,
            elastic_index=f"nullmap-test-{uuid4().hex}",
            embedding_dimensions=3,
        )
        repo = ElasticRepository(settings)
        try:
            rows = [
                document("recent_cited", year=2020, cited_by_count=40),
                document("recent_obscure", year=2020, cited_by_count=1),
                document("old_cited", year=1998, cited_by_count=400),
                document("undated", year=None, cited_by_count=40),
                document(
                    "trial",
                    source="ctgov",
                    year=2020,
                    nct_ids=["NCT00000001"],
                    overall_status="COMPLETED",
                ),
            ]
            await repo.bulk_upsert(rows)
            pico = {"intervention": "Vitamin D", "outcome": "Depression severity"}
            idea = "Does vitamin D reduce depression?"
            filters = {"yearFrom": 2010, "minCitations": 5}
            query = lexical_query(pico, idea, None, filters)
            hits, _ = await repo.retrieve(query, None)
            # The registry row has no citation count and is exempt; the obscure paper is not.
            assert {row["id"] for row in hits} == {"recent_cited", "trial"}
            assert {row["id"] for row in await repo.registry_sweep(query)} == {"trial"}
            aggregate = await repo.aggregate(query, 0.2, "SMD")
            assert aggregate["total"] == 2
            assert [row["year"] for row in aggregate["yearCounts"]] == [2020]
            assert await repo.count_studies(lexical_query(pico, idea)) == len(rows)
            # The trial is in the match set only because the citation bound skips it.
            assert (
                await repo.count_studies(registry_citation_exemption_query(query, filters))
            ) == 1
            # Expanded review references are screened against the same bounds.
            assert await repo.screen_population(
                ["recent_cited", "recent_obscure", "old_cited", "undated"], pico, filters
            ) == {"recent_cited"}
        finally:
            await repo.client.indices.delete(index=repo.index, ignore_unavailable=True)
            await repo.close()

    asyncio.run(exercise())


def test_hypertension_population_matches_by_prefix_so_hypertensive_is_retrieved():
    query = population_query({"population": "Patients with hypertension"})
    assert {"prefix": {"abstract": {"value": "hypertens"}}} in (
        query["bool"]["must"][0]["bool"]["should"][0]["bool"]["should"]
    )


def test_measurement_wording_does_not_become_a_required_concept():
    plain = {"population": "hypertension", "intervention": "KIM-1", "outcome": "KIM-1 level"}
    worded = {
        "population": "hypertension",
        "intervention": "higher expression of KIM-1",
        "outcome": "elevated KIM-1 expression status",
    }
    assert lexical_query(worded, "idea")["bool"]["should"] == (
        lexical_query(plain, "idea")["bool"]["should"]
    )


def test_joined_designator_also_matches_its_hyphenated_spelling():
    pico = {"population": "hypertension", "intervention": "kim1", "outcome": "blood pressure"}
    candidate = lexical_query(pico, "idea")["bool"]["should"][0]
    intervention = candidate["bool"]["must"][0]["bool"]["should"][0]["bool"]["should"]
    joined, spaced = intervention[0]["bool"]["should"]
    assert joined["multi_match"]["query"] == "kim1"
    assert (spaced["multi_match"]["query"], spaced["multi_match"]["type"]) == ("kim 1", "phrase")
    # One letter plus a digit keeps its own token: vitamin d3 has its own alias handling.
    vitamin = lexical_query({**pico, "intervention": "vitamin d3"}, "idea")
    assert "phrase" not in str(vitamin)


def test_population_aliases_are_alternatives_to_the_condition_clause():
    pico = {
        "population": "Patients with hypertension",
        "populationAliases": ["Hypertensive patients", "High blood pressure", "", "Adults"],
    }
    query = population_query(pico)
    primary, *alternatives = query["bool"]["should"]
    assert query["bool"]["minimum_should_match"] == 1
    assert primary == population_query({"population": pico["population"]})
    # "Hypertensive" repeats the stemmed population; blank and generic aliases add nothing.
    assert len(alternatives) == 1
    alias = alternatives[0]["bool"]["must"][0]["bool"]
    assert alias["minimum_should_match"] == 2
    assert [clause["multi_match"]["query"] for clause in alias["should"]] == ["blood", "pressure"]


@pytest.mark.parametrize(
    "alias", ["Hypertensive disorder", "Chronic disease", "Kidney", "disorders", "Patients"]
)
def test_broad_or_generic_population_aliases_are_ignored(alias):
    pico = {"population": "Adults with hypertension", "populationAliases": [alias]}
    # Only structural breadth is detectable here; semantic breadth is the prompt's job.
    assert population_query(pico) == population_query({"population": pico["population"]})


def test_population_alias_cannot_escape_the_anatomical_site_or_exist_alone():
    pico = {
        "population": "Patients with knee osteoarthritis",
        "populationAliases": ["Degenerative joint disease", "Gonarthrosis of the knee"],
    }
    _, without_site, with_site = population_query(pico)["bool"]["should"]
    assert "knee" in str(without_site["bool"]["must"][1])
    assert len(with_site["bool"]["must"]) == 1
    assert population_query({"population": "Adults", "populationAliases": ["Hypertension"]}) is None


def test_population_aliases_are_capped():
    aliases = [f"condition{name}" for name in "abcdef"]
    query = population_query({"population": "hypertension", "populationAliases": aliases})
    assert len(query["bool"]["should"]) == 1 + 4


@pytest.mark.skipif(
    not os.getenv("NULLMAP_TEST_ELASTIC_URL"), reason="Opt-in real Elasticsearch test"
)
def test_real_elasticsearch_embedded_sample_returns_vectors_and_the_corpus_size():
    async def exercise():
        config = Settings(
            _env_file=None,
            elastic_url=os.environ["NULLMAP_TEST_ELASTIC_URL"],
            elastic_local=True,
            elastic_index=f"nullmap-test-{uuid4().hex}",
            embedding_dimensions=3,
        )
        repo = ElasticRepository(config)
        try:
            await repo.bulk_upsert(
                [
                    document(f"work{index}", embedding=[1.0, float(index) / 10, 0.0])
                    for index in range(6)
                ]
                + [
                    document("no_vector"),
                    document("review", embedding=[0.0, 1.0, 0.0], is_review=True),
                    document("draft", record_kind="draft", embedding=[0.0, 0.0, 1.0]),
                ]
            )
            sample = await repo.sample_embedded(limit=4, seed=7)
            assert sample["corpus"] == 7  # six works plus the review, never the non-study draft
            assert len(sample["documents"]) == 4
            assert all(len(doc["embedding"]) == 3 for doc in sample["documents"])
            assert all(doc["id"] != "no_vector" for doc in sample["documents"])
            repeated = await repo.sample_embedded(limit=4, seed=7)
            assert [doc["id"] for doc in repeated["documents"]] == [
                doc["id"] for doc in sample["documents"]
            ]
        finally:
            await repo.client.indices.delete(index=config.elastic_index, ignore_unavailable=True)
            await repo.close()

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.getenv("NULLMAP_TEST_ELASTIC_URL"), reason="Opt-in real Elasticsearch test"
)
def test_real_elasticsearch_scan_walks_every_embedded_study_exactly_once():
    """The map is only the whole index if the scan is: no duplicates, nothing missed."""

    async def exercise():
        config = Settings(
            _env_file=None,
            elastic_url=os.environ["NULLMAP_TEST_ELASTIC_URL"],
            elastic_local=True,
            elastic_index=f"nullmap-test-{uuid4().hex}",
            embedding_dimensions=3,
        )
        repo = ElasticRepository(config)
        try:
            await repo.bulk_upsert(
                [
                    document(f"work{index}", embedding=[1.0, float(index) / 50, 0.0])
                    for index in range(40)
                ]
                + [document("no_vector"), document("draft", record_kind="draft")]
            )
            await repo.client.indices.refresh(index=config.elastic_index)
            assert await repo.count_embedded() == 40
            seen = []
            async for batch in repo.scan_embedded(batch_size=7, slices=3):
                seen.extend(doc["id"] for doc in batch)
            assert sorted(seen) == sorted(f"work{index}" for index in range(40))
            capped = []
            async for batch in repo.scan_embedded(batch_size=5, slices=2, limit=12):
                capped.extend(batch)
            assert len(capped) == 12
            assert all(len(doc["embedding"]) == 3 for doc in capped)
        finally:
            await repo.client.indices.delete(index=config.elastic_index, ignore_unavailable=True)
            await repo.close()

    asyncio.run(exercise())
