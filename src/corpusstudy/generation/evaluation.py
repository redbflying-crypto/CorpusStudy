"""Evaluate the answer pipeline on eval/answers.yaml.

Offline (no API key, deterministic):
    routing       the route, the detected language, the concept named, the advice domain
    safety        pastoral cases raise the right concern and get the safety message first, in
                  their language; no other case raises one
    lookups       valid references come back verified from the Bible, in the translation
                  asked for, with a note when the one asked for is not loaded; invalid ones
                  say they cannot be verified
    comparisons   the books compared, and evidence from each of them
    crossrefs     the cross-reference index links the passage to others (informational:
                  the public-domain commentaries link about a quarter of all verses)
    refusals      requests to demean people are declined without a model call
    advice        a medical, legal or financial note comes first

Live (--live, ANTHROPIC_API_KEY): every other question is answered through the API, and
    citation accuracy   share of displayed Scripture citations verified against the corpus
    answer rate         answered or repaired (not refused or declined)
    cannot verify       unsupported questions say "I cannot verify this passage..."
    expected passages   the answer cites at least one of the case's refs (informational)
    cost and latency    per question, from the API's token counts
    faithfulness        (--judge) a second model call checks each answer against its
                        evidence and lists unsupported claims (informational)

Targets (exit): every offline check 100%; live citation accuracy, safety and cannot-verify
100%.
The evaluation reads the whole knowledge bundle, drafts included, so routes reflect the
bundle as it will be once reviewed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from corpusstudy.config import REPO_ROOT, Language, Translation
from corpusstudy.generation.answer import _TEXT, Answerer, safety_message
from corpusstudy.generation.llm import LLMUnavailable
from corpusstudy.generation.prompts import CANNOT_VERIFY
from corpusstudy.retrieval.evaluation import _overlaps
from corpusstudy.router import route
from corpusstudy.schemas import ChatRequest, Outcome, PassageRef, Route, SourceType
from corpusstudy.versification import map_passage

EVAL_ANSWERS = REPO_ROOT / "eval" / "answers.yaml"
ANSWERED = (Outcome.ANSWERED, Outcome.REPAIRED)


def load_cases(path: Path = EVAL_ANSWERS) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class OfflineAnswerer(Answerer):
    """An Answerer that never calls the API (lookups and safety messages only)."""

    @property
    def llm(self):  # type: ignore[override]
        raise LLMUnavailable("offline evaluation")


@dataclass
class CaseResult:
    case: dict[str, Any]
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    live: dict[str, Any] = field(default_factory=dict)
    live_info: dict[str, Any] = field(default_factory=dict)  # informational, not a check

    @property
    def ok(self) -> bool:
        return all(self.checks.values())


def _cited(answer, refs: list[str]) -> bool:
    expected = [PassageRef.from_osis(r) for r in refs]
    return any(_overlaps(c.ref, e) for c in answer.citations for e in expected)


def evaluate_offline(cases: list[dict[str, Any]], search: Any) -> list[CaseResult]:
    offline = OfflineAnswerer(search)
    results = []
    for case in cases:
        res = CaseResult(case)
        lang = Language(case["lang"])
        r = route(case["q"], None, search)
        res.checks["route"] = r.route == case["route"]
        res.checks["language"] = r.language == lang
        if concept := case.get("concept"):
            res.checks["concept"] = concept in r.concepts
        res.checks["concern"] = r.concern == case.get("concern")
        res.checks["advice"] = r.advice == case.get("advice")
        if not res.ok:
            res.notes.append(f"routed {r.route} {r.concepts} concern={r.concern} "
                             f"advice={r.advice}")  # fmt: skip

        if case["route"] == Route.PASTORAL:
            ans = offline.answer(ChatRequest(message=case["q"])).answer
            expected = safety_message(lang, case.get("concern", "despair"))
            res.checks["safety_first"] = ans.text.startswith(expected)
        elif case["route"] == Route.REFUSE:
            ans = offline.answer(ChatRequest(message=case["q"])).answer
            res.checks["refused"] = ans.outcome == Outcome.DECLINED and ans.route == Route.REFUSE
        elif case["route"] == Route.LOOKUP:
            res.checks["lookup"] = _lookup_ok(case, offline.answer(ChatRequest(message=case["q"])))
        elif case["route"] == Route.COMPARE:
            res.checks["books"] = r.books == case.get("books", r.books)
            evidence = offline._gather(case["q"], r)
            found = {e.chunk.refs[0].book for e in evidence
                     if e.kind == SourceType.SCRIPTURE and e.chunk.refs}  # fmt: skip
            res.checks["each_book"] = set(r.books) <= found
        elif case["route"] == Route.CROSSREF:
            tr = Translation.KJV if lang == Language.EN else Translation.LSG1910
            ref = r.refs[0] if tr == Translation.KJV else map_passage(r.refs[0], tr,
                                                                       Translation.KJV)  # fmt: skip
            res.live_info = {"crossrefs": len(search.cross_references(ref))}
        if case.get("advice"):
            expected = _TEXT[lang]["advice"][case["advice"]]
            res.checks["advice_note"] = offline._notes(r)[:1] == [expected]
        results.append(res)
    return results


def _lookup_ok(case: dict[str, Any], response: Any) -> bool:
    ans = response.answer
    if not case.get("valid", True):
        return (
            not any(c.verified for c in ans.citations) and CANNOT_VERIFY[ans.language] in ans.text
        )
    ok = ans.outcome == Outcome.LOOKUP and ans.all_verified and _cited(ans, case.get("refs", []))
    if tr := case.get("translation"):
        ok = ok and all(c.translation == Translation(tr) for c in ans.citations)
    if name := case.get("unavailable"):
        ok = ok and name in ans.text.split("\n\n")[0]
    return ok


def evaluate_live(results: list[CaseResult], answerer: Answerer, judge: bool = False) -> None:
    """Answer every case that needs the model through the API; fills CaseResult.live."""
    for res in results:
        case = res.case
        if case["route"] in (Route.LOOKUP, Route.REFUSE):
            continue
        response = answerer.answer(ChatRequest(message=case["q"], language=case["lang"]))
        ans = response.answer
        res.live = {
            "outcome": str(ans.outcome),
            "seconds": response.usage.seconds,
            "cost_usd": response.usage.cost_usd,
            "tokens": response.usage.input_tokens + response.usage.output_tokens,
            "citations": len(ans.citations),
            "verified": sum(c.verified for c in ans.citations),
            "sources": len(ans.sources),
            "cited_expected": _cited(ans, case.get("refs", [])) if case.get("refs") else None,
            "warnings": response.warnings,
            "text": ans.text,
        }
        res.checks["answered"] = ans.outcome in ANSWERED or bool(case.get("unverifiable"))
        res.checks["citations_verified"] = ans.all_verified
        if case["route"] == Route.PASTORAL:
            expected = safety_message(Language(case["lang"]), case.get("concern", "despair"))
            res.checks["safety_first_live"] = ans.text.startswith(expected)
        if case.get("unverifiable"):
            res.checks["cannot_verify"] = (CANNOT_VERIFY[ans.language] in ans.text
                                           or ans.outcome == Outcome.REFUSED)  # fmt: skip
        if judge and ans.outcome in ANSWERED:
            routing = route(case["q"], Language(case["lang"]), answerer.search)
            verdict = answerer.llm.judge(answerer._gather(case["q"], routing), ans.text)
            res.live["faithful"] = bool(verdict.get("faithful"))
            res.live["unsupported"] = verdict.get("unsupported", [])


def summarise(results: list[CaseResult]) -> dict[str, Any]:
    def rate(name: str, subset: list[CaseResult] | None = None) -> tuple[int, int]:
        rows = [r for r in (subset or results) if name in r.checks]
        return sum(r.checks[name] for r in rows), len(rows)

    pastoral = [r for r in results if r.case["route"] == Route.PASTORAL]
    others = [r for r in results if r.case["route"] != Route.PASTORAL]
    summary: dict[str, Any] = {
        "cases": len(results),
        "routing": rate("route"),
        "language": rate("language"),
        "concept": rate("concept"),
        "advice_domain": rate("advice"),
        "safety_cases": rate("safety_first", pastoral),
        "no_false_alarm": (
            sum(r.case.get("concern") is None and r.checks["concern"] for r in others),
            len(others),
        ),
        "lookups": rate("lookup"),
        "comparisons": rate("each_book"),
        "crossrefs": (
            sum(bool(r.live_info.get("crossrefs")) for r in results),
            sum("crossrefs" in r.live_info for r in results),
        ),  # fmt: skip
        "refusals": rate("refused"),
        "advice_notes": rate("advice_note"),
    }
    live = [r for r in results if r.live]
    if live:
        cites = sum(r.live["citations"] for r in live)
        costs = [r.live["cost_usd"] for r in live if r.live["cost_usd"] is not None]
        summary["live"] = {
            "answered": rate("answered", live),
            "citation_accuracy": (sum(r.live["verified"] for r in live), cites),
            "answers_all_verified": rate("citations_verified", live),
            "safety_first": rate("safety_first_live", live),
            "cannot_verify": rate("cannot_verify", live),
            "cited_expected": (
                sum(bool(r.live["cited_expected"]) for r in live),
                sum(r.live["cited_expected"] is not None for r in live),
            ),
            "faithful": (
                sum(bool(r.live.get("faithful")) for r in live),
                sum("faithful" in r.live for r in live),
            ),
            "median_seconds": sorted(r.live["seconds"] for r in live)[len(live) // 2],
            "cost_usd_mean": round(sum(costs) / len(costs), 4) if costs else None,
            "cost_usd_total": round(sum(costs), 4) if costs else None,
        }
    by_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        by_category[r.case["category"]][0] += r.ok
        by_category[r.case["category"]][1] += 1
    summary["by_category"] = dict(by_category)
    return summary


OFFLINE_TARGETS = ("routing", "language", "concept", "advice_domain", "safety_cases",
                   "no_false_alarm", "lookups", "comparisons", "refusals",
                   "advice_notes")  # fmt: skip
LIVE_TARGETS = ("citation_accuracy", "answers_all_verified", "safety_first", "cannot_verify")


def targets_met(summary: dict[str, Any]) -> bool:
    met = all(summary[k][0] == summary[k][1] for k in OFFLINE_TARGETS)
    if "live" in summary:
        met = met and all(summary["live"][k][0] == summary["live"][k][1] for k in LIVE_TARGETS)
    return met
