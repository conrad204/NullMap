"""Novelty-gap search: scan the indexed literature cheaply, then read the papers that matter.

The pipeline is deliberately two-speed. A wide scan ranks hundreds of indexed works with
hybrid retrieval, reference edges and embeddings, and costs no LLM calls; only the handful
of papers that could actually settle the claim are pulled as open-access full text and read
by the large model. The question it answers is not "what is relevant" but "has this specific
claim been tested, and if not, which facet of it is still open".

Full text is the one thing the corpus cannot supply: the snapshot carries abstracts and
metadata, and an abstract states what a paper claims rather than what it measured.
"""

import argparse
import asyncio
import json
import logging
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, field

from app.config import Settings, settings
from app.embeddings import get_embedder
from app.fulltext import FullTextClient
from app.llm import LLMService, Usage
from app.models import Claim, NoveltyAssessment
from app.repository import ElasticRepository, lexical_query

logger = logging.getLogger(__name__)

# Reading a paper the scan already ranked far from the claim buys nothing.
READ_MIN_SIMILARITY = 0.35
REFERENCE_CAP = 200


@dataclass
class Candidate:
    """A scanned work: metadata, how it was reached, and how close it sits to the claim."""

    id: str
    title: str
    abstract: str
    year: int | None
    venue: str
    url: str
    doi: str
    pmid: str
    cited_by_count: int
    is_review: bool
    relation: str  # retrieved | reference
    referenced_works: list[str] = field(default_factory=list)
    embedding: list[float] | None = None
    score: float = 0.0


def to_candidate(document: dict, relation: str) -> Candidate | None:
    """An indexed study document as a scan candidate; retracted work is never a precedent."""
    if document.get("is_retracted") or not document.get("id"):
        return None
    url = document.get("url", "") or ""
    return Candidate(
        id=document["id"],
        title=document.get("title", ""),
        abstract=document.get("abstract", "") or "",
        year=document.get("year"),
        venue=document.get("venue", "") or "",
        url=url,
        doi=url if "doi.org" in url else "",
        pmid=(document.get("pmids") or [""])[0],
        cited_by_count=document.get("cited_by_count") or 0,
        is_review=bool(document.get("is_review")),
        relation=relation,
        referenced_works=document.get("referenced_works") or [],
        embedding=document.get("embedding"),
    )


def _title_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _same_title(left: str, right: str) -> bool:
    """Compare titles through punctuation, case and markup differences between sources."""
    first, second = _title_key(left), _title_key(right)
    return bool(first) and bool(second) and (first == second or first in second or second in first)


def cosine(a: list[float], b: list[float]) -> float:
    denominator = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / denominator if denominator else 0.0


