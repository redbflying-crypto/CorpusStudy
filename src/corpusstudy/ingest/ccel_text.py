"""Parser for CCEL commentaries exported from PDF to plain text (Matthew Henry's Concise).

The PDF text keeps the page furniture, so the parser first removes page numbers and the
running header that follows each one. It then walks the books in canonical order:

    <Book name>                      book introduction
    Chapter N                        chapter heading (numbered in sequence)
    Chapter Outline                  outline items ending with "(1–16)"      (layout A)
    Verses 1–16 / Verse 31           section heading on its own line         (layout A)
    ... (Ezek. 1:1-14) ...           outline items carrying full references  (layout B)
    Ezek. 1:1-14 It is a mercy ...   section starting with its reference     (layout B)

Each section becomes an excerpt keyed to its verse range; each outline becomes an excerpt
keyed to the chapter; each introduction one keyed to the whole book. Bible references in the
comment ("Ro 7:11; Heb 3:13", "I Kin. 11:1") are collected as mentions.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from corpusstudy.canon import BOOKS, Book, find_book
from corpusstudy.ingest.raw import Excerpt
from corpusstudy.ingest.text import clean_text

_PAGE = re.compile(r"\d{1,4}")
_VER_MARK = re.compile(r"#\(([\d–,\- ]+)\)")
_CHAPTER = re.compile(r"Chapter (\d+)")
_VERSES = re.compile(r"Verses? (\d+)(?:\s*[–-]\s*(\d+)|,\s*(\d+))?")
_OUTLINE_ITEM = re.compile(r"^(.*?)\s*\((\d+)(?:\s*[–-]\s*(\d+)|,\s*(\d+))?\)\s*$")
# A reference: optional numeral prefix, a book name or abbreviation, chapter:verse[-verse].
_REF = re.compile(
    r"(?<![\w:])((?:[1-3]|I{1,3})\s?)?([A-Z][a-z]{1,15}(?: of [A-Z][a-z]+)?)\.?\s?"
    r"(\d{1,3}):(\d{1,3})(?:\s?[-–]\s?(\d{1,3})(?!:))?"
)
_BRACKET_REF = re.compile(r"\((?:[1-3]|I{1,3})?\s?[A-Z][a-z]+\.?\s?\d+:\d+")
# "; 62:5" continuing the previous book. Used with .match(text, pos), so no "^" anchor:
# "^" would only ever match at index 0.
_CONT = re.compile(
    r"(?:,\s?\d{1,3}(?![\d:]))*[;,]\s*(\d{1,3}):(\d{1,3})(?:\s?[-–]\s?(\d{1,3})(?!:))?"
)


def strip_page_furniture(lines: list[str], headers: set[str]) -> list[str]:
    """Drop page-number lines and the running header line that follows each of them."""
    out: list[str] = []
    skip_header = False
    for line in lines:
        s = line.strip()
        if skip_header:
            skip_header = False
            if s in headers or _CHAPTER.fullmatch(s):
                continue
        if _PAGE.fullmatch(s):
            skip_header = True
            continue
        out.append(s)
    return out


def join_lines(lines: list[str]) -> str:
    """Join wrapped lines, undoing end-of-line hyphenation ("excep-" + "tional")."""
    text = ""
    for line in lines:
        if not line:
            continue
        if text.endswith("-") and re.search(r"[a-z]-$", text) and line[:1].islower():
            text = text[:-1] + line
        elif re.search(r"\d[-–]$", text) and line[:1].isdigit():
            text += line  # a verse range wrapped after its dash: "1-" + "14"
        else:
            text = f"{text} {line}" if text else line
    return clean_text(text)


def find_refs(
    text: str, abbreviations: dict[str, str], default_book: str | None = None
) -> Iterator[str]:
    """OSIS passages cited in running text. `abbreviations` overrides canon.find_book."""
    pos = 0
    last_book: str | None = None
    while True:
        m = _REF.search(text, pos)
        if not m:
            return
        prefix, name, ch, v1, v2 = m.groups()
        key = f"{prefix.strip() if prefix else ''}{name}"
        osis = abbreviations.get(key) or abbreviations.get(name if not prefix else key)
        book = None
        if osis is None:
            found = find_book(f"{prefix or ''}{name}")
            book = found.osis if found else None
        else:
            book = osis
        pos = m.end()
        if book is None:
            continue
        last_book = book
        yield _osis(book, int(ch), int(v1), int(v2) if v2 else None)
        # Follow "; 62:5; 64:1" continuations in the same book.
        while True:
            c = _CONT.match(text, pos)
            if not c:
                break
            pos = c.end()
            yield _osis(last_book, int(c[1]), int(c[2]), int(c[3]) if c[3] else None)


def _osis(book: str, ch: int, v1: int, v2: int | None = None) -> str:
    start = f"{book}.{ch}.{v1}"
    return start if not v2 or v2 == v1 else f"{start}-{book}.{ch}.{v2}"


def _section_range(m: re.Match[str]) -> tuple[int, int]:
    v1 = int(m[1])
    v2 = int(m[2] or m[3] or v1)
    return v1, max(v1, v2)


class _State:
    def __init__(self, abbreviations: dict[str, str]):
        self.abbreviations = abbreviations
        self.excerpts: list[Excerpt] = []
        self.book: Book | None = None
        self.chapter = 0
        self.kind = ""  # "intro" | "outline" | "section"
        self.buffer: list[str] = []
        self.ref = ""
        self.ref_sections = False  # layout B: sections start with a reference
        self.prev = ""  # last body line, to tell a section start from a wrapped line

    def flush(self) -> None:
        # "ver. #(18)" -> "ver. 18"; a leading em dash opens some chapter comments.
        text = _VER_MARK.sub(r"\1", join_lines(self.buffer)).lstrip("—").strip()
        self.buffer = []
        if not text or self.book is None:
            return
        book = self.book
        topic: tuple[str, ...] = (book.name_en,)
        if self.kind == "intro":
            ex_id, ref = f"{book.osis}.intro", f"{book.osis}.1-{book.osis}.{book.chapters}"
        else:
            topic += (f"Chapter {self.chapter}",)
            if self.kind == "outline":
                ex_id, ref = f"{book.osis}.{self.chapter}.outline", f"{book.osis}.{self.chapter}"
                text = "Chapter outline: " + text
            else:
                ex_id, ref = self.ref, self.ref
        mentions = list(find_refs(text, self.abbreviations))
        self.excerpts.append(
            Excerpt(
                id=ex_id,
                topic=topic,
                refs=[ref],
                mentions=list(dict.fromkeys(mentions)),
                paragraphs=[text],
            )
        )

    def start(self, kind: str, ref: str = "") -> None:
        self.flush()
        self.kind, self.ref = kind, ref


def close_gaps(excerpts: list[Excerpt], verse_count) -> list[str]:
    """Extend a section over verses no section covers (a heading missing in the source).

    `verse_count(book, chapter)` gives the chapter length. Returns the extensions made.
    """
    by_chapter: dict[tuple[str, int], list[Excerpt]] = {}
    for ex in excerpts:
        ref = ex.refs[0] if ex.refs else ""
        parts = ref.split("-")[0].split(".")
        if len(parts) == 3:
            by_chapter.setdefault((parts[0], int(parts[1])), []).append(ex)
    notes = []

    def span(ex: Excerpt) -> tuple[int, int]:
        start, _, end = ex.refs[0].partition("-")
        v1 = int(start.split(".")[2])
        v2 = int(end.split(".")[2]) if end and end.split(".")[1] == start.split(".")[1] else v1
        return v1, v2

    for (book, ch), sections in by_chapter.items():
        sections.sort(key=lambda e: span(e)[0])
        last = verse_count(book, ch)
        for i, ex in enumerate(sections):
            v1, v2 = span(ex)
            nxt = span(sections[i + 1])[0] - 1 if i + 1 < len(sections) else last
            if nxt > v2:
                ex.refs[0] = _osis(book, ch, v1, nxt)
                notes.append(f"{_osis(book, ch, v1, v2)} -> {ex.refs[0]}")
    return notes


def _outline_text(lines: list[str]) -> str:
    """Rebuild two-column outline items: 'God separates ... the wa- (6–13)' + 'ters, and ...'."""
    items: list[str] = []
    for line in lines:
        m = _OUTLINE_ITEM.match(line)
        if m:
            rng = m[2] + (f"-{m[3] or m[4]}" if (m[3] or m[4]) else "")
            items.append(f"{m[1]} ({rng})")
        elif items:
            head, _, rng = items[-1].rpartition(" (")
            items[-1] = f"{join_lines([head, line])} ({rng}"
        else:
            items.append(line)
    return " ".join(i if i.endswith((".", ")")) else i + "." for i in items)


def parse_ccel_text(
    path: str | Path,
    *,
    abbreviations: dict[str, str] | None = None,
    start_marker: str = "Title Page",
    end_marker: str = "Indexes",
) -> list[Excerpt]:
    abbreviations = abbreviations or {}
    raw = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").split("\n")
    # The body starts at the second "Title Page" line (the first is in the contents).
    starts = [i for i, line in enumerate(raw) if line.strip() == start_marker]
    begin = starts[1] + 1 if len(starts) > 1 else 0
    end = next((i for i in range(begin, len(raw)) if raw[i].strip() == end_marker), len(raw))
    headers = {b.name_en for b in BOOKS} | {start_marker}
    lines = strip_page_furniture(raw[begin:end], headers)

    st = _State(abbreviations)
    next_book = 0
    # After a chapter heading: "await" (first line decides), "outline_a", "outline_b", "text".
    mode = "text"
    outline_lines: list[str] = []

    def section_start(line: str) -> tuple[str, str] | None:
        """(ref, rest of line) when the line opens a section like 'Ezek. 1:1-14 It is ...'."""
        m = _REF.match(line)
        if not m or st.book is None:
            return None
        ref = next(find_refs(line[: m.end()], abbreviations), None)
        if not ref or not ref.startswith(f"{st.book.osis}.{st.chapter}."):
            return None
        rest = line[m.end() :]
        pair = re.match(r",\s?(\d+)\b", rest)  # "Jer. 4:1,2 The first two verses ..."
        if pair and "-" not in ref:
            book, ch, v1 = ref.split(".")
            ref = _osis(book, int(ch), int(v1), int(pair[1]))
            rest = rest[pair.end() :]
        rest = rest.lstrip(". ").strip()  # "Jer. 48:1-13. The Chaldeans ..."
        if rest and not (rest[0].isupper() or rest[0] in "“‘\"'"):
            return None
        sentence_end = st.prev.endswith((".", "!", "?", ";", ":", "”", "’", '"', ")"))
        if st.prev and not sentence_end and not follows_current(ref):
            return None  # a reference wrapped onto a new line mid-sentence
        return ref, rest

    def follows_current(ref: str) -> bool:
        """True when `ref` starts on the verse right after the current section ends."""
        if st.kind != "section" or st.ref.count(".") < 2:
            return False
        current_end = int(st.ref.rsplit(".", 1)[1])
        return int(ref.split("-")[0].rsplit(".", 1)[1]) == current_end + 1

    def close_outline() -> None:
        nonlocal mode, outline_lines
        if outline_lines:
            st.start("outline")
            st.buffer = [
                _outline_text(outline_lines) if mode == "outline_a" else join_lines(outline_lines)
            ]
            st.flush()
            st.kind = ""
        mode, outline_lines = "text", []

    for line in lines:
        if next_book < len(BOOKS) and line == BOOKS[next_book].name_en:
            close_outline()
            st.start("intro")
            st.book, st.chapter = BOOKS[next_book], 0
            next_book += 1
            continue
        if st.book is None:
            continue
        osis = st.book.osis
        if line == "Chapter Outline" and st.chapter == 0 and st.kind == "intro" and not st.buffer:
            # 2 Kings: the PDF text has no "Chapter 1" line; the outline follows the book name.
            st.start("")
            st.chapter, st.ref_sections, mode = 1, False, "outline_a"
            continue
        m = _CHAPTER.fullmatch(line)
        if m and int(m[1]) == st.chapter + 2 and st.chapter == 0 and st.kind == "intro":
            # 2 Chronicles: no "Chapter 1" line, so what looked like the introduction is
            # chapter 1. Relabel it, then take the heading as chapter 2.
            st.flush()
            intro = (
                st.excerpts[-1] if st.excerpts and st.excerpts[-1].id == f"{osis}.intro" else None
            )
            if intro is not None:
                intro.id = intro.refs[0] = f"{osis}.1"
                intro.topic = (st.book.name_en, "Chapter 1")
            st.chapter = 1
        if m and int(m[1]) == st.chapter + 1:
            close_outline()
            st.start("")
            st.chapter, st.ref_sections, mode = int(m[1]), False, "await"
            st.prev = ""
            continue
        if mode == "await":
            if line == "Chapter Outline":
                mode = "outline_a"
                continue
            mode = "outline_b" if _BRACKET_REF.search(line) else "text"
            # Without a "Chapter Outline", sections may open with their reference
            # ("Lam. 2:1-9 A sad ..."), with or without a bracketed outline before them.
            st.ref_sections = True
        m = _VERSES.fullmatch(line)
        if m and st.chapter:
            close_outline()
            v1, v2 = _section_range(m)
            st.start("section", _osis(osis, st.chapter, v1, v2))
            continue
        if mode == "outline_b":
            if section_start(line) is None:
                outline_lines.append(line)
                continue
            close_outline()  # the first section starts here
        if mode == "outline_a":
            outline_lines.append(line)
            continue
        if st.ref_sections:
            found = section_start(line)
            if found:
                st.start("section", found[0])
                line = found[1]
                if not line:
                    continue
        if st.kind == "" and st.chapter:
            # Text after a chapter heading with no outline and no section heading.
            st.start("section", f"{osis}.{st.chapter}")
        st.buffer.append(line)
        st.prev = line
    close_outline()
    st.flush()
    return st.excerpts
