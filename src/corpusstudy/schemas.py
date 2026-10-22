"""Shared data models for every stage of the pipeline.

The models enforce the project's ground rules up front:
- references must point at a real book and chapter;
- Scripture text always carries a public-domain translation, commentary never does,
  so commentary can never be passed off as Scripture;
- knowledge chunks name the OKF concept they come from (see `corpusstudy.okf`).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from corpusstudy.canon import BOOKS_BY_OSIS, Book, find_book
from corpusstudy.config import ALLOWED_COMMENTARIES, Language, Stance, Translation


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- references


def _book_for(osis: str) -> Book:
    book = BOOKS_BY_OSIS.get(osis)
    if book is None:
        raise ValueError(f"unknown OSIS book code {osis!r}")
    return book


class VerseRef(_Model):
    """A single verse, e.g. Rom 8:28.

    Verse 0 is an unnumbered psalm title (KJV style); in LSG 1910 titles are numbered verses.
    """

    book: str = Field(description="OSIS book code, e.g. 'Rom'")
    chapter: int = Field(ge=1)
    verse: int = Field(ge=0)

    @model_validator(mode="after")
    def _in_canon(self) -> Self:
        book = _book_for(self.book)
        if self.chapter > book.chapters:
            raise ValueError(f"{book.name_en} has {book.chapters} chapters, not {self.chapter}")
        if self.verse == 0 and self.book != "Ps":
            raise ValueError("verse 0 (a psalm title) exists only in Psalms")
        return self

    @classmethod
    def from_osis(cls, osis: str) -> VerseRef:
        book, chapter, verse = osis.split(".")
        return cls(book=book, chapter=int(chapter), verse=int(verse))

    @property
    def key(self) -> tuple[int, int, int]:
        return (BOOKS_BY_OSIS[self.book].order, self.chapter, self.verse)

    @property
    def osis(self) -> str:
        return f"{self.book}.{self.chapter}.{self.verse}"

    def display(self, language: Language = Language.EN) -> str:
        name = BOOKS_BY_OSIS[self.book].name(language)
        if self.verse == 0:
            return f"{name} {self.chapter} ({'title' if language == Language.EN else 'titre'})"
        sep = ":" if language == Language.EN else "."
        return f"{name} {self.chapter}{sep}{self.verse}"


class PassageRef(_Model):
    """A contiguous passage inside one book.

    Verse passages (Rom 8:28-30, Rom 8:28-9:2) set start_verse and end_verse.
    Whole-chapter passages (Ps 23, Gen 1-3) leave both verses as None.
    """

    book: str
    start_chapter: int = Field(ge=1)
    start_verse: int | None = Field(default=None, ge=0)
    end_chapter: int = Field(ge=1)
    end_verse: int | None = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            if data.get("end_chapter") is None:
                data["end_chapter"] = data.get("start_chapter")
            if data.get("start_verse") is not None and data.get("end_verse") is None:
                if data["end_chapter"] != data.get("start_chapter"):
                    raise ValueError("a passage starting on a verse must end on a verse")
                data["end_verse"] = data["start_verse"]
        return data

    @model_validator(mode="after")
    def _check(self) -> Self:
        book = _book_for(self.book)
        for ch in (self.start_chapter, self.end_chapter):
            if ch > book.chapters:
                raise ValueError(f"{book.name_en} has {book.chapters} chapters, not {ch}")
        if self.start_verse is None and self.end_verse is not None:
            raise ValueError("a passage cannot end on a verse if it starts on a whole chapter")
        if 0 in (self.start_verse, self.end_verse) and self.book != "Ps":
            raise ValueError("verse 0 (a psalm title) exists only in Psalms")
        if (self.end_chapter, self.end_verse or 0) < (self.start_chapter, self.start_verse or 0):
            raise ValueError("passage ends before it starts")
        return self

    @property
    def is_whole_chapter(self) -> bool:
        return self.start_verse is None

    @property
    def is_single_verse(self) -> bool:
        return (
            self.start_verse is not None
            and self.start_chapter == self.end_chapter
            and self.start_verse == self.end_verse
        )

    def contains(self, ref: VerseRef) -> bool:
        if ref.book != self.book:
            return False
        if self.is_whole_chapter:
            return self.start_chapter <= ref.chapter <= self.end_chapter
        start = (self.start_chapter, self.start_verse)
        end = (self.end_chapter, self.end_verse)
        return start <= (ref.chapter, ref.verse) <= end

    @property
    def osis(self) -> str:
        def point(ch: int, v: int | None) -> str:
            return f"{self.book}.{ch}" if v is None else f"{self.book}.{ch}.{v}"

        start = point(self.start_chapter, self.start_verse)
        end = point(self.end_chapter, self.end_verse)
        return start if start == end else f"{start}-{end}"

    def display(self, language: Language = Language.EN) -> str:
        book = BOOKS_BY_OSIS[self.book]
        name = book.name(language)
        sep = ":" if language == Language.EN else "."
        if self.is_whole_chapter:
            if book.chapters == 1:
                return name
            if self.end_chapter == self.start_chapter:
                return f"{name} {self.start_chapter}"
            return f"{name} {self.start_chapter}-{self.end_chapter}"
        if book.chapters == 1:
            head = f"{name} {self.start_verse}"
            return head if self.is_single_verse else f"{head}-{self.end_verse}"
        head = f"{name} {self.start_chapter}{sep}{self.start_verse}"
        if self.is_single_verse:
            return head
        if self.end_chapter == self.start_chapter:
            return f"{head}-{self.end_verse}"
        return f"{head}-{self.end_chapter}{sep}{self.end_verse}"

    @classmethod
    def parse(cls, text: str) -> PassageRef:
        return parse_reference(text)

    @classmethod
    def from_osis(cls, osis: str) -> PassageRef:
        """'Gen.1.1', 'Gen.1.1-Gen.1.3', 'Gen.1', 'Gen.1-Gen.2' -> PassageRef (one book only)."""
        start, _, end = osis.partition("-")
        s = start.split(".")
        e = (end or start).split(".")
        if s[0] != e[0] or not 2 <= len(s) <= 3 or len(s) != len(e):
            raise ValueError(f"unsupported OSIS passage {osis!r}")
        return cls(
            book=s[0],
            start_chapter=int(s[1]),
            start_verse=int(s[2]) if len(s) == 3 else None,
            end_chapter=int(e[1]),
            end_verse=int(e[2]) if len(e) == 3 else None,
        )


# "1 Cor 13:4-7", "Jean 3.16", "Rom 8:28-9:2", "Ps 23", "Gen 1-3", "Jude 3", "Jn 3,16"
_REF_RE = re.compile(
    r"""^\s*
    (?P<book>(?:[1-3]|I{1,3})?\s*\.?\s*[^\d\s][^\d]*?)\s*
    (?P<c1>\d+)
    (?:\s*[:.,]\s*(?P<v1>\d+))?
    (?:\s*[-–—]\s*(?P<c2>\d+)(?:\s*[:.,]\s*(?P<v2>\d+))?)?
    \s*$""",
    re.VERBOSE,
)


def parse_reference(text: str) -> PassageRef:
    """Parse one English or French Bible reference into a PassageRef.

    Raises ValueError for unknown books or out-of-range chapters.
    """
    m = _REF_RE.match(text)
    if not m:
        raise ValueError(f"not a Bible reference: {text!r}")
    book = find_book(m["book"])
    if book is None:
        raise ValueError(f"unknown book in reference: {text!r}")

    c1, v1 = int(m["c1"]), m["v1"] and int(m["v1"])
    c2, v2 = m["c2"] and int(m["c2"]), m["v2"] and int(m["v2"])

    # Single-chapter books are cited by verse only: "Jude 3", "Jude 3-5".
    if book.chapters == 1 and v1 is None:
        return PassageRef(book=book.osis, start_chapter=1, start_verse=c1, end_verse=c2 or c1)

    if v1 is None:  # chapter or chapter range
        return PassageRef(book=book.osis, start_chapter=c1, end_chapter=c2)
    if c2 is None:  # single verse
        return PassageRef(book=book.osis, start_chapter=c1, start_verse=v1, end_verse=v1)
    if v2 is None:  # "8:28-30": the second number is a verse in the same chapter
        return PassageRef(book=book.osis, start_chapter=c1, start_verse=v1, end_verse=c2)
    return PassageRef(
        book=book.osis, start_chapter=c1, start_verse=v1, end_chapter=c2, end_verse=v2
    )


def parse_references(text: str) -> list[PassageRef]:
    """Parse a ';'-separated list of references, e.g. 'Rom 8:28; Eph 1:4-5'."""
    return [parse_reference(part) for part in text.split(";") if part.strip()]


# --------------------------------------------------------------------------- corpus


class SourceType(StrEnum):
    SCRIPTURE = "scripture"
    COMMENTARY = "commentary"
    CROSS_REFERENCE = "cross_reference"
    KNOWLEDGE = "knowledge"


class Verse(_Model):
    """One verse of one translation, the unit produced by Phase 2 ingestion."""

    ref: VerseRef
    translation: Translation
    text: str = Field(min_length=1)

    @property
    def id(self) -> str:
        return f"{self.translation}:{self.ref.osis}"


class Chunk(_Model):
    """A retrievable unit of text: a verse window, a commentary section, or a knowledge section."""

    id: str = Field(min_length=1)
    source_type: SourceType
    text: str = Field(min_length=1)
    language: Language
    refs: list[PassageRef] = Field(default_factory=list)
    translation: Translation | None = None
    work: str | None = Field(default=None, description="Commentary key, e.g. 'calvin'")
    concept_id: str | None = Field(
        default=None, description="OKF concept id (bundle path without .md), e.g. doctrines/grace"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _label_rules(self) -> Self:
        st = self.source_type
        if st == SourceType.SCRIPTURE:
            if self.translation is None:
                raise ValueError("Scripture chunks must name their translation")
            if self.work is not None:
                raise ValueError("Scripture chunks cannot carry a commentary work")
            if not self.refs:
                raise ValueError("Scripture chunks must carry at least one reference")
            if self.translation.language != self.language:
                raise ValueError(f"{self.translation} text is not in {self.language}")
        else:
            if self.translation is not None:
                raise ValueError(f"{st} chunks cannot be labelled with a Bible translation")
        if st in (SourceType.COMMENTARY, SourceType.CROSS_REFERENCE):
            if self.work not in ALLOWED_COMMENTARIES:
                raise ValueError(f"{st} chunks need an approved public-domain work: {self.work!r}")
        if st == SourceType.KNOWLEDGE and not self.concept_id:
            raise ValueError("knowledge chunks must name their OKF concept")
        return self


# --------------------------------------------------------------------------- retrieval / answers


class Retriever(StrEnum):
    KEYWORD = "keyword"
    DENSE = "dense"
    FUSED = "fused"
    KNOWLEDGE = "knowledge"
    LOOKUP = "lookup"
    REFERENCE = "reference"  # chunks about a passage cited in the question
    GRAPH = "graph"  # neighbours of a matching knowledge concept


class RetrievalResult(_Model):
    chunk: Chunk
    score: float
    retriever: Retriever
    rank: int = Field(ge=1)
    via: list[str] = Field(default_factory=list, description="result lists that found it")


class Route(StrEnum):
    """Which source(s) the router sends a question to (Phase 5)."""

    LOOKUP = "lookup"  # "What does John 3:16 say?"
    KNOWLEDGE = "knowledge"  # curated files: books, people, doctrines...
    RAG = "rag"  # open question over the corpus
    HYBRID = "hybrid"  # knowledge + RAG
    PASTORAL = "pastoral"  # crisis / abuse / self-harm: safety response first


class Citation(_Model):
    ref: PassageRef
    translation: Translation
    quote: str | None = None
    verified: bool = False


class SourceNote(_Model):
    """A non-Scripture source used in an answer, always labelled as such."""

    source_type: SourceType
    label: str
    chunk_id: str | None = None

    @field_validator("source_type")
    @classmethod
    def _not_scripture(cls, v: SourceType) -> SourceType:
        if v == SourceType.SCRIPTURE:
            raise ValueError("Scripture is cited through Citation, not SourceNote")
        return v


class Answer(_Model):
    text: str
    language: Language
    route: Route
    citations: list[Citation] = Field(default_factory=list)
    sources: list[SourceNote] = Field(default_factory=list)
    stance: Stance = Stance.REFORMED
    interpretive: bool = Field(
        default=False, description="True if the answer goes beyond what the text states"
    )

    @model_validator(mode="after")
    def _citations_in_language(self) -> Self:
        for c in self.citations:
            if c.translation.language != self.language:
                raise ValueError(
                    f"{self.language} answer cannot quote {c.translation} "
                    f"({c.translation.language})"
                )
        return self

    @property
    def all_verified(self) -> bool:
        return all(c.verified for c in self.citations)


# --------------------------------------------------------------------------- chat API


class ChatTurn(_Model):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(_Model):
    message: str = Field(min_length=1, max_length=4000)
    language: Language | None = Field(default=None, description="None = detect from the message")
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


class ChatResponse(_Model):
    answer: Answer
    warnings: list[str] = Field(default_factory=list)
