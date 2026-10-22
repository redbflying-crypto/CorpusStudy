"""Retrieval evaluation: recall@k and MRR over `eval/retrieval.yaml`, per category."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml

from corpusstudy.config import REPO_ROOT, Language, Translation
from corpusstudy.indexing.store import KIND
from corpusstudy.retrieval.search import Search
from corpusstudy.schemas import PassageRef, RetrievalResult

EVAL_PATH = REPO_ROOT / "eval" / "retrieval.yaml"
UNGATED = {"paraphrase"}  # tracked but not held to the target until dense vectors exist


@dataclass
class CaseResult:
    case: dict
    rank: int | None  # 1-based rank of the first hit, None if not in the results


def _overlaps(a: PassageRef, b: PassageRef) -> bool:
    if a.book != b.book:
        return False
    a0, a1 = (a.start_chapter, a.start_verse or 0), (a.end_chapter, a.end_verse or 999)
    b0, b1 = (b.start_chapter, b.start_verse or 0), (b.end_chapter, b.end_verse or 999)
    return a0 <= b1 and b0 <= a1


def is_hit(result: RetrievalResult, case: dict) -> bool:
    chunk = result.chunk
    if KIND[chunk.source_type] != case["kind"]:
        return False
    if "topic" in case:
        topic = " ".join(chunk.metadata.get("topic", []))
        return case["topic"] in topic and chunk.work == case.get("work", chunk.work)
    expected = [PassageRef.from_osis(o) for o in case["expect"]]
    if case["kind"] == "bible":  # the answer translation, in its own numbering
        wanted = Translation.LSG1910 if case["lang"] == "fr" else Translation.KJV
        if chunk.metadata.get("numbering") != str(wanted):
            return False
    return any(_overlaps(r, e) for r in chunk.refs for e in expected)


def load_cases(path: Path = EVAL_PATH) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def evaluate(search: Search, cases: list[dict], k: int = 10) -> list[CaseResult]:
    out = []
    for case in cases:
        results = search.search(case["q"], Language(case["lang"]), k=k)
        rank = next((r.rank for r in results if is_hit(r, case)), None)
        out.append(CaseResult(case, rank))
    return out


def summarise(results: list[CaseResult], ks: tuple[int, ...] = (1, 5, 10)) -> dict:
    by_cat: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_cat[r.case["category"]].append(r)

    def stats(rs: list[CaseResult]) -> dict:
        n = len(rs)
        out = {f"recall@{k}": sum(1 for r in rs if r.rank and r.rank <= k) / n for k in ks}
        out["mrr"] = sum(1 / r.rank for r in rs if r.rank) / n
        out["n"] = n
        return out

    gated = [r for r in results if r.case["category"] not in UNGATED]
    return {
        "categories": {cat: stats(rs) for cat, rs in sorted(by_cat.items())},
        "gated": stats(gated),
        "all": stats(results),
    }
