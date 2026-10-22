"""Evaluate the answer pipeline on eval/answers.yaml.

Offline (no API key, deterministic):
    routing     the route, the detected language, and the concept named
    safety      pastoral cases raise the right concern and get the safety message first, in
                their language; no other case raises one
    lookups     valid references come back verified from the Bible; invalid ones are reported

Live (--live, ANTHROPIC_API_KEY): every other question is answered through the API, and
    citation accuracy   share of displayed Scripture citations verified against the corpus
    answer rate         answered or repaired (not refused or declined)
    expected passages   the answer cites at least one of the case's refs (informational)

Targets (Phase 7 exit): routing, safety and lookups 100%; live citation accuracy 100%.
The evaluation reads the whole knowledge bundle, drafts included, so routes reflect the
bundle as it will be once reviewed.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from corpusstudy.config import REPO_ROOT, Language
from corpusstudy.generation.answer import Answerer, safety_message
from corpusstudy.generation.llm import LLMUnavailable
from corpusstudy.retrieval.evaluation import _overlaps
from corpusstudy.router import route
from corpusstudy.schemas import ChatRequest, Outcome, PassageRef, Route

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
        if r.route != case["route"] or r.concern != case.get("concern"):
            res.notes.append(f"routed {r.route} {r.concepts} concern={r.concern}")

        if case["route"] == Route.PASTORAL:
            ans = offline.answer(ChatRequest(message=case["q"])).answer
            expected = safety_message(lang, case.get("concern", "despair"))
            res.checks["safety_first"] = ans.text.startswith(expected)
        elif case["route"] == Route.LOOKUP:
            ans = offline.answer(ChatRequest(message=case["q"])).answer
            if case.get("valid", True):
                res.checks["lookup"] = (ans.outcome == Outcome.LOOKUP and ans.all_verified
                                        and _cited(ans, case.get("refs", [])))  # fmt: skip
            else:
                res.checks["lookup"] = not any(c.verified for c in ans.citations)
        results.append(res)
    return results


def evaluate_live(results: list[CaseResult], answerer: Answerer) -> None:
    """Answer every non-lookup case through the API; fills CaseResult.live."""
    for res in results:
        case = res.case
        if case["route"] == Route.LOOKUP:
            continue
        started = time.monotonic()
        response = answerer.answer(ChatRequest(message=case["q"], language=case["lang"]))
        ans = response.answer
        res.live = {
            "outcome": str(ans.outcome),
            "seconds": round(time.monotonic() - started, 1),
            "citations": len(ans.citations),
            "verified": sum(c.verified for c in ans.citations),
            "sources": len(ans.sources),
            "cited_expected": _cited(ans, case.get("refs", [])) if case.get("refs") else None,
            "warnings": response.warnings,
            "text": ans.text,
        }
        res.checks["answered"] = ans.outcome in ANSWERED
        res.checks["citations_verified"] = ans.all_verified
        if case["route"] == Route.PASTORAL:
            expected = safety_message(Language(case["lang"]), case.get("concern", "despair"))
            res.checks["safety_first_live"] = ans.text.startswith(expected)


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
        "safety_cases": rate("safety_first", pastoral),
        "no_false_alarm": (
            sum(r.case.get("concern") is None and r.checks["concern"] for r in others),
            len(others),
        ),  # fmt: skip
        "lookups": rate("lookup"),
    }
    live = [r for r in results if r.live]
    if live:
        cites = sum(r.live["citations"] for r in live)
        summary["live"] = {
            "answered": rate("answered", live),
            "citation_accuracy": (sum(r.live["verified"] for r in live), cites),
            "answers_all_verified": rate("citations_verified", live),
            "safety_first": rate("safety_first_live", live),
            "cited_expected": (
                sum(bool(r.live["cited_expected"]) for r in live),
                sum(r.live["cited_expected"] is not None for r in live),
            ),  # fmt: skip
            "median_seconds": sorted(r.live["seconds"] for r in live)[len(live) // 2],
        }
    by_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        by_category[r.case["category"]][0] += r.ok
        by_category[r.case["category"]][1] += 1
    summary["by_category"] = dict(by_category)
    return summary


def targets_met(summary: dict[str, Any]) -> bool:
    full = ("routing", "language", "concept", "safety_cases", "no_false_alarm", "lookups")
    met = all(summary[k][0] == summary[k][1] for k in full)
    if "live" in summary:
        live = summary["live"]
        keys = ("citation_accuracy", "answers_all_verified", "safety_first")
        met = met and all(live[k][0] == live[k][1] for k in keys)
    return met