class NoveltyEngine:
    def __init__(
        self,
        llm: LLMService | None = None,
        config: Settings = settings,
        repo: ElasticRepository | None = None,
        fulltext: FullTextClient | None = None,
        embedder=None,
    ):
        self.config = config
        self.llm = llm or LLMService(config)
        self.repo = repo or ElasticRepository(config)
        self.fulltext = fulltext or FullTextClient()
        self.embedder = embedder

    async def close(self) -> None:
        await asyncio.gather(self.repo.close(), self.fulltext.close(), self.llm.close())

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.embedder is None:
            self.embedder = get_embedder(self.config.embedding_model, self.config.embedding_device)
        return await asyncio.to_thread(self.embedder.embed_documents, texts)

    async def parse(self, hypothesis: str, usage: Usage) -> Claim:
        try:
            return await self.llm.parse_claim(hypothesis, usage)
        except Exception as exc:
            logger.warning("Claim parsing unavailable: %s", type(exc).__name__)
            return Claim(
                intervention="", system="", outcome="", direction="",
                queries=[hypothesis[:250]], synonyms=[],
            )

    async def scan(self, hypothesis: str, claim: Claim, limit: int) -> list[Candidate]:
        """Hybrid-retrieve candidates, then follow reference edges out of the closest papers.

        The edge walk is what a keyword query cannot do: a paper that tested this claim and
        phrased it differently is usually one reference away from a paper that phrases it
        our way, and reviews carry hand-screened lists of exactly that work.
        """
        pico = {
            "intervention": claim.intervention,
            "outcome": claim.outcome,
            "population": claim.system,
            "synonyms": claim.synonyms,
        }
        query = lexical_query(pico, " ".join([hypothesis, *claim.queries]))
        vector = (await self.embed([self.target(hypothesis, claim)]))[0]
        documents, _ = await self.repo.retrieve(query, vector)
        if not documents:
            # The search pipeline wants a precise evidence pool; a novelty scan wants recall,
            # because the reader, not the query, decides whether a paper settles the claim.
            documents, _ = await self.repo.retrieve({"match_all": {}}, vector)
        found: dict[str, Candidate] = {}
        for document in documents:
            candidate = to_candidate(document, "retrieved")
            if candidate and candidate.id not in found:
                found[candidate.id] = candidate
        if not found:
            return []
        ranked = await self.rank(vector, list(found.values()))

        seeds = ranked[:6]
        references = [
            ref
            for ref in dict.fromkeys(r for seed in seeds for r in seed.referenced_works)
            if ref not in found
        ][:REFERENCE_CAP]
        expanded = [
            candidate
            for document in await self.repo.get_many(references)
            if (candidate := to_candidate(document, "reference"))
        ]
        if expanded:
            ranked = sorted([*ranked, *await self.rank(vector, expanded)], key=lambda c: -c.score)
        return ranked[:limit]

    def target(self, hypothesis: str, claim: Claim) -> str:
        return " ".join(filter(None, [hypothesis, claim.intervention, claim.system, claim.outcome]))

    async def rank(self, vector: list[float], candidates: list[Candidate]) -> list[Candidate]:
        """Prefer the indexed embedding the ingestion pass already paid for; embed the rest."""
        missing = [c for c in candidates if not c.embedding or len(c.embedding) != len(vector)]
        if missing:
            vectors = await self.embed([f"{c.title}. {c.abstract}" for c in missing])
            for candidate, embedding in zip(missing, vectors):
                candidate.embedding = embedding
        for candidate in candidates:
            candidate.score = cosine(vector, candidate.embedding or [])
        return sorted(candidates, key=lambda c: -c.score)

    async def read(
        self, hypothesis: str, candidate: Candidate, usage: Usage, warnings: list[str]
    ) -> dict:
        """Pull the actual paper and have the large model report what its results establish."""
        text = await self.fulltext.fetch(
            candidate.id, doi=candidate.doi, pmid=candidate.pmid, abstract=candidate.abstract
        )
        row = {
            **{k: v for k, v in asdict(candidate).items() if k not in ("embedding", "referenced_works")},
            "availability": text.availability,
            "textSource": text.source,
            "textChars": text.chars,
        }
        # OpenAlex merges of MAG records can attach a DOI to another work's title and year,
        # so the publisher record wins for anything shown next to the text we actually read.
        if text.title and not _same_title(text.title, candidate.title):
            row.update(title=text.title, indexTitle=candidate.title, metadataConflict=True)
            warnings.append(
                f"Index metadata disagreed with the publisher record for '{text.title[:60]}'; "
                "the resolved title is shown."
            )
        if text.availability == "unavailable" or not self.config.openai_api_key:
            return row
        body = text.evidence_text()
        try:
            reading = await self.llm.read_paper(hypothesis, row, body, usage)
        except ValueError:
            warnings.append(
                f"A reading of '{candidate.title[:60]}' lacked verbatim support and was discarded."
            )
            return row
        except Exception as exc:
            logger.warning("Paper reading unavailable: %s", type(exc).__name__)
            warnings.append("Some papers could not be read; their abstracts remain visible.")
            return row
        return {**row, **reading.model_dump()}

    async def assess(self, hypothesis: str, scan: int = 120, read: int = 5) -> dict:
        usage = Usage()
        warnings: list[str] = []
        if not self.config.openai_api_key:
            warnings.append(
                "OPENAI_API_KEY is not configured: papers were scanned and retrieved but not read."
            )
        claim = await self.parse(hypothesis, usage)
        scanned = await self.scan(hypothesis, claim, scan)
        if not scanned:
            return {
                "hypothesis": hypothesis,
                "claim": claim.model_dump(),
                "scanned": 0,
                "papers": [],
                "verdict": "insufficient_evidence",
                "summary": "No candidate literature was retrieved, so novelty cannot be assessed. This is a retrieval gap, not evidence that the claim is untested.",
                "gaps": [],
                "settled": [],
                "warnings": [*warnings, "The indexed corpus returned no candidates; it may not cover this field yet."],
                "usage": usage.summary([]),
            }
        targets = [c for c in scanned if not c.is_review and c.score >= READ_MIN_SIMILARITY][:read]
        papers = await asyncio.gather(
            *(self.read(hypothesis, candidate, usage, warnings) for candidate in targets)
        )
        read_full = [p for p in papers if p.get("availability") == "full_text"]
        if targets and not read_full:
            warnings.append(
                "No open-access full text was available for the closest papers; readings fall back to abstracts."
            )
        assessment = await self.judge(hypothesis, claim, papers, scanned, usage, warnings)
        return {
            "hypothesis": hypothesis,
            "claim": claim.model_dump(),
            "scanned": len(scanned),
            "readFullText": len(read_full),
            "papers": papers,
            "nearest": [
                {"id": c.id, "title": c.title, "year": c.year, "score": round(c.score, 4),
                 "relation": c.relation, "url": c.url}
                for c in scanned[: max(read * 4, 20)]
            ],
            **assessment,
            "warnings": warnings,
            "usage": usage.summary([c.abstract for c in scanned]),
        }

    async def judge(
        self,
        hypothesis: str,
        claim: Claim,
        papers: list[dict],
        scanned: list[Candidate],
        usage: Usage,
        warnings: list[str],
    ) -> dict:
        readings = [p for p in papers if p.get("coverage")]
        direct = [p for p in readings if p["coverage"] == "tests_claim"]
        fallback = {
            "verdict": "already_done" if direct else
            ("incremental" if any(p["coverage"] == "tests_related" for p in readings)
             else "insufficient_evidence"),
            "summary": (
                f"{len(scanned)} candidate works were scanned and {len(readings)} were read. "
                f"{len(direct)} tested this claim directly. "
                "This summary is computed from the readings, without narration."
            ),
            "gaps": list(dict.fromkeys(f for p in readings for f in p.get("facets_untested", [])))[:4],
            "settled": list(dict.fromkeys(f for p in readings for f in p.get("facets_settled", [])))[:4],
        }
        if not readings or not self.config.openai_api_key:
            return fallback
        payload = {
            "hypothesis": hypothesis,
            "claim": claim.model_dump(),
            "scannedCount": len(scanned),
            "readings": [
                {
                    k: p.get(k)
                    for k in ("title", "year", "url", "availability", "coverage", "system_tested",
                              "intervention_tested", "outcome_measured", "finding",
                              "facets_settled", "facets_untested")
                }
                for p in readings
            ],
        }
        try:
            assessment: NoveltyAssessment = await self.llm.judge_novelty(payload, usage)
        except Exception as exc:
            logger.warning("Novelty narration unavailable: %s", type(exc).__name__)
            warnings.append("AI narration was unavailable; the verdict is computed from readings.")
            return fallback
        return assessment.model_dump()


