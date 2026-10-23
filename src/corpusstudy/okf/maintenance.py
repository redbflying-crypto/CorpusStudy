"""Phase 9: keeping the knowledge bundle healthy. Claude proposes; the reviewer decides.

`python -m corpusstudy.okf maintain` writes a report that needs no API key:

- checks       errors and warnings from `okf check`
- review queue drafts awaiting review, those that answer the most questions first
- changes      reviewed concepts edited since (back to draft), stale or soon stale concepts,
               reviewed concepts whose source was regenerated after the review
- links        broken links, links to deprecated concepts, orphans
- coverage     concepts per type, books of the Bible without a Book concept
- gaps         questions no concept covers, from the evaluation sets and, when enabled,
               the gap log

A question is covered by a concept the question names (title or alias), or one retrieved
for it by more than loose keywords or graph neighbourhood: a phrase, all of its words, or a
reference it cites. Questions that need no concept (lookups, cross-references, pastoral
care, refusals, medical, legal or financial advice) are left out.

The gap log (`CORPUS_GAP_LOG`, off by default) is the one place the bot writes questions
down: topical questions answered without any reviewed concept, one JSON line each. It is
meant for your own use on your own computer; delete the file to forget them.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from corpusstudy.canon import BOOKS, find_book
from corpusstudy.config import REPO_ROOT, Language
from corpusstudy.okf.bundle import Bundle, Issue
from corpusstudy.okf.graph import build_graph, usable
from corpusstudy.okf.models import Status, actor_kind
from corpusstudy.okf.profile import check_bundle
from corpusstudy.okf.references import SOURCES_DIR
from corpusstudy.schemas import Route

EVAL_SETS = (REPO_ROOT / "eval" / "answers.yaml", REPO_ROOT / "eval" / "answers_holdout.yaml")
TOPICAL = {Route.KNOWLEDGE, Route.HYBRID, Route.INTERPRETIVE, Route.RAG, Route.COMPARE}
STRONG = {"concept", "phrase", "all", "reference"}  # retrieval lists that show real coverage
DUE_WITHIN = timedelta(days=30)


# ----------------------------------------------------------------------------- questions


@dataclass
class Question:
    text: str
    language: Language
    origin: str  # "eval/answers.yaml", "gap log", ...
    count: int = 1


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def record_gap(path: Path, question: str, language: Language, route: str,
               now: datetime | None = None) -> None:  # fmt: skip
    """Append one question to the gap log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    at = (now or datetime.now(UTC)).replace(microsecond=0).isoformat()
    line = json.dumps({"at": at, "q": question, "lang": str(language), "route": route},
                      ensure_ascii=False)  # fmt: skip
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_questions(paths: list[Path]) -> list[Question]:
    """Questions from evaluation sets (YAML lists of {q, lang}) and gap logs (JSON lines),
    repeated questions counted once."""
    found: dict[str, Question] = {}
    for path in paths:
        if not path.exists():
            continue
        origin = (path.relative_to(REPO_ROOT).as_posix() if path.is_relative_to(REPO_ROOT)
                  else path.name)  # fmt: skip
        if path.suffix == ".jsonl":
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()]  # fmt: skip
            origin = "gap log"
        else:
            rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for row in rows:
            text = str(row.get("q") or "").strip()
            if not text:
                continue
            key = _key(text)
            if key in found:
                found[key].count += 1
            else:
                found[key] = Question(text, Language(row.get("lang", "en")), origin)
    return list(found.values())


# ----------------------------------------------------------------------------- coverage


@dataclass
class Coverage:
    question: Question
    route: str
    concepts: list[str]  # every concept that covers it, reviewed or not
    reviewed: list[str]  # those answers may use now

    @property
    def status(self) -> str:
        if self.reviewed:
            return "covered"
        return "drafts only" if self.concepts else "gap"


def needs_concept(routing: Any) -> bool:
    """Whether a routed question is one a knowledge concept should cover."""
    return (routing.route in TOPICAL and not routing.advice and not routing.concern
            and not routing.invalid_refs)  # fmt: skip


def covering(search: Any, text: str, language: Language, k: int = 12) -> list[str]:
    """Concepts that cover a question, named ones first. `search` should include drafts."""
    found = list(search.named_concepts(text, language))
    for r in search.search(text, language, k=k):
        cid = r.chunk.concept_id
        if cid and {v.split(":", 1)[0] for v in r.via} & STRONG:
            found.append(cid)
    return list(dict.fromkeys(found))


def coverage(questions: list[Question], search: Any) -> list[Coverage]:
    from corpusstudy.router import route

    reviewed = {cid for cid, c in search.bundle.concepts.items() if usable(c, use_drafts=False)}
    out = []
    for q in questions:
        routing = route(q.text, q.language, search)
        if not needs_concept(routing):
            continue
        concepts = covering(search, q.text, q.language)
        out.append(Coverage(q, str(routing.route), concepts,
                            [c for c in concepts if c in reviewed]))  # fmt: skip
    return out


