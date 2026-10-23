"""USFM parser (used for Louis Segond 1910).

Keeps only canonical verse text. Dropped: book introductions (before `\\c 1`), section
headings and parallel-passage lines (`\\s`, `\\r`, `\\ms`, `\\mr`...), footnotes (`\\f...\\f*`),
cross-references (`\\x...\\x*`) and remarks. Character styles (`\\wj`, `\\it`, `\\qs`, `\\w`...)
are unwrapped and their text kept.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from corpusstudy.canon import BOOKS_BY_USFM
from corpusstudy.ingest.raw import RawVerse
from corpusstudy.ingest.text import clean_text

# Paragraph-level markers whose whole line is not Scripture text.
_SKIP_LINE = re.compile(
    r"^\\(?:id|ide|h|toc\d*|mt\d*|mte\d*|imt\d*|is\d*|ip\w*|ipi|im\w*|io\d*|ior|iot|iq\d*|ib\d*|"
    r"ie|ili\d*|iex|s\d*|ms\d*|mr|sr|r|sp|sd\d*|cl|cp|ca|va|rem|periph|sts|restore|"
    r"tr|th\d*|tc\d*|thr\d*|tcr\d*|usfm|lit)\b"
)
_NOTE = re.compile(r"\\(f|fe|x|ef|ex)\s.*?\\\1\*", re.DOTALL)
# \w word|lemma="..."\w*  -> word
_ATTRS = re.compile(r"\|[^\\]*?(?=\\\+?\w+\*)")
_MARKER = re.compile(r"\\\+?[a-z]+\d*\*?\s?")
_VERSE = re.compile(r"\\v\s+(\d+)(?:[-–]\d+)?[a-z]?\s*")


def _strip_markup(text: str) -> str:
    text = _NOTE.sub(" ", text)
    text = _ATTRS.sub("", text)
    text = _MARKER.sub(" ", text)
    return text


def parse_usfm_lines(lines: Iterable[str]) -> Iterator[RawVerse]:
    book: str | None = None
    chapter = 0
    current: tuple[int, int] | None = None
    buf: list[str] = []

    def flush() -> Iterator[RawVerse]:
        if book and current is not None:
            text = clean_text(_strip_markup(" ".join(buf)))
            if text:
                yield RawVerse(book, current[0], current[1], text)

    # Join the file so notes spanning lines can be removed, then re-split on markers that
    # start a new paragraph-level element.
    joined = "\n".join(lines)
    joined = _NOTE.sub(" ", joined)
    for raw_line in joined.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        m = re.match(r"^\\id\s+(\w+)", line)
        if m:
            yield from flush()
            usfm_code = m.group(1).upper()
            canon_book = BOOKS_BY_USFM.get(usfm_code)
            book = canon_book.osis if canon_book else None
            chapter, current, buf = 0, None, []
            continue
        m = re.match(r"^\\c\s+(\d+)", line)
        if m:
            yield from flush()
            chapter, current, buf = int(m.group(1)), None, []
            continue
        if chapter == 0 or _SKIP_LINE.match(line):
            continue
        # A psalm title (\d) is numbered as verse text in LSG; keep it where it falls.
        if line.startswith("\\d "):
            line = line[3:]
        pieces = _VERSE.split(line)
        # pieces = [before_first_v, vnum, text, vnum, text, ...]
        if pieces[0].strip():
            buf.append(pieces[0])
        for i in range(1, len(pieces), 2):
            yield from flush()
            current, buf = (chapter, int(pieces[i])), [pieces[i + 1]]
    yield from flush()


def parse_usfm(path: str | Path) -> Iterator[RawVerse]:
    with open(path, encoding="utf-8-sig") as fh:
        yield from parse_usfm_lines(fh.read().splitlines())


def parse_usfm_dir(directory: str | Path, pattern: str = "*.sfm") -> Iterator[RawVerse]:
    for path in sorted(Path(directory).glob(pattern)):
        yield from parse_usfm(path)
