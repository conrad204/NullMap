"""The online pipeline: parse → retrieve/expand → extract/cache → aggregate → plan."""

import asyncio
import logging
import math
from collections import Counter
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from app.config import Settings, settings
from app.fulltext import FullTextClient
from app.llm import LLMService, Usage
from app.models import Pico, SearchRequest
from app.repository import ElasticRepository, lexical_query, population_query, rrf_fuse
from app.statistics import analyze_studies, assign_bucket

logger = logging.getLogger(__name__)


_REASON_PHRASES = {
    "wide_interval": "had an interval too wide to separate no effect from a meaningful one",
    "mixed_result": "reported conflicting primary results",
    "no_result": "had no readable comparative result",
    "other_scale": "reported on a scale that cannot be compared with this threshold",
    "no_threshold": "could not be compared without a valid threshold",
}


def inconclusive_sentence(count: int, reasons: dict | None) -> str:
    """Inconclusive is mostly a statement about what could be read, so say which."""
    if not count:
        return ""
    parts = [
        f"{reasons[key]} {phrase}"
        for key, phrase in _REASON_PHRASES.items()
        if reasons and reasons.get(key)
    ]
    detail = f": {'; '.join(parts)}" if parts else ""
    return f"A further {count} are inconclusive{detail}. "


def to_paper(study: dict, sesoi: float, effect_type: str) -> dict:
    verdict = assign_bucket(study, sesoi, effect_type)
    effect = None
    if study.get("estimate") is not None and study.get("effect_type"):
        effect = {"metric": study["effect_type"], "value": study["estimate"]}
        if study.get("ci_low") is not None and study.get("ci_high") is not None:
            effect["ci"] = [study["ci_low"], study["ci_high"]]
    nct_ids = study.get("nct_ids", [])
    links = [f"https://clinicaltrials.gov/study/{nct}" for nct in nct_ids]
    links += [f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" for pmid in study.get("pmids", [])]
    links += [p["url"] for p in study.get("linked_papers", []) if p.get("url")]
    return {
        "id": study["id"],
        "title": study.get("title", ""),
        "authors": study.get("authors", []),
        "year": study.get("year") or 0,
        "venue": study.get("venue"),
        "source": "clinicaltrials"
        if study.get("source") == "ctgov"
        else study.get("source", "openalex"),
        "url": study.get("url") or (links[0] if links else ""),
        "citations": study.get("cited_by_count", 0),
        "abstractAvailable": study.get("abstract_available"),
        "workType": study.get("work_type"),
        "snapshotDate": (study.get("snapshot_provenance") or {}).get("snapshot_date"),
        "verdict": verdict["bucket"],
        "rationale": verdict["rationale"],
        "inconclusiveReason": verdict["inconclusive_reason"],
        "resultDirection": study.get("result_direction"),
        "sampleSize": study.get("n"),
        "effectSize": effect,
        "evidenceSpan": study.get("evidence_span", ""),
        "numericSource": study.get("numeric_source"),
        "evidenceTier": verdict["evidence_tier"],
        "mde": verdict["mde"],
        "pValue": study.get("p_value"),
        "whyStopped": study.get("why_stopped"),
        "nctIds": nct_ids,
        "pmids": study.get("pmids", []),
        "significantButTrivial": verdict["significant_but_trivial"],
        "linkedUrls": list(dict.fromkeys(links)),
        "numericNotes": verdict.get("numeric_notes", []),
        "analysisEffectSize": (
            {
                "metric": verdict["analysis_effect_type"],
                "value": verdict["analysis_estimate"],
                "ci": verdict["analysis_ci"],
            }
            if verdict.get("analysis_estimate") is not None and verdict.get("analysis_ci")
            else None
        ),
        "extractionEvidence": study.get("extraction_evidence", {}),
        "extractionSource": study.get("extraction_source"),
        "pmcid": study.get("pmcid"),
        "ciLevel": study.get("ci_level"),
        "pValueOperator": study.get("p_value_operator"),
        "analysisEffectType": verdict.get("analysis_effect_type"),
        "primaryOutcome": study.get("outcome") or "",
        "outcomeUnit": study.get("outcome_unit") or "",
    }


class SearchPipeline:
    def __init__(
        self,
        repository: ElasticRepository,
        llm: LLMService,
        config: Settings = settings,
        embedder=None,
        fulltext: FullTextClient | None = None,
    ):
        self.repo, self.llm, self.config = repository, llm, config
        self.embedder = embedder
        self.fulltext = fulltext or FullTextClient(config)
        # Single API process shares document locks; concurrent warm-up pays once per work.
        self.locks: dict[str, asyncio.Lock] = {}
        self.active = asyncio.Semaphore(config.max_concurrent_searches)

    async def embed(self, text: str) -> list[float] | None:
        if not self.config.embeddings_enabled:
            return None
        if self.embedder is None:
            from app.embeddings import get_embedder

            self.embedder = get_embedder(self.config.embedding_model, self.config.embedding_device)
        return await asyncio.to_thread(self.embedder.embed_query, text)

    async def expand(
        self,
        hits: list[dict],
        vector: list[float] | None,
        warnings: list[str],
        pico: dict | None = None,
    ) -> list[dict]:
        if vector is None:
            return []
        reviews = [d for d in hits if d.get("is_review")][:3]
        if any(not review.get("referenced_works") for review in reviews):
            warnings.append(
                "Some indexed reviews lack reference lists, limiting reference discovery."
            )
        references = list(
            dict.fromkeys(ref for review in reviews for ref in review.get("referenced_works", []))
        )[:180]
        if not references:
            return []
        indexed = await self.repo.get_many(references)
        known = {d["id"] for d in indexed}
        missing = [ref for ref in references if ref not in known]
        if missing:
            warnings.append(
                f"{len(missing)} review references are outside the loaded snapshot index. "
                "Reference discovery is limited to the imported records."
            )
        canonical_ids = list(
            dict.fromkeys(
                identifier
                for doc in indexed
                if doc.get("record_kind") == "linked_publication"
                for identifier in doc.get("canonical_ids", [])
            )
        )
        canonical = await self.repo.get_many(canonical_ids) if canonical_ids else []
        indexed = list(
            {
                doc["id"]: doc
                for doc in [*indexed, *canonical]
                if doc.get("record_kind") != "linked_publication"
            }.values()
        )
        norm = math.sqrt(sum(x * x for x in vector))
        ranked = []
        for doc in indexed:
            embedding = doc.get("embedding")
            if not embedding:
                embedding = await self.embed(doc.get("abstract") or doc.get("title", ""))
            if embedding and len(embedding) == len(vector):
                denom = norm * math.sqrt(sum(x * x for x in embedding))
                similarity = sum(a * b for a, b in zip(vector, embedding)) / denom if denom else 0
                if similarity >= self.config.reference_min_similarity:
                    ranked.append((similarity, doc))
        if ranked and population_query(pico or {}) is not None:
            eligible_ids = await self.repo.screen_population(
                [doc["id"] for _, doc in ranked], pico or {}
            )
            ranked = [(score, doc) for score, doc in ranked if doc["id"] in eligible_ids]
        return [doc for _, doc in sorted(ranked, key=lambda pair: -pair[0])]

    async def extract_one(self, study: dict, usage: Usage, warnings: list[str]) -> dict:
        if study.get("is_review"):
            return study
        if not study.get("abstract") and not self.fulltext.pmcid(study):
            return study
        # Registry primary-outcome numbers are authoritative; papers cannot overwrite them.
        if study.get("source") == "ctgov" or (
            study.get("source") == "merged"
            and any(study.get(k) is not None for k in ("estimate", "ci_low", "p_value"))
        ):
            return study
        lock = self.locks.setdefault(study["id"], asyncio.Lock())
        async with lock:
            current = await self.repo.get(study["id"]) or study
            fresh = bool(current.get("extracted_at")) and (
                current.get("extraction_version") == self.config.extraction_cache_version
            )
            # Full text is a lazy upgrade: an abstract-only cache is re-read once when Europe
            # PMC has the paper. A recorded 'used' or 'unavailable' verdict is never re-fetched.
            upgrade = (
                self.config.fulltext_enabled
                and self.fulltext.pmcid(current) is not None
                and current.get("fulltext_status") not in ("used", "unavailable")
            )
            if fresh and not upgrade:
                usage.extraction_cache_hits += 1
                if current.get("extraction_status") == "rejected":
                    warnings.append(
                        "A cached extraction failed evidence validation; that study remains text only."
                    )
                if current.get("extraction_status") == "retained_verified":
                    warnings.append(
                        "A newer extraction failed verification; this study uses its previous verified facts."
                    )
                return current
            if not self.config.openai_api_key:
                return current
            lines, status = (None, None)
            if self.config.fulltext_enabled:
                lines, status = await self.fulltext.lines(current)
            if status == "error":
                warnings.append(
                    "Full text could not be fetched for some open-access papers; their abstracts were used."
                )
                if fresh:
                    usage.extraction_cache_hits += 1
                    return current
            if fresh and status == "unavailable":
                # Nothing beyond the abstract exists; record that so the fetch is not repeated.
                await self.repo.cache_extraction(study["id"], {"fulltext_status": "unavailable"})
                usage.extraction_cache_hits += 1
                return dict(current, fulltext_status="unavailable")
            if not lines and not current.get("abstract"):
                return current
            try:
                extraction = (
                    await self.llm.extract(current, usage, lines=lines)
                    if lines
                    else await self.llm.extract(current, usage)
                )
            except ValueError:
                extraction = {
                    "extracted_at": datetime.now(UTC).isoformat(),
                    "extraction_version": self.config.extraction_cache_version,
                    "extraction_status": "rejected",
                    "extraction_source": "full_text" if lines else "abstract",
                }
                if current.get("extraction_status") in ("verified", "retained_verified"):
                    extraction["extraction_status"] = "retained_verified"
                    extraction["extraction_facts_version"] = current.get(
                        "extraction_facts_version", current.get("extraction_version")
                    )
                    extraction["extraction_source"] = current.get("extraction_source")
                    warnings.append(
                        "A new extraction failed verification; the previous verified facts were retained."
                    )
                else:
                    warnings.append(
                        "An extraction failed verbatim evidence validation and was excluded."
                    )
            except Exception as exc:
                logger.warning("Extraction unavailable: %s", type(exc).__name__)
                warnings.append(
                    "Some paper extractions were unavailable; source classifications remain visible."
                )
                return current
            if status in ("used", "unavailable"):
                extraction["fulltext_status"] = status
            # Never replace trusted registry PICO fields with absent extraction fields.
            updated = dict(current, **extraction)
            extraction.update(assign_bucket(updated))
            await self.repo.cache_extraction(study["id"], extraction)
            return dict(current, **extraction)

    async def group_outcomes(
        self, question: dict, studies: list[dict], verdicts: dict, usage: Usage, warnings: list[str]
    ) -> dict[str, str]:
        """Ask which numeric studies measure the same thing; pooling itself stays in code.

        Exact outcome wording almost never repeats across reports, so without this no
        pool forms. Only studies that already have a usable standard error are sent.
        """
        rows = [
            {
                "id": d["id"],
                "outcome": d.get("outcome") or "",
                "unit": d.get("outcome_unit") or "",
                "intervention": d.get("intervention") or "",
                "comparator": d.get("comparator") or "",
                "scale": verdicts[d["id"]]["analysis_effect_type"],
            }
            for d in studies
            if verdicts[d["id"]]["analysis_se"] is not None
            and verdicts[d["id"]]["bucket"] not in {"failed", "unreported"}
            and d.get("outcome")
        ]
        if len(rows) < 3 or not self.config.openai_api_key:
            return {}
        try:
            return await self.llm.group_outcomes(question, rows, usage)
        except Exception as exc:
            logger.warning("Outcome grouping unavailable: %s", type(exc).__name__)
            warnings.append(
                "Outcome grouping was unavailable; only studies with identical outcome "
                "wording could be pooled."
            )
            return {}

    async def effect_trend(
        self,
        idea: str,
        studies: list[dict],
        verdicts: dict,
        aggregation: dict,
        stats: dict,
        usage: Usage,
        warnings: list[str],
    ) -> dict | None:
        """What the studies reporting an effect have in common; tallies are counted here."""
        effects = [d for d in studies if verdicts[d["id"]]["bucket"] == "effect"]
        if not effects:
            return None
        directions = Counter(d.get("result_direction") or "unclear" for d in effects)
        counts = aggregation["bucketCounts"]
        trend = {
            "studied": len(effects),
            "totalEffects": counts["effect"],
            "favoursIntervention": directions["favours_intervention"],
            "favoursComparator": directions["favours_comparator"],
            "unclear": directions["unclear"],
            "summary": None,
            "patterns": [],
            "scope": f"Based on the {len(effects)} effect-reporting studies read in detail, of "
            f"{counts['effect']} in the full match set.",
        }
        if len(effects) < 2 or not self.config.openai_api_key:
            return trend
        rows = []
        for d in effects[:20]:
            verdict = verdicts[d["id"]]
            evidence = d.get("extraction_evidence") or {}
            rows.append(
                {
                    "id": d["id"],
                    "population": (d.get("population") or "")[:160],
                    "intervention": (d.get("intervention") or "")[:160],
                    "comparator": (d.get("comparator") or "")[:160],
                    "outcome": (d.get("outcome") or "")[:200],
                    "unit": d.get("outcome_unit") or "",
                    "n": d.get("n"),
                    "direction": d.get("result_direction") or "unclear",
                    "estimate": d.get("estimate"),
                    "scale": d.get("effect_type"),
                    "tier": verdict["evidence_tier"],
                    "quote": (
                        evidence.get("reported_result")
                        or evidence.get("result_direction")
                        or d.get("evidence_span")
                        or ""
                    )[:320],
                }
            )
        try:
            narrative = await self.llm.trend(
                {
                    "question": idea,
                    "rows": rows,
                    "pools": [
                        {key: pool[key] for key in ("outcome", "effectType", "k", "estimate", "ci")}
                        for pool in stats.get("pools", [])
                    ],
                    "context": {
                        "effectStudiesInTable": len(rows),
                        "effectStudiesInFullMatchSet": counts["effect"],
                        "reportedNulls": counts["credible_null"] + counts.get("reported_null", 0),
                        "unreportedTrials": counts["unreported"],
                        "directionTally": dict(directions),
                    },
                },
                usage,
            )
            trend.update(summary=narrative.summary, patterns=narrative.patterns)
        except Exception as exc:
            logger.warning("Effect trend unavailable: %s", type(exc).__name__)
            warnings.append("The effect-trend summary was unavailable; direction counts are shown.")
        return trend

    async def search(self, request: SearchRequest, progress=None) -> dict:
        async with self.active:
            return await self._search(request, progress)

    async def _search(self, request: SearchRequest, progress=None) -> dict:
        started = perf_counter()
        warnings: list[str] = []
        usage = Usage()

        async def emit(stage: str, message: str, scanned: int | None = None):
            if progress:
                await progress(
                    {
                        "stage": stage,
                        "message": message,
                        **({"scanned": scanned} if scanned is not None else {}),
                    }
                )

        await self.repo.ensure_index()
        await emit("keywords", "Parsing population, intervention, comparison, and outcome")
        try:
            pico = await self.llm.parse(request, usage)
        except Exception as exc:
            logger.warning("PICO parsing unavailable: %s", type(exc).__name__)
            pico = Pico(
                population="",
                intervention=request.idea,
                comparator="",
                outcome="",
                synonyms=[],
                studyDesigns=[],
                sesoi=request.sesoi or 0.2,
                sesoiRationale="Fallback planning assumption; set a domain-appropriate threshold.",
                effectType=request.effectType,
            )
            warnings.append(
                "PICO parsing was unavailable. Search uses your original wording and selected planning values."
            )
        await emit("searching", "Retrieving indexed papers and registered trials")
        query = lexical_query(pico.model_dump(), request.idea)
        try:
            vector = await self.embed(request.idea)
        except Exception as exc:
            vector = None
            logger.warning("Local embeddings unavailable: %s", type(exc).__name__)
            warnings.append("Local embeddings are unavailable; retrieval used BM25 keyword search.")
        hits, mode = await self.repo.retrieve(query, vector)
        expanded = await self.expand(hits, vector, warnings, pico.model_dump())
        if expanded:
            query = lexical_query(pico.model_dump(), request.idea, [d["id"] for d in expanded])
            hits = rrf_fuse(hits, expanded)
        registry = await self.repo.registry_sweep(query)
        hits = rrf_fuse(hits, registry)
        # Link across retrieved source records, preserving one canonical row per registered trial.
        from app.ingest.linker import link_studies

        hits = link_studies(hits)
        # Linking is normally offline; newly discovered links must also update global counts.
        merged = [d for d in hits if d.get("source") == "merged"]
        if merged:
            await self.repo.persist_links(merged)
            query = lexical_query(
                pico.model_dump(),
                request.idea,
                [d["id"] for d in expanded] + [d["id"] for d in merged],
            )
        studies = [d for d in hits if not d.get("is_review")]
        await emit(
            "classifying",
            "Loading cached numbers and verifying uncached evidence spans",
            len(studies),
        )
        targets = [d for d in studies if d.get("source") in ("openalex", "merged")][
            : self.config.extraction_limit
        ]
        extracted = await asyncio.gather(*(self.extract_one(d, usage, warnings) for d in targets))
        by_id = {d["id"]: d for d in extracted}
        studies = [by_id.get(d["id"], d) for d in studies]
        await emit(
            "estimating", "Aggregating every match and calculating study assurance", len(studies)
        )
        aggregation = await self.repo.aggregate(query, pico.sesoi, pico.effectType)
        plan = request.model_dump()
        plan.update(sesoi=pico.sesoi, effectType=pico.effectType)
        verdicts = {d["id"]: assign_bucket(d, pico.sesoi, pico.effectType) for d in studies}
        question = {
            "idea": request.idea,
            "intervention": pico.intervention,
            "comparator": pico.comparator,
            "outcome": pico.outcome,
        }
        outcome_groups = await self.group_outcomes(question, studies, verdicts, usage, warnings)
        stats = await asyncio.to_thread(
            analyze_studies, studies, plan, aggregation["fileDrawer"], outcome_groups
        )
        warnings.extend(stats.get("warnings", []))
        effect_trend = await self.effect_trend(
            request.idea, studies, verdicts, aggregation, stats, usage, warnings
        )
        if mode == "bm25" and not any("BM25" in warning for warning in warnings):
            warnings.append(
                "This deployment is configured for BM25 retrieval; enable local embeddings for hybrid search."
            )
        if aggregation["total"] == 0:
            summary = "No indexed primary studies matched this question. Try narrower intervention and outcome terms, or ingest more source records. This is a coverage gap, not evidence of no effect."
        elif not studies:
            summary = f"{aggregation['total']} indexed primary studies matched, but only reviews reached the retrieved page. Narrow the query to inspect primary evidence; no planning estimate is available from this page."
        else:
            c = aggregation["bucketCounts"]
            summary = (
                f"The full indexed match set contains {aggregation['total']} primary studies: "
                f"{c['effect']} reported effects, {c['credible_null']} credible nulls, "
                f"{c.get('reported_null', 0)} reported nulls of unverified magnitude, "
                f"{c['failed']} methodological failures, "
                f"and {c['unreported']} overdue unreported trials. "
                + inconclusive_sentence(c["inconclusive"], aggregation.get("inconclusiveReasons"))
                + "Text-only classifications do not establish meaningful effect size. "
            )
        drivers = stats.get("warnings", [])[:4]
        if studies and stats.get("assurance") is None:
            summary += " A compatible numerical evidence pool for this study plan is unavailable, so assurance and expected value are not estimated. Missing reports have unknown outcomes."
        # Insufficient evidence gets deterministic narration. A model must not turn
        # a classifier-only positive or another endpoint into a compatible result.
        if studies and self.config.openai_api_key and stats.get("assurance") is not None:
            try:
                narrative = await self.llm.narrate(
                    {
                        "counts": aggregation["bucketCounts"],
                        "matched": aggregation["total"],
                        "scope": "Full lexical matches plus relevance-screened review references; planning uses top retrieved compatible studies.",
                        "sesoi": pico.sesoi,
                        "scale": pico.effectType,
                        "assurance": stats.get("assurance"),
                        "fileDrawer": stats.get("fileDrawer"),
                        "pooledGroups": len(stats.get("pools", [])),
                        "warnings": stats.get("warnings", []),
                        "rows": [
                            {
                                "id": d["id"],
                                "bucket": assign_bucket(d, pico.sesoi, pico.effectType)["bucket"],
                                "n": d.get("n"),
                                "effect": d.get("estimate"),
                                "scale": d.get("effect_type"),
                                "evidenceTier": assign_bucket(d, pico.sesoi, pico.effectType)[
                                    "evidence_tier"
                                ],
                                "primaryOutcome": d.get("outcome"),
                                "analysisScale": assign_bucket(d, pico.sesoi, pico.effectType)[
                                    "analysis_effect_type"
                                ],
                            }
                            for d in studies[:12]
                        ],
                    },
                    usage,
                )
                summary, drivers = narrative.summary, narrative.drivers
            except Exception as exc:
                logger.warning("Narration unavailable: %s", type(exc).__name__)
                warnings.append(
                    "AI narration was unavailable; the summary reports computed counts directly."
                )
        groups = Counter(d.get("population", "").strip() for d in studies if d.get("population"))
        alternatives = [
            {
                "label": population,
                "reason": "Sparse representation in retrieved studies; investigate coverage before treating this as an untested population.",
                "evidenceCount": count,
            }
            for population, count in sorted(groups.items(), key=lambda x: x[1])[:3]
        ]
        for term in aggregation["nullTerms"][:3]:
            alternatives.append(
                {
                    "label": f"Reconsider outcome/context: {term['term']}",
                    "reason": "Over-represented in null-result abstracts (credible or reported); inspect the source studies before reusing this design.",
                    "evidenceCount": term["count"],
                }
            )
        ev = stats.get("expectedValue")
        recommendation = (
            "pursue_with_changes" if ev is None else ("pursue" if ev >= 0 else "deprioritize")
        )
        return {
            "queryId": f"q_{uuid4().hex}",
            "idea": request.idea,
            "field": request.field,
            "keywords": list(
                dict.fromkeys(filter(None, [pico.intervention, pico.outcome, *pico.synonyms]))
            ),
            "searchedSources": ["openalex", "clinicaltrials"],
            "totalScanned": aggregation["total"],
            "papers": [to_paper(d, pico.sesoi, pico.effectType) for d in studies[:50]],
            "summary": summary,
            "estimate": {
                "pSuccess": stats.get("assurance"),
                "expectedValue": ev,
                "confidence": "low",
                "recommendation": recommendation,
                "drivers": drivers,
            },
            "completedAt": datetime.now(UTC).isoformat(),
            "pico": pico.model_dump(),
            "bucketCounts": aggregation["bucketCounts"],
            "inconclusiveReasons": aggregation.get("inconclusiveReasons"),
            "yearCounts": aggregation["yearCounts"],
            "countScope": "All indexed lexical matches plus screened review references; reviews excluded. Planning uses compatible studies among the top retrieved results.",
            "nullTerms": aggregation["nullTerms"],
            "statistics": stats,
            "costs": usage.summary([d.get("abstract", "") for d in targets], self.config),
            "warnings": list(dict.fromkeys(warnings)),
            "alternativeRoutes": alternatives,
            "effectTrend": effect_trend,
            "retrieval": {
                "mode": mode,
                "expanded": len(expanded),
                "durationMs": round((perf_counter() - started) * 1000),
            },
            "spin": aggregation["spin"],
        }