def gap_terms(gaps: list[Coverage], top: int = 12) -> list[tuple[str, int]]:
    """Content words that recur across the gap questions: candidate topics."""
    from corpusstudy.indexing.analysis import STOPWORDS, fold, tokens

    counts: Counter[str] = Counter()
    for g in gaps:
        lang = g.question.language
        words = {w for w in tokens(fold(g.question.text), lang)
                 if len(w) > 3 and w not in STOPWORDS[lang]}  # fmt: skip
        for w in words:
            counts[w] += g.question.count
    return [(w, n) for w, n in counts.most_common(top) if n > 1]


# ----------------------------------------------------------------------------- report


@dataclass
class ReviewItem:
    concept: str
    title: str
    drafted: datetime | None
    by: str
    answers: int = 0  # questions it would answer that no reviewed concept covers
    changed_after_review: bool = False


@dataclass
class Report:
    at: datetime
    concepts: int
    by_type: dict[str, int]
    tiers: dict[str, int]
    errors: list[Issue]
    warnings: list[Issue]
    review_queue: list[ReviewItem]
    stale: list[tuple[str, datetime]]
    due: list[tuple[str, datetime]]
    source_updates: list[tuple[str, str]]
    broken: list[tuple[str, str]]
    deprecated_links: list[tuple[str, str]]
    orphans: list[str]
    missing_books: list[str]
    coverage: list[Coverage] | None = None  # None when the search index is not built
    note: str = ""
    gaps: list[Coverage] = field(default_factory=list)


def build_report(bundle: Bundle, *, search: Any = None, questions: list[Question] | None = None,
                 now: datetime | None = None) -> Report:  # fmt: skip
    now = now or datetime.now(UTC)
    issues = list(bundle.issues) + check_bundle(bundle)
    graph = build_graph(bundle)
    knowledge = {cid: c for cid, c in bundle.concepts.items() if c.meta.type != "Reference"}

    queue = []
    for cid, c in knowledge.items():
        meta = c.meta
        if meta.lifecycle != Status.DRAFT:
            continue
        human = [v for v in meta.verified if actor_kind(v.by) == "human"]
        gen = meta.generated
        queue.append(ReviewItem(cid, c.title, gen.at if gen else None, gen.by if gen else "?",
                                changed_after_review=bool(human)))  # fmt: skip

    stale, due = [], []
    for cid, c in knowledge.items():
        when = c.meta.stale_after
        if when is None or c.meta.lifecycle == Status.DEPRECATED:
            continue
        if c.meta.is_stale(now):
            stale.append((cid, when))
        elif when - now <= DUE_WITHIN:
            due.append((cid, when))

    # A reviewed concept citing a source regenerated after the review: re-read its quotes.
    regenerated = {cid: c.meta.generated.at for cid, c in bundle.concepts.items()
                   if cid.startswith(SOURCES_DIR + "/") and c.meta.generated
                   and c.meta.generated.at}  # fmt: skip
    source_updates = []
    for cid, c in knowledge.items():
        last = c.meta.last_verified
        if c.meta.lifecycle != Status.STABLE or last is None:  # drafts get a full review
            continue
        for src in sorted(graph.cites.get(cid, ())):
            if src in regenerated and regenerated[src] > last:
                source_updates.append((cid, src))

    deprecated = {cid for cid, c in knowledge.items() if c.meta.lifecycle == Status.DEPRECATED}
    deprecated_links = sorted((src, dst) for src, outs in graph.out.items() for dst in outs
                              if dst in deprecated and src not in deprecated)  # fmt: skip

    with_book = set()
    for c in knowledge.values():
        if c.meta.type == "Bible Book" and (book := find_book(c.title)):
            with_book.add(book.osis)
    missing_books = [b.name_en for b in BOOKS if b.osis not in with_book]

    by_type = Counter(c.meta.type for c in knowledge.values())
    tiers = Counter(c.meta.trust_tier.value for c in knowledge.values())
    report = Report(
        at=now.replace(microsecond=0),
        concepts=len(knowledge),
        by_type=dict(sorted(by_type.items())),
        tiers=dict(sorted(tiers.items())),
        errors=[i for i in issues if i.level == "error"],
        warnings=[i for i in issues if i.level == "warning"],
        review_queue=queue,
        stale=sorted(stale),
        due=sorted(due),
        source_updates=source_updates,
        broken=sorted(graph.broken),
        deprecated_links=deprecated_links,
        orphans=graph.orphans(),
        missing_books=missing_books,
    )
    if search is None:
        report.note = "search index not built: question coverage skipped"
    elif questions:
        report.coverage = coverage(questions, search)
        report.gaps = [c for c in report.coverage if c.status == "gap"]
        waiting = Counter(cid for cov in report.coverage if cov.status == "drafts only"
                          for cid in cov.concepts)  # fmt: skip
        for item in queue:
            item.answers = waiting.get(item.concept, 0)
    queue.sort(key=lambda i: (-i.answers, not i.changed_after_review, i.concept))
    return report


# ----------------------------------------------------------------------------- rendering


def _day(when: datetime | None) -> str:
    return when.date().isoformat() if when else "?"


