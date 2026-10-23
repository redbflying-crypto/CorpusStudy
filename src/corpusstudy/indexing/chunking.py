"""Chunks: the units that search returns.

- Bible: windows of `size` verses moving by `stride` inside a chapter, in each translation's
  own numbering (a psalm title travels with verse 1). A window starts at the beginning of a
  sentence and runs to the end of one (at most `max_size` verses), so no sentence is split;
  each carries its book number and testament.
- Commentary: one chunk per excerpt; long excerpts are split on sentence boundaries.
- Knowledge (OKF concepts): one chunk per `#` section, carrying the concept's trust
  metadata so search can apply the review rules at query time, and what the section holds
  (`content`: summary, biblical_text, historical_context or interpretation). Reference
  concepts (source descriptions) are not content and are skipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from corpusstudy.canon import BOOKS_BY_OSIS
from corpusstudy.commentary import CommentaryExcerpt
from corpusstudy.config import Language, Translation
from corpusstudy.corpus import BibleText
from corpusstudy.okf.bundle import Bundle, Concept
from corpusstudy.schemas import Chunk, PassageRef, SourceType, parse_reference

# What each template section holds (okf.lifecycle.TEMPLATES); unknown headings: summary.
SECTION_CONTENT = {
    "Definition": "summary", "Summary": "summary", "Overview": "summary",
    "Life": "biblical_text", "Development through Scripture": "biblical_text",
    "Outline": "biblical_text", "Key passages": "biblical_text",
    "Setting": "historical_context", "Context": "historical_context",
    "Reformed view": "interpretation", "Where other traditions differ": "interpretation",
    "Significance": "interpretation", "Meaning": "interpretation",
    "Interpretive notes": "interpretation", "Themes": "interpretation",
}  # fmt: skip
_VERSE_END = re.compile(r"[.?!][’'\")\]»]*\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z“\"'(])")
_FOOTNOTE_DEF = re.compile(r"^\[\^[^\]]+\]:.*$", re.M)
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]+\]")
_MD_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\([^)]*\)")


def _ends_sentence(text: str) -> bool:
    return bool(_VERSE_END.search(text))


def bible_chunks(
    text: BibleText, size: int = 3, stride: int = 2, max_size: int = 10
) -> Iterator[Chunk]:
    tr = text.translation
    chapters: dict[tuple[str, int], list] = {}
    for verse in text:
        chapters.setdefault((verse.ref.book, verse.ref.chapter), []).append(verse)
    for (book, ch), verses in chapters.items():
        verses.sort(key=lambda v: v.ref.verse)
        title = [v for v in verses if v.ref.verse == 0]
        numbered = [v for v in verses if v.ref.verse > 0]
        starts = list(range(0, max(len(numbered) - size, 0) + 1, stride))
        if starts[-1] + size < len(numbered):
            starts.append(len(numbered) - size)  # the last window reaches the chapter end
        seen: set[tuple[int, int]] = set()
        for i in starts:
            begin = i  # back to the start of the sentence (a few verses at most)
            while (
                begin > 0
                and i - begin < max_size - size
                and not _ends_sentence(numbered[begin - 1].text)
            ):
                begin -= 1
            end = min(i + size, len(numbered))  # forward to the end of a sentence
            while (
                end < len(numbered)
                and end - begin < max_size
                and not _ends_sentence(numbered[end - 1].text)
            ):
                end += 1
            if (begin, end) in seen:
                continue
            seen.add((begin, end))
            window = numbered[begin:end]
            if begin == 0:
                window = title + window
            first, last = window[0].ref, window[-1].ref
            passage = PassageRef(
                book=book,
                start_chapter=ch,
                start_verse=first.verse,
                end_chapter=ch,
                end_verse=last.verse,
            )
            body = " ".join((f"[{v.ref.verse}] " if v.ref.verse else "") + v.text for v in window)
            yield Chunk(
                id=f"{tr}:{passage.osis}",
                source_type=SourceType.SCRIPTURE,
                text=body,
                language=tr.language,
                refs=[passage],
                translation=tr,
                metadata={
                    "numbering": str(tr),
                    "book_number": BOOKS_BY_OSIS[book].order,
                    "testament": str(BOOKS_BY_OSIS[book].testament),
                },  # fmt: skip
            )


def _split(text: str, max_chars: int) -> list[str]:
    """Pieces of at most ~max_chars, cut between sentences (a longer sentence stays whole)."""
    if len(text) <= max_chars:
        return [text]
    units: list[tuple[str, bool]] = []  # (sentence, ends a paragraph)
    for para in text.split("\n\n"):
        sentences = [s for s in _SENTENCE_END.split(para.strip()) if s]
        units += [(s, i == len(sentences) - 1) for i, s in enumerate(sentences)]
    parts: list[str] = []
    current = ""
    for sentence, para_end in units:
        if current and len(current) + 1 + len(sentence) > max_chars:
            parts.append(current.strip())
            current = ""
        current += sentence + ("\n\n" if para_end else " ")
    if current.strip():
        parts.append(current.strip())
    return parts


def _passages(osis_list) -> list[PassageRef]:
    out = []
    for osis in osis_list:
        try:
            out.append(PassageRef.from_osis(osis))
        except ValueError:
            continue
    return out


def commentary_chunks(excerpts: list[CommentaryExcerpt], max_chars: int = 1200) -> Iterator[Chunk]:
    for ex in excerpts:
        refs = _passages(ex.refs)
        parts = _split(ex.text, max_chars)
        for n, part in enumerate(parts, 1):
            yield Chunk(
                id=ex.id if len(parts) == 1 else f"{ex.id}#{n}",
                source_type=SourceType.COMMENTARY,
                text=part,
                language=Language.EN,
                refs=refs,
                work=ex.work,
                metadata={
                    "numbering": str(Translation.KJV),
                    "source": ex.source,
                    "excerpt_id": ex.id,
                    "topic": list(ex.topic),
                    "mentions": list(ex.mentions),
                    "part": n,
                    "parts": len(parts),
                },
            )


def _sections(body: str) -> list[tuple[str, str]]:
    body = _FOOTNOTE_DEF.sub("", body)
    body = _FOOTNOTE_REF.sub("", body)
    body = _MD_LINK.sub(r"\1", body)
    sections: list[tuple[str, str]] = []
    heading, lines = "", []
    for line in body.splitlines():
        m = re.match(r"^#{1,3}\s+(.*)$", line)
        if m:
            if "\n".join(lines).strip():
                sections.append((heading, "\n".join(lines).strip()))
            heading, lines = m.group(1).strip(), []
        else:
            lines.append(line)
    if "\n".join(lines).strip():
        sections.append((heading, "\n".join(lines).strip()))
    return sections


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "body"


def knowledge_chunks(bundle: Bundle) -> Iterator[Chunk]:
    for c in sorted(bundle.concepts.values(), key=lambda c: c.id):
        if c.meta.type == "Reference":
            continue
        yield from _concept_chunks(c)


def _concept_chunks(c: Concept) -> Iterator[Chunk]:
    meta, extra = c.meta, c.meta.extra
    refs = []
    for p in extra.get("key_passages") or []:
        try:
            refs.append(parse_reference(str(p)))
        except ValueError:
            continue
    common = {
        "numbering": str(Translation.KJV),
        "concept_type": meta.type,
        "title": c.title,
        "title_fr": extra.get("title_fr") or "",
        "aliases": list(extra.get("aliases") or []),
        "aliases_fr": list(extra.get("aliases_fr") or []),
        "tags": list(meta.tags),
        "status": meta.status,
        "trust_tier": meta.trust_tier.value,
        "description": meta.description or "",
    }
    seen: set[str] = set()
    for heading, text in _sections(c.body):
        slug = _slug(heading)
        while slug in seen:
            slug += "-x"
        seen.add(slug)
        yield Chunk(
            id=f"{c.id}#{slug}",
            source_type=SourceType.KNOWLEDGE,
            text=f"{c.title} — {heading}\n\n{text}" if heading else f"{c.title}\n\n{text}",
            language=Language.EN,
            refs=refs,
            concept_id=c.id,
            metadata={
                **common,
                "section": heading,
                "content": SECTION_CONTENT.get(heading, "summary"),
            },  # fmt: skip
        )