def format_report(report: dict) -> str:
    lines = [
        "=" * 100,
        f"HYPOTHESIS: {report['hypothesis']}",
        "=" * 100,
        f"claim      : {json.dumps(report['claim'])}",
        f"scan       : {report['scanned']} works ranked, "
        f"{report.get('readFullText', 0)} read as full text, {len(report['papers'])} read in total",
        "",
        f"VERDICT    : {report['verdict'].upper()}",
        f"{report['summary']}",
        "",
    ]
    if report.get("gaps"):
        lines += ["OPEN GAPS:", *(f"  - {gap}" for gap in report["gaps"]), ""]
    if report.get("settled"):
        lines += ["ALREADY SETTLED:", *(f"  - {item}" for item in report["settled"]), ""]
    for paper in report["papers"]:
        lines += [
            "-" * 100,
            f"[{paper.get('coverage', 'not_read')}] {paper['title'][:110]} ({paper.get('year')})",
            f"  similarity {paper['score']:.3f} via {paper['relation']} | "
            f"{paper.get('availability')} {paper.get('textSource', '')} "
            f"({paper.get('textChars', 0)} chars) | {paper.get('url', '')}",
        ]
        if paper.get("finding"):
            lines += [
                f"  system     : {paper.get('system_tested')}",
                f"  measured   : {paper.get('outcome_measured')}",
                f"  finding    : {paper.get('finding')}",
                *(f"  quote      : \"{quote[:220]}\"" for quote in paper.get("quotes", [])),
            ]
    usage = report["usage"]
    lines += [
        "-" * 100,
        f"llm calls {usage['calls']} | tokens in {usage['inputTokens']} out {usage['outputTokens']} "
        f"| estimated ${usage['estimatedUsd']:.4f}",
    ]
    if report.get("warnings"):
        lines += ["warnings:", *(f"  ! {w}" for w in report["warnings"])]
    return "\n".join(lines)


async def run(hypothesis: str, scan: int, read: int, as_json: bool) -> int:
    engine = NoveltyEngine()
    try:
        report = await engine.assess(hypothesis, scan=scan, read=read)
    finally:
        await engine.close()
    print(json.dumps(report, indent=2) if as_json else format_report(report))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check whether a hypothesis is already tested.")
    parser.add_argument("hypothesis")
    parser.add_argument("--scan", type=int, default=120, help="candidate works to rank")
    parser.add_argument("--read", type=int, default=5, help="papers to pull and read in full")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"))
    return asyncio.run(run(args.hypothesis, args.scan, args.read, args.json))


if __name__ == "__main__":
    sys.exit(main())
