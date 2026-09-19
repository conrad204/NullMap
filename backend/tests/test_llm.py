import asyncio
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm import LLMService, Usage, indexed_to_extraction, validate_extraction
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
    "fact",
    [
        {"value": 500, "quote": "Among 200 patients."},
        {"value": 200, "quote": "Among 200 patients there was no effect."},
        {"value": 200, "quote": ""},
    ],
)
def test_fabricated_number_or_quote_rejects_entire_extraction(fact):
    with pytest.raises(ValueError):
        validate_extraction(extraction(n=fact), "Among 200 patients.")


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

    asyncio.run(run())
