import asyncio
import json
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm import (
    LLMService,
    Usage,
    indexed_to_extraction,
    spelled_numbers,
    validate_extraction,
)
from app.models import Extraction, IndexedExtraction, Pico, SearchRequest


def extraction(**kwargs):
    return Extraction.model_validate({**dict.fromkeys(Extraction.model_fields), **kwargs})


def test_exact_numeric_and_quote_validation():
    text = "Among 200 patients the difference was 0.02 (95% CI -0.10 to 0.14; p = 0.7)."
    data = extraction(
        n={"value": 200, "quote": text},
        estimate={"value": 0.02, "quote": text},
        ci_low={"value": -0.1, "quote": text},
        ci_high={"value": 0.14, "quote": text},
        ci_level={"value": 0.95, "quote": text},
        p_value={"value": 0.7, "quote": text},
    )
    result = validate_extraction(data, text)
    assert result["n"] == 200
    assert result["ci_level"] == 0.95
    assert result["p_value_operator"] == "="


@pytest.mark.parametrize(
    "facts",
    [
        {"estimate": {"value": 500, "quote": "Among 200 patients."}},
        {"n": {"value": 200, "quote": "Among 200 patients there was no effect."}},
        {"n": {"value": 200, "quote": ""}},
    ],
)
def test_fabricated_number_or_quote_rejects_entire_extraction(facts):
    with pytest.raises(ValueError):
        validate_extraction(extraction(**facts), "Among 200 patients.")


def test_fabricated_total_sample_size_is_never_stored():
    result = validate_extraction(
        extraction(n={"value": 500, "quote": "Among 200 patients."}), "Among 200 patients.")
    assert result["n"] is None and "n" not in result["extraction_evidence"]


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("The difference was 0.02 (95% CI -0.10 to 0.14).", 0.95),
        ("The difference was 0.02 (90% confidence interval, -0.10 to 0.14).", 0.90),
        ("The difference was 0.02 (CI 95%: -0.10 to 0.14).", 0.95),
        ("The difference was 0.02 (-0.10 to 0.14).", None),
        ("A 1.95% CI difference of 0.02 (-0.10 to 0.14).", None),
    ],
)
def test_omitted_ci_level_is_read_from_the_interval_sentence_when_it_states_one(text, level):
    result = validate_extraction(
        extraction(
            estimate={"value": 0.02, "quote": text},
            ci_low={"value": -0.1, "quote": text},
            ci_high={"value": 0.14, "quote": text},
        ),
        text,
    )
    if level is None:
        assert result["ci_low"] is None and result["ci_high"] is None
        assert result["ci_level"] is None
    else:
        assert (result["ci_low"], result["ci_high"], result["ci_level"]) == (-0.1, 0.14, level)
        assert result["extraction_evidence"]["ci_level"] == text


def test_conflicting_levels_across_bound_sentences_drop_the_interval():
    low = "The lower 95% CI bound was -0.10."
    high = "The upper 90% CI bound was 0.14."
    result = validate_extraction(
        extraction(ci_low={"value": -0.1, "quote": low}, ci_high={"value": 0.14, "quote": high}),
        low + " " + high,
    )
    assert result["ci_low"] is None and result["ci_level"] is None


def test_inequality_preserved():
    text = "The primary outcome was significant (p < 0.05)."
    result = validate_extraction(extraction(p_value={"value": 0.05, "quote": text}), text)
    assert result["p_value_operator"] == "<"


def test_interval_without_reported_level_is_not_assumed_95():
    text = "Effect 0.1, interval -0.2 to 0.4."
    result = validate_extraction(
        extraction(
            estimate={"value": 0.1, "quote": text},
            ci_low={"value": -0.2, "quote": text},
            ci_high={"value": 0.4, "quote": text},
        ),
        text,
    )
    assert result['ci_low'] is None and result['ci_high'] is None


def test_paper_numbers_with_commas_and_unicode_minus():
    text = "Among 1,500 participants, the effect was −0.1."
    result = validate_extraction(
        extraction(n={"value": 1500, "quote": text}, estimate={"value": -0.1, "quote": text}), text
    )
    assert result["n"] == 1500 and result["estimate"] == -0.1


