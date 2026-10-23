"""Read access to the processed Bible texts written by `python -m corpusstudy.ingest`."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from corpusstudy.config import Translation, get_settings
from corpusstudy.schemas import PassageRef, Verse, VerseRef


class CorpusNotBuilt(FileNotFoundError):
    def __init__(self, path: Path):
        super().__init__(f"{path} not found; run `python -m corpusstudy.ingest` first")


class BibleText:
    """All verses of one translation, in its own numbering, keyed by (book, chapter, verse)."""

    def __init__(self, translation: Translation, verses: list[Verse]):
        self.translation = translation
        self._verses = verses
        self._index = {(v.ref.book, v.ref.chapter, v.ref.verse): v for v in verses}
        self._chapters: dict[tuple[str, int], list[Verse]] = defaultdict(list)
        for v in verses:
            self._chapters[(v.ref.book, v.ref.chapter)].append(v)
        for chapter in self._chapters.values():
            chapter.sort(key=lambda v: v.ref.verse)

    @classmethod
    def load(cls, translation: Translation, data_dir: Path | None = None) -> BibleText:
        path = (data_dir or get_settings().data_dir) / "processed" / f"{translation}.jsonl"
        if not path.exists():
            raise CorpusNotBuilt(path)
        verses = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                ref = VerseRef.model_construct(
                    book=row["book"], chapter=row["chapter"], verse=row["verse"]
                )  # rows were validated at ingestion; skip re-validation for speed
                verses.append(
                    Verse.model_construct(ref=ref, translation=translation, text=row["text"])
                )
        return cls(translation, verses)

    def __len__(self) -> int:
        return len(self._verses)

    def __iter__(self) -> Iterator[Verse]:
        return iter(self._verses)

    def get(self, ref: VerseRef) -> Verse | None:
        return self._index.get((ref.book, ref.chapter, ref.verse))

    def chapter(self, book: str, chapter: int, *, titles: bool = True) -> list[Verse]:
        verses = self._chapters.get((book, chapter), [])
        return list(verses) if titles else [v for v in verses if v.ref.verse > 0]

    def passage(self, passage: PassageRef) -> list[Verse]:
        """The verses of a passage, in order. Whole-chapter passages include psalm titles."""
        out: list[Verse] = []
        for ch in range(passage.start_chapter, passage.end_chapter + 1):
            for verse in self.chapter(passage.book, ch):
                n = verse.ref.verse
                if passage.start_verse is not None and passage.end_verse is not None:
                    if (ch, n) < (passage.start_chapter, passage.start_verse):
                        continue
                    if (ch, n) > (passage.end_chapter, passage.end_verse):
                        break
                out.append(verse)
        return out


@lru_cache(maxsize=4)
def bible(translation: Translation) -> BibleText:
    return BibleText.load(translation)
