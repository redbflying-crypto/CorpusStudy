"""CCEL ThML commentary parser (HTML/Word export, e.g. `calcom.doc`).

CCEL texts mark every Bible reference as `<scripRef osisRef="Bible:Gen.1.1">`. In a
commentary, an excerpt opens with a *lemma* paragraph: the verse quoted in italics followed by
its reference (sometimes then "(Calvin's wording.)"). The excerpt runs until the next lemma or
heading and is keyed to the lemma's passage; references inside the comment are kept as
`mentions`.

Only the author's text is kept: editors' introductions (everything before a "THE TEXT"
heading), footnotes, page-break numbers and the indexes are dropped.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from corpusstudy.canon import BOOKS_BY_OSIS
from corpusstudy.ingest.raw import Excerpt
from corpusstudy.ingest.text import clean_text

_HEADING = re.compile(r"<(h[1-6])[^>]*>(.*?)</\1>", re.S)
_PARA = re.compile(r'<p class="?normal"?[^>]*\bid="([^"]+)"[^>]*>(.*?)</p>', re.S)
_SCRIPREF = re.compile(r"<scripRef\b[^>]*?osisRef=\"([^\"]+)\"[^>]*>(.*?)</scripRef>", re.S)
_PAGE = re.compile(r'<span class="pb">\s*\d+\s*</span>')
_FOOTNOTE = re.compile(r"<a\b[^>]*(?:_ftn|MsoFootnoteReference)[^>]*>.*?</a>", re.S)
_TAG = re.compile(r"<[^>]+>")
_TRAILER = re.compile(r"(\s*\([^()]{0,60}\)\s*\.?|\s*\[\d+\]|\s*\.)+\s*$")


def _plain(fragment: str) -> str:
    fragment = _PAGE.sub("", fragment)
    fragment = _FOOTNOTE.sub("", fragment)
    return clean_text(html.unescape(_TAG.sub("", fragment)))


def _heading(text: str) -> str:
    """'II. PREFACE TO OLIVÉTAN’S NEW TESTAMENT' -> 'II. Preface To Olivétan’s New Testament'."""
    if not text.isupper():
        return text
    words = []
    for w in text.split():
        words.append(w if re.fullmatch(r"[IVXLC]+\.?", w) else w[:1] + w[1:].lower())
    return " ".join(words)


def normalise_osis(osis_ref: str) -> list[str]:
    """'Bible:Gen.1.1-Gen.1.3 Bible:Exod.22.21' -> ['Gen.1.1-Gen.1.3', 'Exod.22.21'].

    References to books outside the canon, or without chapter and verse, are dropped.
    """
    out = []
    for part in osis_ref.split():
        part = part.removeprefix("Bible:").split("!")[0]
        if not part:
            continue
        book = part.split(".")[0]
        if book in BOOKS_BY_OSIS and len(part.split("-")[0].split(".")) >= 2:
            out.append(part)
    return out


def _is_lemma(para: str) -> bool:
    """A paragraph that is mostly an italic quotation and ends with its scripRef."""
    refs = list(_SCRIPREF.finditer(para))
    if not refs:
        return False
    last = refs[-1]
    after = _plain(para[last.end() :])
    if after and not _TRAILER.fullmatch(" " + after):
        return False
    before = para[: last.start()]
    text = _plain(before)
    italic = sum(len(_plain(m)) for m in re.findall(r"<i>(.*?)</i>", before, re.S))
    return bool(text) and italic >= 0.5 * len(text)


def parse_ccel_thml(path: str | Path, *, text_marker: str = "THE TEXT") -> list[Excerpt]:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    body = raw[raw.find("<body") :]
    # Indexes and the footnote block at the end are not part of the text.
    for marker in ('<p class="div1">Indexes</p>', 'class="MsoFootnoteText"'):
        cut = body.find(marker)
        if cut != -1:
            body = body[:cut]

    events: list[tuple[int, str, str, str]] = []
    for m in _HEADING.finditer(body):
        events.append((m.start(), "h", m.group(1), _plain(m.group(2))))
    for m in _PARA.finditer(body):
        events.append((m.start(), "p", m.group(1), m.group(2)))
    events.sort()

    excerpts: list[Excerpt] = []
    headings: dict[int, str] = {}
    in_text = False
    current: Excerpt | None = None

    def topic() -> tuple[str, ...]:
        return tuple(
            headings[level]
            for level in sorted(headings)
            if headings[level] and headings[level].upper() != text_marker
        )

    for _, kind, tag_or_id, content in events:
        if kind == "h":
            level = int(tag_or_id[1])
            if content.upper() == text_marker:
                in_text = True
                continue
            if level <= 2:
                in_text = False  # a new part starts with the editor's introduction
            headings = {lvl: h for lvl, h in headings.items() if lvl < level}
            if level > 1:  # h1 is the book title
                headings[level] = _heading(content)
            current = None
            continue
        if not in_text:
            continue
        para_id, para = tag_or_id, content
        text = _plain(para)
        if not text:
            continue
        refs = [r for m in _SCRIPREF.finditer(para) for r in normalise_osis(m.group(1))]
        if _is_lemma(para) or current is None:
            current = Excerpt(id=para_id, topic=topic())
            excerpts.append(current)
            if _is_lemma(para):
                lemma = _SCRIPREF.findall(para)[-1][0]
                current.refs = normalise_osis(lemma)
                refs = [r for r in refs if r not in current.refs]
        current.paragraphs.append(text)
        current.mentions.extend(r for r in refs if r not in current.mentions)
    return excerpts
