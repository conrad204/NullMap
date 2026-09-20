import asyncio

import pytest

from app.config import Settings
from app.fulltext import FullText, FullTextClient, parse_jats
from app.llm import LLMService, Usage
from app.models import Claim
from app.novelty import Candidate, NoveltyEngine, cosine, to_candidate

JATS = """<article><front><article-meta><abstract><p>We asked whether a JAK2 inhibitor changes
viability in leukemia cells, and measured viability at 24 hours.</p>
</abstract></article-meta></front><body>
<sec><title>Methods</title><p>Cells were treated with 10 uM compound for 24 hours.</p></sec>
<sec><title>Results</title><p>Viability did not differ between treated and control wells (p = 0.61).</p></sec>
<sec><title>Discussion</title><p>The compound does not affect viability in this model.</p></sec>
</body></article>"""


def document(identifier="W1", title="JAK2 inhibition in leukemia cells", year=2021, **extra):
    return {
        "id": identifier,
        "title": title,
        "abstract": "Ruxolitinib reduced pJAK2 phosphorylation and viability was measured.",
        "year": year,
        "venue": "Blood",
        "url": "https://doi.org/10.1/abc",
        "pmids": ["999"],
        "cited_by_count": 7,
        "referenced_works": ["W55"],
        "is_review": False,
        **extra,
    }


def test_jats_sections_are_split_by_kind():
    sections = parse_jats(JATS)
    assert set(sections) == {"abstract", "methods", "results", "discussion"}
    assert "did not differ" in sections["results"]


def test_back_matter_is_not_kept_as_evidence():
    xml = JATS.replace(
        "</body>",
        "<sec><title>References</title><p>1. Someone et al, Journal of Things, 2019, 1-20.</p>"
        "</sec><sec><title>Competing interests</title><p>The authors declare none at all.</p>"
        "</sec></body>",
    )
    assert set(parse_jats(xml)) == {"abstract", "methods", "results", "discussion"}


def test_evidence_text_puts_results_before_methods():
    text = FullText("W1", "full_text", sections=parse_jats(JATS))
    body = text.evidence_text()
    assert body.index("## results") < body.index("## methods")
    assert text.chars > 100


def test_candidate_carries_identifiers_and_relation():
    candidate = to_candidate(document(), "reference")
    assert candidate is not None
    assert (candidate.id, candidate.relation, candidate.pmid) == ("W1", "reference", "999")
    assert candidate.doi.endswith("10.1/abc")
    assert candidate.abstract.startswith("Ruxolitinib")


def test_retracted_work_is_dropped():
    assert to_candidate(document(is_retracted=True), "retrieved") is None


class StubRepo:
    """Hybrid retrieval returns two works; one of them references a third, unretrieved paper."""

    def __init__(self):
        self.queries, self.requested = [], []

    async def retrieve(self, query, vector):
        self.queries.append(query)
        return [
            document("W1", "JAK2 inhibitor viability in leukemia cells"),
            document("W2", "pJAK2 assays paper", referenced_works=["W55"]),
        ], "hybrid_rrf"

    async def get_many(self, ids, include_vectors=True):
        self.requested.append(list(ids))
        return [document("W55", "referenced paper on pJAK2 assays", referenced_works=[])]

    async def close(self):
        pass


class StubEmbedder:
    """Deterministic lexical embedding: similarity rises with shared words."""

    vocabulary = ["jak2", "leukemia", "viability", "pjak2", "assays", "referenced", "paper"]

    def embed_documents(self, texts):
        return [
            [1.0 * (term in text.lower()) for term in self.vocabulary] + [0.1] for text in texts
        ]


class StubFullText:
    def __init__(self, availability="full_text", title=""):
        self.availability = availability
        self.title = title
        self.fetched = []

    async def fetch(self, paper_id, doi="", pmid="", abstract=""):
        self.fetched.append(paper_id)
        return FullText(
            paper_id,
            self.availability,
            source="europepmc:PMC1",
            sections=parse_jats(JATS) if self.availability == "full_text" else {"abstract": abstract},
            title=self.title,
        )

    async def close(self):
        pass


def engine(config=None, fulltext=None, llm=None):
    return NoveltyEngine(
        llm=llm or LLMService(Settings(openai_api_key="")),
        config=config or Settings(openai_api_key=""),
        repo=StubRepo(),
        fulltext=fulltext or StubFullText(),
        embedder=StubEmbedder(),
    )


def test_scan_expands_through_indexed_references():
    instance = engine()
    claim = Claim(
        intervention="JAK2 inhibitor", system="leukemia cells", outcome="viability",
        direction="decrease", queries=["JAK2 inhibitor leukemia"], synonyms=[],
    )
    scanned = asyncio.run(instance.scan("JAK2 inhibition reduces viability", claim, 20))
    ids = {candidate.id: candidate.relation for candidate in scanned}
    assert ids == {"W1": "retrieved", "W2": "retrieved", "W55": "reference"}
    assert instance.repo.requested == [["W55"]]
    assert all(candidate.score > 0 for candidate in scanned)
    assert scanned == sorted(scanned, key=lambda c: -c.score)


def test_without_a_key_papers_are_retrieved_but_not_read():
    report = asyncio.run(engine().assess("JAK2 inhibition reduces leukemia viability", 10, 2))
    assert report["verdict"] == "insufficient_evidence"
    assert report["scanned"] > 0 and report["usage"]["calls"] == 0
    assert any("not read" in warning for warning in report["warnings"])


