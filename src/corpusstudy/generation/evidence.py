"""The evidence an answer may draw on: retrieved Scripture, commentary and knowledge.

Commentary and knowledge items get keys (E1, E2, ...) that the answer cites as [E1].
Scripture is cited by its reference in the text ("John 3:16", "Jean 3.16"), never by key.
"""

from __future__ import annotations

from dataclasses import dataclass

from corpusstudy.config import Language
from corpusstudy.schemas import Chunk, RetrievalResult, SourceType

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
        return self.chunk.text

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


def pack(results: list[RetrievalResult]) -> list[Evidence]:
    """Evidence in retrieval order, keyed E1, E2, ... for commentary and knowledge."""
    out, n = [], 0
    for r in results:
        if r.chunk.source_type == SourceType.SCRIPTURE:
            out.append(Evidence(None, r.chunk, label(r.chunk)))
        else:
            n += 1
            out.append(Evidence(f"E{n}", r.chunk, label(r.chunk)))
    return out
