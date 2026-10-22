"""Read access to the processed commentaries written by `python -m corpusstudy.ingest`.

Excerpt ids are `<source>:<local id>`, e.g. `henry-concise:Rom.3.21-Rom.3.31` or
`calvin-lcc:vi-p90`. They are stable for a given imported file (its checksum is locked),
so knowledge concepts cite them as `/references/sources/<source>.md#<local id>`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from corpusstudy.config import get_settings


@dataclass(frozen=True)
class CommentaryExcerpt:
    id: str
    work: str
    source: str
    topic: tuple[str, ...]
    refs: tuple[str, ...]
    mentions: tuple[str, ...]
    text: str

    @property
    def local_id(self) -> str:
        return self.id.split(":", 1)[1]


class CommentaryStore:
    def __init__(self, source: str, excerpts: list[CommentaryExcerpt]):
        self.source = source
        self._by_id = {e.id: e for e in excerpts}

    @classmethod
    def path(cls, source: str, data_dir: Path | None = None) -> Path:
        base = data_dir or get_settings().data_dir
        return base / "processed" / "commentary" / f"{source}.jsonl"

    @classmethod
    def load(cls, source: str, data_dir: Path | None = None) -> CommentaryStore:
        path = cls.path(source, data_dir)
        excerpts = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                excerpts.append(
                    CommentaryExcerpt(
                        id=r["id"],
                        work=r["work"],
                        source=r["source"],
                        topic=tuple(r["topic"]),
                        refs=tuple(r["refs"]),
                        mentions=tuple(r["mentions"]),
                        text=r["text"],
                    )
                )
        return cls(source, excerpts)

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, local_id: str) -> CommentaryExcerpt | None:
        return self._by_id.get(f"{self.source}:{local_id}")


@lru_cache(maxsize=8)
def commentary(source: str) -> CommentaryStore | None:
    """The store for `source`, or None when it has not been built."""
    path = CommentaryStore.path(source)
    return CommentaryStore.load(source) if path.exists() else None
