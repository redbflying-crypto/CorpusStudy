"""Parser output formats: verses in the source's own numbering, and commentary excerpts."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RawVerse:
    """A verse as found in a source file, before validation against the canon.

    `verse == 0` is an unnumbered psalm superscription (KJV style).
    """

    book: str  # OSIS code
    chapter: int
    verse: int
    text: str

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.book, self.chapter, self.verse)


@dataclass
class Excerpt:
    """A commentary excerpt: the passage(s) it comments on, plus passages it cites."""

    id: str
    topic: tuple[str, ...]
    refs: list[str] = field(default_factory=list)  # OSIS passages the excerpt comments on
    mentions: list[str] = field(default_factory=list)  # other passages cited in the comment
    paragraphs: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)