def test_user_sesoi_and_scale_override_model_proposal_and_usage_measured():
    async def run():
        async def parse(**kwargs):
            assert kwargs["store"] is False
            assert kwargs["input"][0]["role"] == "system"
            return SimpleNamespace(
                output_parsed=Pico(
                    population="adults",
                    intervention="vitamin D",
                    comparator="placebo",
                    outcome="depression",
                    synonyms=[],
                    studyDesigns=["RCT"],
                    sesoi=0.2,
                    sesoiRationale="proposal",
                    effectType="SMD",
                ),
                usage=SimpleNamespace(
                    input_tokens=1000,
                    output_tokens=100,
                    input_tokens_details=SimpleNamespace(cached_tokens=500),
                ),
            )

        client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
        config = Settings(_env_file=None, openai_api_key="test")
        service = LLMService(config, client=client)
        usage = Usage()
        result = await service.parse(
            SearchRequest(idea="Does vitamin D reduce depression?", sesoi=0.3, effectType="logOR"),
            usage,
        )
        assert result.sesoi == 0.3 and result.effectType == "logOR"
        assert usage.records[0]["cachedTokens"] == 500
        assert usage.records[0]["estimatedUsd"] == pytest.approx(0.00041)

    asyncio.run(run())


def test_unknown_failed_call_usage_is_labeled():
    async def run():
        async def parse(**kwargs):
            raise RuntimeError("private provider detail")

        service = LLMService(
            Settings(_env_file=None, openai_api_key="test"),
            client=SimpleNamespace(responses=SimpleNamespace(parse=parse)),
        )
        usage = Usage()
        with pytest.raises(RuntimeError):
            await service.structured(Pico, "system", "data", "parse", usage)
        assert usage.records[0]["status"] == "failed_usage_unknown"
        assert "private" not in str(usage.records)

    asyncio.run(run())


def test_sentence_selection_supplies_original_evidence_and_rejects_wrong_number():
    sentences = ["Background sentence.", "Among 200 patients the difference was 0.02."]
    indexed = IndexedExtraction.model_validate(
        {
            **dict.fromkeys(IndexedExtraction.model_fields),
            "n": {"value": 200, "sentence_index": 1},
            "estimate": {"value": 0.02, "sentence_index": 1},
        }
    )
    facts = validate_extraction(indexed_to_extraction(indexed, sentences), " ".join(sentences))
    assert facts["extraction_evidence"]["estimate"] == sentences[1]
    indexed.estimate.value = 0.5
    with pytest.raises(ValueError, match="Number absent"):
        validate_extraction(indexed_to_extraction(indexed, sentences), " ".join(sentences))
    indexed.estimate.sentence_index = 2
    with pytest.raises(ValueError, match="Invalid evidence sentence"):
        indexed_to_extraction(indexed, sentences)


