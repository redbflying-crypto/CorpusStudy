"""Building the search index from the processed corpus and the knowledge bundle."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from corpusstudy.commentary import CommentaryStore
from corpusstudy.config import Translation, get_settings
from corpusstudy.corpus import BibleText
from corpusstudy.indexing.chunking import bible_chunks, commentary_chunks, knowledge_chunks
from corpusstudy.indexing.store import (
    SCHEMA_VERSION,
    fingerprint,
    index_path,
    read_info,
    write_index,
)
from corpusstudy.ingest.commentary import COMMENTARY_SOURCES
from corpusstudy.okf.bundle import load_bundle
from corpusstudy.schemas import Chunk


def input_files() -> list[Path]:
    settings = get_settings()
    processed = settings.data_dir / "processed"
    files = [processed / f"{tr}.jsonl" for tr in Translation]
    files += [CommentaryStore.path(s) for s in COMMENTARY_SOURCES]
    files += sorted(settings.knowledge_dir.rglob("*.md"))
    return [f for f in files if f.exists()]


def all_chunks() -> Iterator[Chunk]:
    for tr in Translation:
        yield from bible_chunks(BibleText.load(tr))
    for source in COMMENTARY_SOURCES:
        if CommentaryStore.path(source).exists():
            yield from commentary_chunks(list(CommentaryStore.load(source)))
    yield from knowledge_chunks(load_bundle(get_settings().knowledge_dir))


def build_index(path: Path | None = None) -> dict:
    path = path or index_path()
    files = input_files()
    info = {
        "inputs": fingerprint(files),
        "input_files": [f.name for f in files if f.suffix == ".jsonl"],
    }
    return write_index(path, all_chunks(), info)


def index_status(path: Path | None = None) -> str:
    """'missing', 'stale' (inputs or the index format changed since the build) or 'fresh'.
    Bump `store.SCHEMA_VERSION` whenever chunking or the index tables change, so existing
    indexes (a Docker volume, for example) are rebuilt on the next start."""
    info = read_info(path or index_path())
    if info is None:
        return "missing"
    if info.meta.get("schema") != SCHEMA_VERSION:
        return "stale"
    return "fresh" if info.meta.get("inputs") == fingerprint(input_files()) else "stale"
