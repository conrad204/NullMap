"""Europe PMC full text: PMCID handling, JATS flattening, and fetch behaviour without a network."""

import asyncio
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.fulltext import FullTextClient, flatten_jats, normalize_pmcid

FIXTURE = (Path(__file__).parent / "fixtures" / "jats_trial.xml").read_text()


def config(**kwargs):
    return Settings(_env_file=None, openai_api_key="", embeddings_enabled=False, **kwargs)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("PMC123456", "PMC123456"),
        ("pmc0007", "PMC7"),
        ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9876543/", "PMC9876543"),
        ("9876543", "PMC9876543"),
        ("", None),
        (None, None),
        (True, None),
        ("W123", None),
        ("https://openalex.org/W123", None),
    ],
)
def test_pmcid_normalization(value, expected):
    assert normalize_pmcid(value) == expected


def test_flatten_prioritizes_abstract_primary_outcome_results_then_tables():
    abstract = "Background: Vitamin D has been proposed for depression. Results: Symptoms improved in both arms."
    flat = flatten_jats(FIXTURE, abstract)
    lines = flat.lines
    # Abstract sentences first, from the study's own abstract rather than the XML.
    assert lines[0].startswith("Background:")
    # Methods sentences that define the primary outcome or power are kept; others are not.
    assert any("prespecified primary outcome" in line for line in lines)
    assert any("80% power" in line for line in lines)
    assert not any("randomized adults with mild depression" in line for line in lines)
    # Table rows are flattened with their label so the extraction can cite a row verbatim.
    assert "[Table 2 Primary and secondary outcomes at 12 weeks] columns: Outcome | Vitamin D | Placebo | Difference (95% CI) | p" in lines
    assert "[Table 2 Primary and secondary outcomes at 12 weeks] PHQ-9 change | −4.1 | −4.0 | 0.02 (−0.10 to 0.14) | 0.74" in lines
    # Results prose is included.
    assert any("did not differ between groups (SMD 0.02" in line for line in lines)
    # Introduction, discussion, conclusions and references never contribute evidence lines.
    assert not any("Observational data" in line for line in lines)
    assert not any("clinically important secondary finding" in line for line in lines)
    assert not any("well tolerated" in line for line in lines)
    assert not any("Someone et al." in line for line in lines)
    assert flat.summary()["tables"] == 3 and flat.summary()["methods_primary"] == 2
    assert flat.text.count("\n") == len(lines) - 1
    # Results prose precedes table rows, so a tight budget keeps the narrative numbers.
    first_table = next(i for i, line in enumerate(lines) if line.startswith("[Table"))
    assert any("did not differ" in line for line in lines[:first_table])


def test_outcome_tables_outrank_baseline_tables_and_structured_abstracts_are_read():
    xml = """<article><front><abstract><sec><title>Background</title><p>One thing.</p></sec>
    <sec><title>Results</title><p>Another thing.</p></sec></abstract></front><body>
    <sec sec-type="results"><title>Results</title>
    <table-wrap><label>Table 1</label><caption><p>Baseline characteristics</p></caption>
      <table><tbody><tr><td>Age</td><td>60</td></tr></tbody></table></table-wrap>
    <table-wrap><label>Table 3</label><caption><p>Adverse events</p></caption>
      <table><tbody><tr><td>Nausea</td><td>5</td></tr></tbody></table></table-wrap>
    <table-wrap><label>Table 2</label><caption><p>Primary outcome</p></caption>
      <table><tbody><tr><td>Score</td><td>0.1 (-0.2 to 0.4)</td></tr></tbody></table></table-wrap>
    </sec></body></article>"""
    flat = flatten_jats(xml, "")
    assert flat.lines[:2] == ["One thing.", "Another thing."]
    tables = [line for line in flat.lines if line.startswith("[Table")]
    assert tables[0].startswith("[Table 2 Primary outcome]")
    assert tables[-1].startswith("[Table 1 Baseline") or tables[-1].startswith("[Table 3 Adverse")
    assert flatten_jats(xml, "", max_lines=20).lines[2].startswith("[Table 2 Primary outcome]")


def test_flatten_falls_back_to_xml_abstract_and_respects_the_line_budget():
    flat = flatten_jats(FIXTURE, "", max_lines=20)
    assert flat.lines[0].startswith("Background: Vitamin D")
    tiny = flatten_jats(FIXTURE, "", max_lines=4)
    assert len(tiny.lines) == 4 and tiny.truncated
    # Budget is spent in priority order: abstract, then methods primary-outcome lines.
    assert tiny.lines[-1].startswith("The prespecified primary outcome") or "power" in tiny.lines[-1]


def test_flatten_survives_malformed_xml_and_bodyless_articles():
    assert flatten_jats("<article><body><sec><title>Results</title><p>Unclosed").lines == []
    only_front = "<article><front><abstract><p>One. Two.</p></abstract></front></article>"
    flat = flatten_jats(only_front, "")
    assert flat.lines == ["One.", "Two."] and flat.results_lines == 0


def test_lines_returns_used_unavailable_or_error_without_raising():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/PMC1/fullTextXML"):
            return httpx.Response(200, text=FIXTURE)
        if request.url.path.endswith("/PMC2/fullTextXML"):
            return httpx.Response(404)
        if request.url.path.endswith("/PMC3/fullTextXML"):
            return httpx.Response(200, text="")
        if request.url.path.endswith("/PMC5/fullTextXML"):
            return httpx.Response(200, text="<article><front><abstract><p>Only.</p></abstract></front></article>")
        return httpx.Response(500)

    client = FullTextClient(config(), transport=httpx.MockTransport(handler))

    async def exercise():
        used = await client.lines({"id": "a", "pmcid": "PMC1", "abstract": "Abstract sentence."})
        assert used[1] == "used" and any("PHQ-9 change" in line for line in used[0])
        assert await client.lines({"id": "b", "pmcid": "PMC2", "abstract": ""}) == (None, "unavailable")
        assert await client.lines({"id": "c", "pmcid": "PMC3", "abstract": ""}) == (None, "unavailable")
        assert await client.lines({"id": "d", "pmcid": "PMC4", "abstract": ""}) == (None, "error")
        # Full text without a results section or table adds nothing beyond the abstract.
        assert await client.lines({"id": "e", "pmcid": "PMC5", "abstract": ""}) == (None, "unavailable")
        assert await client.lines({"id": "f", "abstract": "No PMCID."}) == (None, None)
        assert all(url.startswith("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC") for url in calls)
        assert len(calls) == 5

    asyncio.run(exercise())


def test_disabled_setting_short_circuits_before_any_request():
    def handler(request):
        raise AssertionError("network must not be used")

    client = FullTextClient(config(fulltext_enabled=False), transport=httpx.MockTransport(handler))
    assert asyncio.run(client.lines({"id": "a", "pmcid": "PMC1"})) == (None, None)