def test_parse_cache_keeps_concepts_stable_without_retaining_plan_overrides():
    async def run():
        calls = 0

        async def parse(**kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(
                output_parsed=Pico(
                    population="adults",
                    intervention="fluoxetine",
                    comparator="placebo",
                    outcome="depression",
                    interventionAliases=["Prozac"],
                    outcomeAliases=["depressive symptoms"],
                    populationAliases=["MDD", "b", "c", "d", "e"],
                    synonyms=[],
                    studyDesigns=["RCT"],
                    sesoi=0.2,
                    sesoiRationale="Editable planning judgment.",
                    effectType="SMD",
                ),
                usage=None,
            )

        service = LLMService(
            Settings(_env_file=None, openai_api_key="test"),
            client=SimpleNamespace(responses=SimpleNamespace(parse=parse)),
        )
        first = await service.parse(
            SearchRequest(idea="Does fluoxetine reduce depression?", sesoi=0.4), Usage()
        )
        repeat_usage = Usage()
        repeat = await service.parse(
            SearchRequest(idea="Does fluoxetine reduce depression?", plannedN=400), repeat_usage
        )
        assert calls == 1 and repeat_usage.parse_cache_hits == 1
        assert first.sesoi == 0.4 and repeat.sesoi == 0.2
        assert repeat.interventionAliases == ["Prozac"]
        assert repeat.outcomeAliases == ["depressive symptoms"]
        assert repeat.populationAliases == ["MDD", "b", "c", "d"]

    asyncio.run(run())


def test_full_text_lines_are_quoted_verbatim_and_validated_against_the_lines_only(monkeypatch):
    lines = [
        "Abstract sentence without numbers.",
        "The prespecified primary outcome was the change in PHQ-9 score at 12 weeks.",
        "[Table 2 Outcomes] PHQ-9 change | −4.1 | −4.0 | 0.02 (−0.10 to 0.14) | 0.74",
    ]
    indexed = IndexedExtraction.model_validate(
        {
            **dict.fromkeys(IndexedExtraction.model_fields),
            "outcome": {"value": "change in PHQ-9 score at 12 weeks", "sentence_index": 1},
            "estimate": {"value": 0.02, "sentence_index": 2},
            "ci_low": {"value": -0.10, "sentence_index": 2},
            "ci_high": {"value": 0.14, "sentence_index": 2},
            "p_value": {"value": 0.74, "sentence_index": 2},
        }
    )
    captured = {}

    async def structured(self, schema, prompt, data, purpose, usage, large=False):
        captured.update(prompt=prompt, data=data)
        return indexed

    monkeypatch.setattr(LLMService, "structured", structured)
    service = LLMService(Settings(_env_file=None, openai_api_key="test"))
    # The study abstract is deliberately unrelated: validation must run against the lines.
    study = {"id": "W1", "abstract": "Completely different abstract text."}
    result = asyncio.run(service.extract(study, Usage(), lines=lines))
    assert result["extraction_source"] == "full_text"
    assert result["extraction_evidence"]["estimate"] == lines[2]
    assert result["extraction_evidence"]["outcome"] == lines[1]
    assert result["estimate"] == 0.02 and result["p_value"] == 0.74
    # The CI level was never stated in the quoted row, so bounds are dropped, not assumed 95%.
    assert result["ci_low"] is None and result["ci_high"] is None
    assert json.loads(captured["data"]) == {"sentences": lines}
    assert "table" in captured["prompt"].lower()
    abstract = (
        "Background. The prespecified primary outcome was the change in PHQ-9 score at 12 weeks. "
        "PHQ-9 change was 0.02 (−0.10 to 0.14), p = 0.74."
    )
    plain = asyncio.run(service.extract({"id": "W2", "abstract": abstract}, Usage()))
    assert plain["extraction_source"] == "abstract"
    assert plain["extraction_evidence"]["estimate"] == "PHQ-9 change was 0.02 (−0.10 to 0.14), p = 0.74."


def _arm_facts(**overrides):
    text = ("Mean pain was 4.1 (SD 1.9) with drug (n=60) and 4.3 (SD 2.0) with placebo (n=62). "
            "Stroke occurred in 12 and 15 participants.")
    facts = {
        "n_intervention": {"value": 60, "quote": text}, "n_comparator": {"value": 62, "quote": text},
        "mean_intervention": {"value": 4.1, "quote": text},
        "mean_comparator": {"value": 4.3, "quote": text},
        "sd_intervention": {"value": 1.9, "quote": text},
        "sd_comparator": {"value": 2.0, "quote": text},
        "events_intervention": {"value": 12, "quote": text},
        "events_comparator": {"value": 15, "quote": text},
    }
    facts.update(overrides)
    return extraction(**facts), text


def test_arm_level_summaries_need_verbatim_numbers_and_both_arms():
    facts, text = _arm_facts()
    result = validate_extraction(facts, text)
    assert (result["mean_intervention"], result["sd_comparator"], result["events_comparator"]) == (
        4.1, 2.0, 15)
    # One arm's SD missing: the continuous summary is dropped whole; counts survive.
    facts, text = _arm_facts(sd_comparator=None)
    partial = validate_extraction(facts, text)
    assert partial["mean_intervention"] is None and "mean_intervention" not in partial[
        "extraction_evidence"]
    assert partial["events_intervention"] == 12
    # Arm sizes missing: nothing arm-level is usable.
    facts, text = _arm_facts(n_comparator=None)
    assert validate_extraction(facts, text)["events_intervention"] is None


def test_arm_level_standard_errors_and_percentages_are_kept_as_pairs():
    text = ("Mean pain was 4.1 (SE 0.3) with drug (n=60) and 4.3 (SE 0.4) with placebo (n=62). "
            "Stroke occurred in 12.5% and 16.1% of participants.")
    fact = lambda value: {"value": value, "quote": text}  # noqa: E731
    facts = dict(n_intervention=fact(60), n_comparator=fact(62), mean_intervention=fact(4.1),
                 mean_comparator=fact(4.3), se_intervention=fact(0.3), se_comparator=fact(0.4),
                 percent_intervention=fact(12.5), percent_comparator=fact(16.1))
    result = validate_extraction(extraction(**facts), text)
    assert (result["se_intervention"], result["se_comparator"]) == (0.3, 0.4)
    assert (result["percent_intervention"], result["percent_comparator"]) == (12.5, 16.1)
    assert result["mean_intervention"] == 4.1 and result["sd_intervention"] is None
    # One SE missing drops the means too; a missing percentage drops the other one.
    partial = validate_extraction(extraction(**{**facts, "se_comparator": None}), text)
    assert partial["mean_intervention"] is None and partial["se_intervention"] is None
    assert partial["percent_intervention"] == 12.5
    partial = validate_extraction(extraction(**{**facts, "percent_comparator": None}), text)
    assert partial["percent_intervention"] is None and partial["se_comparator"] == 0.4
    padded = text + " 0 101"
    for name, value in (("se_comparator", 0), ("percent_comparator", 101)):
        with pytest.raises(ValueError):
            validate_extraction(
                extraction(**{**facts, name: {"value": value, "quote": padded}}), padded)


@pytest.mark.parametrize(
    "override",
    [
        {"events_intervention": {"value": 1.9, "quote": "(SD 1.9)"}},
        {"events_intervention": {"value": 62, "quote": "placebo (n=62)"}},
        {"sd_comparator": {"value": 0, "quote": "in 12 and 15 participants. 0"}},
    ],
)
def test_fractional_or_impossible_arm_values_reject_the_extraction(override):
    facts, text = _arm_facts(**override)
    with pytest.raises(ValueError):
        validate_extraction(facts, text + " 0")


def test_unquoted_arm_value_or_p_value_is_dropped_without_losing_the_verified_interval():
    text = "Mean pain was 4.1 (SD 1.9) vs 4.3 (SD 2.0); HR 1.18 (95% CI 1.08 to 1.29; P = .001)."
    fact = lambda value: {"value": value, "quote": text}  # noqa: E731
    result = validate_extraction(
        extraction(
            estimate=fact(1.18), ci_low=fact(1.08), ci_high=fact(1.29), ci_level=fact(0.95),
            p_value=fact(0.05), n_intervention=fact(60), n_comparator=fact(62),
            mean_intervention=fact(4.2), mean_comparator=fact(4.3),
            sd_intervention=fact(1.9), sd_comparator=fact(2.0),
        ),
        text,
    )
    assert (result["estimate"], result["ci_low"], result["ci_high"]) == (1.18, 1.08, 1.29)
    # 0.05 and 4.2 are in no quote: the p value goes alone, the arm summary as a group.
    assert result["p_value"] is None and "p_value_operator" not in result
    assert all(result[name] is None for name in (
        "mean_intervention", "mean_comparator", "sd_intervention", "sd_comparator"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Sixty-three rats were randomized (n = 21)", [63.0]),
        ("Four hundred patients were randomized, 201 received", [400.0]),
        ("One thousand two hundred and fifty-six adults", [1256.0]),
        ("After matching, 11 106 matched pairs were found.", [11106.0]),
        ("HR 1.18 (95% CI 1.08 1.29) in 2 of 3 trials", []),
    ],
)
def test_spelled_out_and_space_grouped_numbers_are_read_as_written(text, expected):
    assert spelled_numbers(text) == expected


def test_spelled_sample_size_is_verbatim_support_and_a_summed_total_is_dropped_alone():
    text = "Four hundred patients were randomized. The difference was 0.31 (95% CI -0.19 to 0.81)."
    sentences = text.split(". ")
    numbers = {
        "estimate": {"value": 0.31, "quote": sentences[1]},
        "ci_low": {"value": -0.19, "quote": sentences[1]},
        "ci_high": {"value": 0.81, "quote": sentences[1]},
        "ci_level": {"value": 0.95, "quote": sentences[1]},
    }
    spelled = validate_extraction(
        extraction(n={"value": 400, "quote": sentences[0] + "."}, **numbers), text)
    assert spelled["n"] == 400 and spelled["extraction_evidence"]["n"].startswith("Four hundred")
    # 201 + 199 appears in no sentence: the total goes, the verified interval stays.
    summed = validate_extraction(
        extraction(n={"value": 401, "quote": sentences[0] + "."}, **numbers), text)
    assert summed["n"] is None and "n" not in summed["extraction_evidence"]
    assert (summed["estimate"], summed["ci_low"], summed["ci_high"]) == (0.31, -0.19, 0.81)
    # Any other invented number still rejects the whole extraction.
    with pytest.raises(ValueError):
        validate_extraction(
            extraction(**{**numbers, "estimate": {"value": 0.35, "quote": sentences[1]}}), text)


def test_reported_result_needs_its_quote_and_a_known_value():
    text = "There was no significant difference between groups."
    ok = validate_extraction(extraction(reported_result={"value": "null", "quote": text}), text)
    assert ok["reported_result"] == "null"
    assert ok["extraction_evidence"]["reported_result"] == text
    # The schema itself admits only the three categories, never a free-text verdict.
    with pytest.raises(ValueError):
        extraction(reported_result={"value": "promising", "quote": text})
    with pytest.raises(ValueError):
        validate_extraction(
            extraction(reported_result={"value": "null", "quote": "No difference."}), text)


def test_out_of_range_sentence_drops_a_droppable_fact_but_still_rejects_an_estimate():
    fields = dict.fromkeys(IndexedExtraction.model_fields)
    droppable = IndexedExtraction.model_validate(
        {**fields, "reported_result": {"value": "null", "sentence_index": 7},
         "estimate": {"value": 0.3, "sentence_index": 0}})
    converted = indexed_to_extraction(droppable, ["The difference was 0.3."])
    assert converted.reported_result is None and converted.estimate.value == 0.3
    fatal = IndexedExtraction.model_validate(
        {**fields, "estimate": {"value": 0.3, "sentence_index": 7}})
    with pytest.raises(ValueError, match="Invalid evidence sentence"):
        indexed_to_extraction(fatal, ["The difference was 0.3."])


def test_outcome_groups_ignore_unknown_repeated_and_lone_ids():
    from app.models import OutcomeGroups

    async def exercise():
        service = LLMService(Settings(_env_file=None, openai_api_key=""))

        async def structured(schema, prompt, data, purpose, usage, large=False):
            assert purpose == "group" and large and "question" in json.loads(data)
            return OutcomeGroups.model_validate({"groups": [
                {"label": " Systolic  BP ", "study_ids": ["A", "B", "B", "ZZ"]},
                {"label": "Second claim on A", "study_ids": ["A", "C"]},
                {"label": "", "study_ids": ["D", "E"]},
            ]})

        service.structured = structured
        rows = [{"id": x} for x in "ABCDE"]
        return await service.group_outcomes({"idea": "question"}, rows, Usage())

    # A stays in its first group; C is then alone and dropped; an unlabeled group is dropped.
    assert asyncio.run(exercise()) == {"A": "Systolic BP", "B": "Systolic BP"}


def test_trend_prose_may_only_repeat_numbers_from_its_table():
    from app.models import EffectTrend

    async def exercise(summary):
        service = LLMService(Settings(_env_file=None, openai_api_key=""))

        async def structured(schema, prompt, data, purpose, usage, large=False):
            return EffectTrend(summary=summary, patterns=["Reductions near 5.2 mmHg", " "])

        service.structured = structured
        return await service.trend({"rows": [{"estimate": 5.2, "n": 1200}]}, Usage())

    kept = asyncio.run(exercise("Reports describe falls of 5.2 mmHg among 1,200 adults."))
    assert kept.patterns == ["Reductions near 5.2 mmHg"]
    with pytest.raises(ValueError, match="absent from the table: 9.8"):
        asyncio.run(exercise("Reports describe falls of 9.8 mmHg."))


def test_overview_keeps_only_notes_for_supplied_studies_and_rejects_invented_numbers():
    from app.models import Overview

    async def exercise(summary):
        service = LLMService(Settings(_env_file=None, openai_api_key=""))

        async def structured(schema, prompt, data, purpose, usage, large=False):
            assert purpose == "overview" and large
            return Overview.model_validate({"summary": summary, "notes": [
                {"id": "A", "note": "Observational study of blood levels."},
                {"id": "A", "note": "Repeated."}, {"id": "ZZ", "note": "Not a supplied study."},
                {"id": "B", "note": " "}]})

        service.structured = structured
        return await service.overview({"question": {}, "rows": [{"id": "A"}, {"id": "B"}]}, Usage())

    kept = asyncio.run(exercise("Nothing here tested the supplement against a control."))
    assert [(n.id, n.note) for n in kept.notes] == [("A", "Observational study of blood levels.")]
    with pytest.raises(ValueError, match="Overview introduced a number"):
        asyncio.run(exercise("About 40% improved."))