def render(report: Report, *, limit: int = 25) -> str:
    """The report as Markdown (a CI job summary, an issue, or a file)."""
    r = report
    tiers = ", ".join(f"{n} {t}" for t, n in r.tiers.items())
    types = ", ".join(f"{n} {t}" for t, n in r.by_type.items())
    lines = [f"# Knowledge maintenance report ({_day(r.at)})", "",
             f"{r.concepts} knowledge concepts ({types}); trust: {tiers}.", ""]  # fmt: skip

    lines += ["## Checks", ""]
    if r.errors:
        lines += [f"- **error** {i.path}: {i.message}" for i in r.errors[:limit]]
    lines.append(f"{len(r.errors)} errors, {len(r.warnings)} warnings (`okf check` lists them).")
    lines.append("")

    lines += ["## Review queue", ""]
    if r.review_queue:
        lines += ["| Concept | Drafted | Answers | Note |", "|---|---|---|---|"]
        for item in r.review_queue[:limit]:
            note = "edited since your review" if item.changed_after_review else ""
            answers = str(item.answers) if r.coverage is not None else "-"
            lines.append(f"| `{item.concept}` ({item.title}) | {_day(item.drafted)} by "
                         f"{item.by} | {answers} | {note} |")  # fmt: skip
        if len(r.review_queue) > limit:
            lines.append(f"| ... and {len(r.review_queue) - limit} more | | | |")
        lines += ["", "Answers: questions it covers that no reviewed concept does; reviewing "
                  "those first helps the bot most. Approve with "
                  "`okf verify <concept>`.", ""]  # fmt: skip
    else:
        lines += ["Nothing awaits review.", ""]

    changes = [f"- `{c}` is stale since {_day(w)}: re-review, then raise `stale_after`"
               for c, w in r.stale]  # fmt: skip
    changes += [f"- `{c}` becomes stale on {_day(w)}" for c, w in r.due]
    changes += [f"- `{c}` cites `{s}`, regenerated after your review: re-read its quotations"
                for c, s in r.source_updates]  # fmt: skip
    lines += ["## Changes since review", "", *(changes or ["None."]), ""]

    links = [f"- broken: `{a}` -> `{b}`" for a, b in r.broken]
    links += [f"- to a deprecated concept: `{a}` -> `{b}`" for a, b in r.deprecated_links]
    links += [f"- orphan (no links in or out): `{c}`" for c in r.orphans]
    lines += ["## Links", "", *(links or ["No broken links, deprecated targets or orphans."]), ""]

    lines += ["## Coverage", ""]
    lines.append(f"Books of the Bible without a Book concept ({len(r.missing_books)} of "
                 f"{len(BOOKS)}): {', '.join(r.missing_books) or 'none'}.")  # fmt: skip
    lines.append("")
    if r.coverage is None:
        lines += [f"Question coverage not computed ({r.note or 'no questions'}).", ""]
    else:
        counts = Counter(c.status for c in r.coverage)
        lines.append(f"{len(r.coverage)} topical questions: {counts['covered']} covered by a "
                     f"reviewed concept, {counts['drafts only']} by drafts only, "
                     f"{counts['gap']} by no concept.")  # fmt: skip
        lines.append("")
        lines += ["## Gaps", ""]
        if r.gaps:
            for g in sorted(r.gaps, key=lambda g: (-g.question.count, g.question.text))[:limit]:
                times = f" (asked {g.question.count} times)" if g.question.count > 1 else ""
                lines.append(f"- {g.question.text} [{g.question.language}, {g.route}, "
                             f"{g.question.origin}]{times}")  # fmt: skip
            terms = gap_terms(r.gaps)
            if terms:
                lines += ["", "Recurring words: " + ", ".join(f"{w} ({n})" for w, n in terms)]
            lines += ["", "To propose a concept for a gap (needs the API key; the draft is "
                      "for your review and never used before it): `okf propose <Type> <slug> "
                      '"<Title>" --question "<question>"`.', ""]  # fmt: skip
        else:
            lines += ["Every topical question is covered by some concept.", ""]
    return "\n".join(lines).rstrip() + "\n"


def as_json(report: Report) -> dict[str, Any]:
    r = report
    return {
        "at": r.at.isoformat(),
        "concepts": r.concepts,
        "by_type": r.by_type,
        "tiers": r.tiers,
        "errors": [str(i) for i in r.errors],
        "warnings": len(r.warnings),
        "review_queue": [
            {
                "concept": i.concept,
                "answers": i.answers,
                "changed_after_review": i.changed_after_review,
            }
            for i in r.review_queue
        ],  # fmt: skip
        "stale": [c for c, _ in r.stale],
        "due": [c for c, _ in r.due],
        "source_updates": [list(p) for p in r.source_updates],
        "broken": [list(p) for p in r.broken],
        "deprecated_links": [list(p) for p in r.deprecated_links],
        "orphans": r.orphans,
        "missing_books": r.missing_books,
        "coverage": None
        if r.coverage is None
        else [
            {
                "q": c.question.text,
                "lang": str(c.question.language),
                "route": c.route,
                "status": c.status,
                "concepts": c.concepts,
            }
            for c in r.coverage
        ],  # fmt: skip
    }
