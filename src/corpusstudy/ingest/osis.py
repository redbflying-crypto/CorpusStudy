"""OSIS XML parser (used for the KJV).

Handles both milestone verses (`<verse sID/>` ... `<verse eID/>`) and container verses
(`<verse osisID>text</verse>`). Notes are dropped, supplied words (`<transChange>`, the KJV
italics) are kept as plain text, and psalm superscriptions become verse 0 of their chapter.
Books outside the 66-book canon (the Apocrypha) are skipped.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

from corpusstudy.canon import BOOKS_BY_OSIS
from corpusstudy.ingest.raw import RawVerse
from corpusstudy.ingest.text import clean_text

_SKIP = {"note", "title", "header", "reference"}  # never part of verse text (titles handled apart)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_osis_id(osis_id: str) -> tuple[str, int, int] | None:
    # "Gen.1.1", sometimes several space-separated ids for a merged verse: keep the first.
    parts = osis_id.split()[0].split(".")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    return parts[0], int(parts[1]), int(parts[2])


class _Walker:
    def __init__(self) -> None:
        self.current: tuple[str, int, int] | None = None
        self.buffers: dict[tuple[str, int, int], list[str]] = {}
        self.order: list[tuple[str, int, int]] = []
        self.chapter: tuple[str, int] | None = None

    def _emit(self, text: str | None) -> None:
        if text and self.current is not None:
            self.buffers[self.current].append(text)

    def _open(self, key: tuple[str, int, int]) -> None:
        if key not in self.buffers:
            self.buffers[key] = []
            self.order.append(key)
        self.current = key

    def walk(self, elem: ET.Element) -> None:
        tag = _local(elem.tag)

        if tag == "chapter":
            ref = elem.get("osisRef") or elem.get("osisID") or elem.get("sID", "")
            parts = ref.split(".")
            if len(parts) >= 2 and parts[1].isdigit():
                self.chapter = (parts[0], int(parts[1]))
            if elem.get("eID") is not None:
                self.current = None

        if tag == "verse":
            if elem.get("eID") is not None:
                self.current = None
            else:
                key = _parse_osis_id(elem.get("osisID", ""))
                if key is not None:
                    self._open(key)
                    if elem.get("sID") is None:  # container verse
                        self._emit(elem.text)
                        for child in elem:
                            self.walk(child)
                        self.current = None
                        self._emit(elem.tail)
                        return
            self._emit(elem.tail)
            return

        if tag == "title" and elem.get("type") == "psalm" and self.chapter is not None:
            book, ch = self.chapter
            # Ps 119's acrostic letters (ALEPH, BETH...) are headings, not superscriptions.
            if book == "Ps" and ch != 119 and self.current is None:
                saved = self.current
                self._open((book, ch, 0))
                self._emit(elem.text)
                for child in elem:
                    self.walk(child)
                self.current = saved
            self._emit(elem.tail)
            return

        if tag in _SKIP:
            self._emit(elem.tail)
            return

        self._emit(elem.text)
        for child in elem:
            self.walk(child)
        self._emit(elem.tail)


def parse_osis(path: str | Path) -> Iterator[RawVerse]:
    tree = ET.parse(path)
    walker = _Walker()
    walker.walk(tree.getroot())
    for key in walker.order:
        book, chapter, verse = key
        if book not in BOOKS_BY_OSIS:
            continue
        text = clean_text("".join(walker.buffers[key]))
        if text:
            yield RawVerse(book, chapter, verse, text)