class StubLLM:
    """Stands in for structured OpenAI calls, including one paper that tests the claim."""

    def __init__(self, coverage="tests_claim", quote="Viability did not differ between treated and control wells (p = 0.61)."):
        self.coverage, self.quote = coverage, quote
        self.read_texts = []

    async def parse_claim(self, hypothesis, usage):
        return Claim(
            intervention="ruxolitinib", system="leukemia cells", outcome="viability",
            direction="decrease", queries=["JAK2 inhibitor leukemia viability"], synonyms=[],
        )

    async def read_paper(self, claim, paper, text, usage):
        from app.models import PaperRead

        self.read_texts.append(text)
        return PaperRead(
            coverage=self.coverage,
            system_tested="HEL cells",
            intervention_tested="ruxolitinib 10 uM",
            outcome_measured="viability at 24 h",
            finding="No difference in viability.",
            quotes=[self.quote],
            facets_settled=["viability at 24 h in HEL cells"],
            facets_untested=["primary patient blasts"],
        )

    async def judge_novelty(self, payload, usage):
        from app.models import NoveltyAssessment

        return NoveltyAssessment(
            verdict="open_gap", summary="Only cell lines were tested.",
            gaps=["primary patient blasts"], settled=["viability at 24 h"],
        )

    async def close(self):
        pass


def test_full_run_reads_full_text_and_returns_a_verdict():
    fulltext = StubFullText()
    instance = engine(config=Settings(openai_api_key="k"), fulltext=fulltext, llm=StubLLM())
    report = asyncio.run(instance.assess("Ruxolitinib reduces viability in leukemia cells", 20, 2))
    assert report["verdict"] == "open_gap"
    assert report["readFullText"] == len(fulltext.fetched) > 0
    assert report["papers"][0]["coverage"] == "tests_claim"
    assert "did not differ" in report["papers"][0]["quotes"][0]
    assert report["gaps"] == ["primary patient blasts"]


def test_the_publisher_title_wins_when_the_index_disagrees():
    """OpenAlex merges of MAG records can hang a DOI off another work's title."""
    resolved = "Hypertension Management in Patients with Chronic Kidney Disease"
    instance = engine(
        config=Settings(openai_api_key="k"),
        fulltext=StubFullText(title=resolved),
        llm=StubLLM(),
    )
    report = asyncio.run(instance.assess("Ruxolitinib reduces viability in leukemia cells", 20, 1))
    paper = report["papers"][0]
    assert paper["title"] == resolved
    assert paper["indexTitle"].startswith("JAK2")
    assert paper["metadataConflict"] is True
    assert any("disagreed" in warning for warning in report["warnings"])


def test_deterministic_verdict_when_narration_fails():
    class FailingJudge(StubLLM):
        async def judge_novelty(self, payload, usage):
            raise RuntimeError("no narration")

    instance = engine(config=Settings(openai_api_key="k"), llm=FailingJudge())
    report = asyncio.run(instance.assess("Ruxolitinib reduces viability in leukemia cells", 20, 2))
    assert report["verdict"] == "already_done"
    assert report["gaps"] == ["primary patient blasts"]
    assert any("narration" in warning for warning in report["warnings"])


def test_reading_without_verbatim_support_is_discarded():
    service = LLMService(Settings(openai_api_key="k"))

    async def structured(schema, prompt, data, purpose, usage, large=False):
        from app.models import PaperRead

        return PaperRead(
            coverage="tests_claim", system_tested="HEL", intervention_tested="ruxolitinib",
            outcome_measured="viability", finding="Reduced viability.",
            quotes=["Viability fell by 80% in every model."],
            facets_settled=[], facets_untested=[],
        )

    service.structured = structured
    with pytest.raises(ValueError):
        asyncio.run(service.read_paper("claim", {"title": "t"}, "Viability did not differ.", Usage()))


def test_quotes_survive_whitespace_differences():
    service = LLMService(Settings(openai_api_key="k"))

    async def structured(schema, prompt, data, purpose, usage, large=False):
        from app.models import PaperRead

        return PaperRead(
            coverage="tests_claim", system_tested="HEL", intervention_tested="ruxolitinib",
            outcome_measured="viability", finding="No change.",
            quotes=["Viability did  not\ndiffer."], facets_settled=[], facets_untested=[],
        )

    service.structured = structured
    reading = asyncio.run(
        service.read_paper("claim", {"title": "t"}, "Viability did not differ.", Usage())
    )
    assert len(reading.quotes) == 1


def test_fulltext_falls_back_to_the_abstract_when_no_open_body_exists():
    class NoRecord(FullTextClient):
        async def locate(self, doi="", pmid=""):
            return {}

    result = asyncio.run(NoRecord().fetch("W1", doi="10.1/x", abstract="Only the abstract."))
    assert result.availability == "abstract_only"
    assert result.sections == {"abstract": "Only the abstract."}


def test_cosine_is_orientation_only():
    assert cosine([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 3.0]) == pytest.approx(0.0)
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_low_similarity_candidates_are_not_read():
    instance = engine(config=Settings(openai_api_key="k"), llm=StubLLM())
    far = Candidate(
        id="W900", title="medieval wool prices", abstract="trade", year=1990, venue="",
        url="", doi="", pmid="", cited_by_count=0, is_review=False, relation="search",
    )

    async def only_far(hypothesis, claim, limit):
        far.score = 0.05
        return [far]

    instance.scan = only_far
    report = asyncio.run(instance.assess("Ruxolitinib reduces viability in leukemia cells", 20, 2))
    assert report["papers"] == []
    assert report["verdict"] == "insufficient_evidence"
