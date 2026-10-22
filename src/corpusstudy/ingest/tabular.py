"""CSV / JSON / JSONL verse files: one row per verse with book, chapter, verse and text.

`book` may be any name, abbreviation, OSIS or USFM code that `canon.find_book` recognises,
in English or French. Useful for texts supplied by hand.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from corpusstudy.canon import find_book
from corpusstudy.ingest.raw import RawVerse
from corpusstudy.ingest.text import clean_text

_ALIASES = {
    "book": ("book", "book_name", "livre", "b"),
    "chapter": ("chapter", "chapitre", "c"),
    "verse": ("verse", "verset", "v"),
    "text": ("text", "texte", "t"),
}


def _field(row: Mapping[str, Any], name: str) -> Any:
    lowered = {k.lower().strip(): v for k, v in row.items() if k is not None}
    for alias in _ALIASES[name]:
        if alias in lowered:
            return lowered[alias]
    raise KeyError(f"row has no {name!r} column: {sorted(lowered)}")


def parse_rows(rows: Iterable[Mapping[str, Any]]) -> Iterator[RawVerse]:
    for i, row in enumerate(rows, 1):
        book = find_book(str(_field(row, "book")))
        if book is None:
            raise ValueError(f"row {i}: unknown book {_field(row, 'book')!r}")
        text = clean_text(str(_field(row, "text")))
        if text:
            yield RawVerse(book.osis, int(_field(row, "chapter")), int(_field(row, "verse")), text)


def parse_tabular(path: str | Path) -> Iterator[RawVerse]:
    path = Path(path)
    suffix = path.suffix.lower()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        if suffix in (".csv", ".tsv"):
            yield from parse_rows(csv.DictReader(fh, delimiter="\t" if suffix == ".tsv" else ","))
        elif suffix == ".jsonl":
            yield from parse_rows(json.loads(line) for line in fh if line.strip())
        elif suffix == ".json":
            data = json.load(fh)
            rows = data.get("verses", data) if isinstance(data, dict) else data
            yield from parse_rows(rows)
        else:
            raise ValueError(f"unsupported verse file type: {path.name}")
