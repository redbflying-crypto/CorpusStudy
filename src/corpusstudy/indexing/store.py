"""The search index: one SQLite file with chunks, passage ranges and an FTS5 table.

    chunks  id, kind (bible | commentary | knowledge), language, text, refs, metadata
    refs    one row per (chunk, passage): book, numbering, start/end as chapter*1000+verse
    fts     analysed stems per chunk and language (FTS5, BM25 ranking)
    xrefs   cross-references (KJV numbering): passages Henry or Calvin cite in their comment
            on a passage, and key passages listed together in a knowledge concept; both
            directions, with the excerpt or concept that links them
    meta    what the index was built from, to detect a stale index

Stems are computed by `analysis` (the same code analyses queries), so FTS5 only has to
split on spaces.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from corpusstudy.config import Language, Translation, get_settings
from corpusstudy.indexing.analysis import stems
from corpusstudy.schemas import Chunk, PassageRef, SourceType

INDEX_NAME = "corpus.sqlite"
SCHEMA_VERSION = "2"
KIND = {
    SourceType.SCRIPTURE: "bible",
    SourceType.COMMENTARY: "commentary",
    SourceType.KNOWLEDGE: "knowledge",
}

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE chunks (
    rowid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    language TEXT NOT NULL,
    data TEXT NOT NULL          -- the Chunk as JSON
);
CREATE TABLE refs (
    chunk INTEGER NOT NULL REFERENCES chunks(rowid),
    book TEXT NOT NULL,
    numbering TEXT NOT NULL,
    start INTEGER NOT NULL,
    stop INTEGER NOT NULL
);
CREATE INDEX refs_lookup ON refs (book, numbering, start, stop);
CREATE TABLE xrefs (
    book TEXT NOT NULL,
    start INTEGER NOT NULL,
    stop INTEGER NOT NULL,
    target TEXT NOT NULL,       -- OSIS passage
    via TEXT NOT NULL           -- commentary excerpt id or knowledge concept id
);
CREATE INDEX xrefs_lookup ON xrefs (book, start, stop);
CREATE VIRTUAL TABLE fts USING fts5(
    terms, chunk UNINDEXED, lang UNINDEXED, kind UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 0'
);
"""


def passage_bounds(p: PassageRef) -> tuple[int, int]:
    """(start, stop) as chapter*1000+verse; whole chapters span verse 0..999."""
    start = p.start_chapter * 1000 + (p.start_verse if p.start_verse is not None else 0)
    stop = p.end_chapter * 1000 + (p.end_verse if p.end_verse is not None else 999)
    return start, stop


def index_path() -> Path:
    return get_settings().index_dir / INDEX_NAME


def fts_texts(chunk: Chunk) -> Iterator[tuple[Language, str]]:
    """The searchable text per language. Knowledge chunks are written in English, so their
    French entry is the French title and aliases only."""
    meta = chunk.metadata
    if chunk.source_type == SourceType.KNOWLEDGE:
        en = " ".join([chunk.text, *meta.get("aliases", []), *meta.get("tags", [])])
        yield Language.EN, en
        fr = " ".join([meta.get("title_fr", ""), *meta.get("aliases_fr", [])]).strip()
        if fr:
            yield Language.FR, fr
    else:
        yield chunk.language, chunk.text


def write_index(path: Path, chunks: Iterable[Chunk], build_info: dict[str, Any]) -> dict:
    """Build a fresh index at `path` (atomically replaced). Returns counts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(_SCHEMA)
    counts: dict[str, int] = {}
    started = time.monotonic()
    with con:
        links: set[tuple[str, str, str]] = set()
        for chunk in chunks:
            links |= _links(chunk)
            kind = KIND[chunk.source_type]
            cur = con.execute(
                "INSERT INTO chunks (id, kind, language, data) VALUES (?, ?, ?, ?)",
                (chunk.id, kind, str(chunk.language), chunk.model_dump_json()),
            )
            rowid = cur.lastrowid
            numbering = chunk.metadata.get("numbering", str(Translation.KJV))
            for p in chunk.refs:
                start, stop = passage_bounds(p)
                con.execute(
                    "INSERT INTO refs VALUES (?, ?, ?, ?, ?)",
                    (rowid, p.book, numbering, start, stop),
                )
            for lang, text in fts_texts(chunk):
                con.execute(
                    "INSERT INTO fts (terms, chunk, lang, kind) VALUES (?, ?, ?, ?)",
                    (" ".join(stems(text, lang)), rowid, str(lang), kind),
                )
            counts[kind] = counts.get(kind, 0) + 1
        for a, b, via in sorted(links):
            for src, dst in ((a, b), (b, a)):
                p = PassageRef.from_osis(src)
                start, stop = passage_bounds(p)
                con.execute("INSERT INTO xrefs VALUES (?, ?, ?, ?, ?)",
                            (p.book, start, stop, dst, via))  # fmt: skip
        info = {**build_info, "schema": SCHEMA_VERSION, "counts": counts, "xrefs": len(links),
                "seconds": round(time.monotonic() - started, 1)}  # fmt: skip
        con.executemany(
            "INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in info.items()]
        )
    con.execute("INSERT INTO fts(fts) VALUES ('optimize')")
    con.commit()
    con.close()
    tmp.replace(path)
    return info


# Concepts whose key passages are related by subject (a book's or a person's key passages
# are an outline, not cross-references).
_LINKING_TYPES = {"Theme", "Doctrine", "Event", "Parable"}


def _overlap(a: PassageRef, b: PassageRef) -> bool:
    if a.book != b.book:
        return False
    a0, a1 = passage_bounds(a)
    b0, b1 = passage_bounds(b)
    return a0 <= b1 and b0 <= a1


def _links(chunk: Chunk) -> set[tuple[str, str, str]]:
    """Cross-reference pairs (passage, passage, via) a chunk contributes, KJV numbering."""
    if chunk.source_type == SourceType.COMMENTARY:
        via = chunk.id.split("#", 1)[0]
        targets = []
        for osis in chunk.metadata.get("mentions") or []:
            try:
                targets.append(PassageRef.from_osis(osis))
            except ValueError:
                continue
        return {(r.osis, t.osis, via) for r in chunk.refs for t in targets if not _overlap(r, t)}
    if (
        chunk.source_type == SourceType.KNOWLEDGE
        and chunk.concept_id
        and (chunk.metadata.get("concept_type") in _LINKING_TYPES)
    ):
        refs = chunk.refs
        return {(a.osis, b.osis, chunk.concept_id) for i, a in enumerate(refs)
                for b in refs[i + 1:] if not _overlap(a, b)}  # fmt: skip
    return set()


@dataclass
class IndexInfo:
    path: Path
    meta: dict[str, Any]

    @property
    def counts(self) -> dict[str, int]:
        return self.meta.get("counts", {})


def read_info(path: Path) -> IndexInfo | None:
    if not path.exists():
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
    finally:
        con.close()
    return IndexInfo(path, meta)


def fingerprint(paths: Iterable[Path]) -> str:
    """A digest of the files an index is built from (to detect a stale index)."""
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.as_posix().encode())
        h.update(p.read_bytes() if p.exists() else b"-")
    return h.hexdigest()
