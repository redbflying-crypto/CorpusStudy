"""The parser output format: one record per verse in the source's own numbering."""

from __future__ import annotations

from dataclasses import dataclass


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
