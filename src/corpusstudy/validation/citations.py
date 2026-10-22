"""Check a generated answer against the corpus before it is shown.

1. References: every Bible reference must exist in the answer language's translation and
   numbering (KJV for English, Louis Segond 1910 for French). A book name followed by
   numbers that do not form a valid reference ("Romans 99:1") is a problem too.
2. Quotations: every quotation of three words or more ("...", “...”, « ... » or a Markdown
   block quote) must occur word for word (case, curly quotes and spacing aside) in the Bible
   text of a passage the answer references, in the Scripture evidence, or in a commentary or
   knowledge item marked [E1] in the same paragraph. An ellipsis (and, in a block quote, a
   bold verse number) splits a quotation into fragments that must each occur.
3. Markers: every [E1] marker must name an evidence item that was provided.
4. Knowledge: every knowledge concept cited must still pass the OKF producer profile, so
   its footnotes resolve to real passages and excerpts.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from corpusstudy.canon import find_book
from corpusstudy.config import Language, get_settings
from corpusstudy.generation.evidence import Evidence
from corpusstudy.okf.quotes import normalise
from corpusstudy.retrieval.references import _CANDIDATE, find_references
from corpusstudy.schemas import Citation, PassageRef, SourceType
from corpusstudy.versification import versification

MIN_QUOTE_WORDS = 3
MARKER = re.compile(r"\[(E\d+(?:\s*,\s*E\d+)*)\]")
_QUOTE = re.compile(r"“([^”]+)”|\"([^\"]+)\"|«\s*([^»]+?)\s*»")
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")
_VERSE_NUMBER = re.compile(r"\*\*\d{1,3}\*\*|^\d{1,3}\s")


@dataclass
class Problem:
    kind: str  # reference | quotation | marker | knowledge
    message: str


@dataclass
class Report:
    problems: list[Problem] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)  # evidence keys, in order of first use

    @property
    def ok(self) -> bool:
        return not self.problems


def markers_in(text: str) -> list[str]:
    keys: dict[str, None] = {}
    for m in MARKER.finditer(text):
        for key in re.split(r"\s*,\s*", m.group(1)):
            keys.setdefault(key, None)
    return list(keys)


def _passage_text(ref: PassageRef, language: Language) -> str | None:
    from corpusstudy.corpus import CorpusNotBuilt, bible

    try:
        verses = bible(get_settings().translation_for(language)).passage(ref)
    except (CorpusNotBuilt, ValueError):
        return None
    return " ".join(v.text for v in verses) or None


def _quotations(paragraph: str) -> list[str]:
    """Quoted strings, and the text of block-quote lines split at verse numbers."""
    quotes = [next(g for g in m.groups() if g) for m in _QUOTE.finditer(paragraph)]
    for line in paragraph.splitlines():
        line = line.strip()
        if not line.startswith(">"):
            continue
        body = line.lstrip("> ").strip()
        for _, start, end in reversed(find_references(body)):  # an attribution
            body = body[:start] + "…" + body[end:]
        body = MARKER.sub("…", body).replace("“", "").replace("”", "").replace('"', "")
        quotes += [piece.strip(" *_—–-()") for piece in _VERSE_NUMBER.split(body)]
    return [q for q in quotes if q]


def _bad_references(text: str, found: list[tuple[PassageRef, int, int]]) -> list[str]:
    """Book names followed by numbers that the parser rejected ("Romans 99:1")."""
    spans = [(s, e) for _, s, e in found]
    bad = []
    for m in _CANDIDATE.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue
        prefix, book, numbers = m.group(1) or "", m.group(2), m.group(3)
        has_verse = bool(re.search(r"\d(?:\s*[:.]\s*|,)\d", numbers))
        if (has_verse or book[0].isupper()) and find_book(f"{prefix}{book}".strip()):
            bad.append(m.group(0).strip())
    return bad


def validate(
    text: str,
    language: Language,
    evidence: list[Evidence],
    *,
    knowledge_check: Callable[[str], list[str]] | None = None,
    passage_text: Callable[[PassageRef, Language], str | None] = _passage_text,
) -> Report:
    """Validate an answer. `knowledge_check(concept_id)` returns a concept's provenance
    errors (see okf.profile); without it knowledge citations are not re-checked."""
    report = Report()
    tr = get_settings().translation_for(language)
    numbering = versification(tr)
    by_key = {e.key: e for e in evidence if e.key}

    # References.
    found = find_references(text)
    for ref, _, _ in found:
        problems = numbering.check_passage(ref)
        for p in problems:
            report.problems.append(Problem("reference", f"{ref.display(language)}: {p}"))
        report.citations.append(Citation(ref=ref, translation=tr, verified=not problems))
    for bad in _bad_references(text, found):
        report.problems.append(Problem("reference", f"{bad!r} is not a valid reference in {tr}"))

    # Markers.
    report.markers = markers_in(text)
    for key in report.markers:
        if key not in by_key:
            report.problems.append(Problem("marker", f"[{key}] does not name a provided source"))

    # Quotations, paragraph by paragraph.
    scripture = [normalise(e.text) for e in evidence if e.kind == SourceType.SCRIPTURE]
    for ref, _, _ in found:
        if not numbering.check_passage(ref) and (body := passage_text(ref, language)):
            scripture.append(normalise(body))
    everything = scripture + [normalise(e.text) for e in evidence]
    for paragraph in re.split(r"\n\s*\n", text):
        quotes = _quotations(paragraph)
        if not quotes:
            continue
        allowed = scripture + [normalise(by_key[k].text) for k in markers_in(paragraph)
                               if k in by_key]  # fmt: skip
        for quote in quotes:
            for frag in _ELLIPSIS.split(quote):
                frag = normalise(MARKER.sub("", frag)).strip(" ,.;:!?")
                if len(frag.split()) < MIN_QUOTE_WORDS or any(frag in t for t in allowed):
                    continue
                where = (
                    "is not in the sources cited in its paragraph"
                    if any(frag in t for t in everything)
                    else "does not occur in the sources provided"
                )
                report.problems.append(Problem("quotation", f'quotation "{frag}" {where}'))

    # Knowledge provenance.
    if knowledge_check is not None:
        seen: set[str] = set()
        for key in report.markers:
            e = by_key.get(key)
            if e is None or not e.concept_id or e.concept_id in seen:
                continue
            seen.add(e.concept_id)
            for err in knowledge_check(e.concept_id):
                report.problems.append(Problem("knowledge", f"{e.concept_id}: {err}"))

    return report
