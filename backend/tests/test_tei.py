import pytest

from app.ingest.tei import (
    extract_findings,
    parse_tei,
    summarize,
    tei_to_study,
)
from app.statistics import assign_bucket

TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title level="a" type="main">Effect of vitamin D on depression</title></titleStmt>
      <sourceDesc><biblStruct><idno type="DOI">10.1000/xyz123</idno></biblStruct></sourceDesc>
    </fileDesc>
    <profileDesc>
      <abstract><p>We tested whether vitamin D supplementation reduces depression.</p></abstract>
    </profileDesc>
  </teiHeader>
  <text>
    <body>
      <div><head>Results</head>
        <p>There was no statistically significant difference between groups
           (OR = 1.05, 95% CI 0.88 to 1.26, p = 0.62).</p>
        <p>Depression scores were significantly reduced in the treatment arm
           (SMD = 0.45, 95% CI 0.20-0.70, p &lt; 0.001).</p>
      </div>
      <div><head>Correlation analysis</head>
        <p>Serum vitamin D correlated weakly with mood (r = 0.08, p = 0.30, n = 240).</p>
      </div>
    </body>
    <back>
      <div type="annex"><head>Appendix A. Sensitivity analyses</head>
        <p>The sensitivity model showed no significant association
           (HR = 0.98, 95% CI 0.80 to 1.20).</p>
        <figure type="table">
          <head>Table S1. Adjusted estimates</head>
          <table>
            <row><cell>Outcome</cell><cell>Estimate</cell><cell>95% CI</cell><cell>p</cell></row>
            <row><cell>Remission</cell><cell>OR = 1.10</cell><cell>0.90 to 1.34</cell><cell>p = 0.41</cell></row>
          </table>
        </figure>
      </div>
      <div type="references"><head>References</head><p>Smith J, et al. 2019.</p></div>
    </back>
  </text>
</TEI>
"""


@pytest.fixture
def document():
    return parse_tei(TEI)


def _one(findings, **conditions):
    for finding in findings:
        if all(getattr(finding, key) == value for key, value in conditions.items()):
            return finding
    raise AssertionError(f"No finding matched {conditions}")


def test_parse_extracts_title_doi_abstract_and_sections(document):
    assert document.title == "Effect of vitamin D on depression"
    assert document.doi == "10.1000/xyz123"
    assert "vitamin D supplementation" in document.abstract
    parts = {p.part for p in document.paragraphs}
    assert {"abstract", "body", "appendix"} <= parts
    assert any(p.section == "Results" for p in document.paragraphs)
    # The bibliography div must be excluded.
    assert not any("Smith" in p.text for p in document.paragraphs)


def test_table_from_appendix_is_parsed(document):
    assert len(document.tables) == 1
    table = document.tables[0]
    assert "Adjusted estimates" in table.caption
    assert ["Remission", "OR = 1.10", "0.90 to 1.34", "p = 0.41"] in table.rows


def test_null_result_from_effect_ci_and_pvalue(document):
    findings = extract_findings(document)
    null_or = _one(findings, effect_type="OR", part="body")
    assert null_or.estimate == 1.05
    assert (null_or.ci_low, null_or.ci_high) == (0.88, 1.26)
    assert null_or.p_value == 0.62 and null_or.p_value_operator == "="
    assert null_or.is_null is True and null_or.result_label == "null"
    assert "confidence_interval" in null_or.terms and "p_value" in null_or.terms


def test_significant_result_excludes_null(document):
    findings = extract_findings(document)
    positive = _one(findings, effect_type="SMD")
    assert positive.estimate == 0.45
    assert (positive.ci_low, positive.ci_high) == (0.20, 0.70)
    assert positive.p_value == 0.001 and positive.p_value_operator == "<"
    assert positive.is_significant is True and positive.result_label == "positive"


def test_correlation_and_sample_size(document):
    findings = extract_findings(document)
    correlation = _one(findings, correlation=0.08)
    assert correlation.p_value == 0.30
    assert correlation.n == 240
    # p = 0.30 confirms the null for a correlation (null value 0).
    assert correlation.is_null is True and correlation.result_label == "null"


def test_appendix_finding_marked_and_null_by_ci(document):
    findings = extract_findings(document)
    appendix = _one(findings, effect_type="HR")
    assert appendix.part == "appendix"
    assert (appendix.ci_low, appendix.ci_high) == (0.80, 1.20)
    # CI straddles 1 for a hazard ratio -> null even without a p-value.
    assert appendix.is_null is True


def test_table_finding_uses_pvalue(document):
    findings = extract_findings(document)
    table_findings = [f for f in findings if f.part == "table" and f.p_value is not None]
    assert table_findings
    assert any(f.p_value == 0.41 and f.is_null for f in table_findings)


def test_summary_is_mixed_and_reports_evidence(document):
    findings = extract_findings(document)
    summary = summarize(findings)
    # The document has several null findings and one significant one.
    assert summary["result_label"] == "mixed"
    assert summary["evidence_span"]
    assert summary["effect_type"] in {"OR", "SMD", "HR"}


def test_tei_to_study_matches_schema_and_flows_into_statistics(document):
    study = tei_to_study(TEI, source="openalex")
    assert study["id"] == "10.1000/xyz123"
    assert study["source"] == "openalex"
    assert study["classification_method"] == "tei_lexical_numeric"
    assert study["null_evidence_count"] >= 3
    assert study["url"] == "https://doi.org/10.1000/xyz123"
    # The extracted fields must be consumable by the shared classifier.
    bucket = assign_bucket(study, sesoi=0.2, effect_type=study["effect_type"] or "SMD")
    assert bucket["bucket"] in {"credible_null", "effect", "inconclusive", "failed"}


def test_lexical_null_context_colours_a_bare_statistic():
    xml = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>
      <div><head>Results</head>
        <p>No significant between-group difference was observed. The mean change
           was 2.1 (95% CI 0.5 to 3.7) in the intervention arm.</p>
      </div></body></text></TEI>"""
    findings = extract_findings(parse_tei(xml))
    # The interval alone is ambiguous, but the paragraph confirms the null.
    interval = _one(findings, ci_low=0.5)
    assert interval.is_null is True and interval.result_label == "null"


def test_scientific_notation_pvalue_is_significant():
    xml = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>
      <div><head>Results</head>
        <p>The effect was strong (SMD = 0.90, 95% CI 0.70 to 1.10, p = 3 x 10-4).</p>
      </div></body></text></TEI>"""
    finding = _one(extract_findings(parse_tei(xml)), effect_type="SMD")
    assert finding.p_value is not None and finding.p_value < 0.001
    assert finding.is_significant is True


def test_credible_null_flows_through_assign_bucket():
    xml = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>
      <div><head>Results</head>
        <p>No meaningful effect was detected (SMD = 0.02, 95% CI -0.05 to 0.09, p = 0.60).</p>
      </div></body></text></TEI>"""
    study = tei_to_study(xml, "doc-1")
    assert study["effect_type"] == "SMD"
    bucket = assign_bucket(study, sesoi=0.2, effect_type="SMD")
    assert bucket["bucket"] == "credible_null"


def test_invalid_xml_raises():
    with pytest.raises(ValueError, match="Invalid TEI XML"):
        parse_tei("<TEI><body><p>unclosed")


def test_no_statistics_returns_no_result():
    xml = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>
      <div><head>Introduction</head><p>This paper reviews background theory.</p></div>
      </body></text></TEI>"""
    study = tei_to_study(xml, "doc-2")
    assert study["result_label"] == "no_result_stated"
    assert study["null_evidence_count"] == 0
