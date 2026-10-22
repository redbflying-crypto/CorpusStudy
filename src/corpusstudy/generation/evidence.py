"""The evidence an answer may draw on: retrieved Scripture, commentary and knowledge.

Commentary and knowledge items get keys (E1, E2, ...) that the answer cites as [E1].
Scripture is cited by its reference in the text ("John 3:16", "Jean 3.16"), never by key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from corpusstudy.config import Language
from corpusstudy.schemas import Chunk, RetrievalResult, SourceType, VerseRef

_VERSE_MARK = re.compile(r"\[(\d+)\]\s*")

WORKS = {
    "henry": "Matthew Henry, Concise Commentary",
    "calvin": "John Calvin, Commentaries (LCC XXIII)",
}


@dataclass(frozen=True)
class Evidence:
    key: str | None  # "E1" for commentary and knowledge; None for Scripture
    chunk: Chunk
    label: str

    @property
    def kind(self) -> SourceType:
        return self.chunk.source_type

    @property
    def text(self) -> str:
        """The text as it may be quoted (Scripture without its [n] verse markers)."""
        if self.kind == SourceType.SCRIPTURE:
            return " ".join(text for _, text in self.verses())
        return self.chunk.text

    def verses(self) -> list[tuple[VerseRef | None, str]]:
        """Scripture as (verse, text) pairs; a psalm title comes first, as verse 0."""
        chunk = self.chunk
        passage = chunk.refs[0] if chunk.refs else None
        parts = _VERSE_MARK.split(chunk.text)
        out: list[tuple[VerseRef | None, str]] = []
        if parts[0].strip():
            title = (VerseRef(book=passage.book, chapter=passage.start_chapter, verse=0)
                     if passage and passage.book == "Ps" else None)  # fmt: skip
            out.append((title, parts[0].strip()))
        chapter, last = (passage.start_chapter if passage else 0), 0
        for number, text in zip(parts[1::2], parts[2::2], strict=True):
            if int(number) <= last:  # a multi-chapter passage moved to the next chapter
                chapter += 1
            last = int(number)
            ref = (VerseRef(book=passage.book, chapter=chapter, verse=last)
                   if passage else None)  # fmt: skip
            out.append((ref, text.strip()))
        return out

    @property
    def concept_id(self) -> str | None:
        return self.chunk.concept_id

    @property
    def trust_tier(self) -> str | None:
        return self.chunk.metadata.get("trust_tier") if self.concept_id else None


def label(chunk: Chunk) -> str:
    if chunk.source_type == SourceType.SCRIPTURE:
        ref = chunk.refs[0].display(chunk.language) if chunk.refs else chunk.id
        return f"{ref} ({chunk.translation})"
    if chunk.source_type == SourceType.COMMENTARY:
        work = WORKS.get(chunk.work or "", chunk.work or "Commentary")
        if chunk.refs:
            return f"{work}, on {chunk.refs[0].display(Language.EN)}"
        topic = chunk.metadata.get("topic") or ""
        return f"{work}, {topic}".rstrip(", ")
    meta = chunk.metadata
    section = meta.get("section")
    title = meta.get("title") or chunk.concept_id or chunk.id
    return f"{title} — {section}" if section else title


def pack(results: list[RetrievalResult | Chunk]) -> list[Evidence]:
    """Evidence in retrieval order, keyed E1, E2, ... for commentary and knowledge. A
    Scripture chunk whose verses all came earlier (overlapping windows) is dropped, and so
    is a repeated chunk."""
    out, n = [], 0
    seen_ids: set[str] = set()
    seen_verses: set[tuple] = set()
    for r in results:
        chunk = r.chunk if isinstance(r, RetrievalResult) else r
        if chunk.id in seen_ids:
            continue
        seen_ids.add(chunk.id)
        if chunk.source_type == SourceType.SCRIPTURE:
            e = Evidence(None, chunk, label(chunk))
            verses = {(chunk.translation, ref.osis) for ref, _ in e.verses() if ref}
            if verses and verses <= seen_verses:
                continue
            seen_verses |= verses
            out.append(e)
        else:
            n += 1
            out.append(Evidence(f"E{n}", chunk, label(chunk)))
    return out
