"""Pinned public-domain sources and their download.

Every URL points at a fixed git commit, and every file is checked against the SHA-256 recorded
in `resources/sources.lock.json`, so a rebuild always ingests exactly the same text.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from corpusstudy.canon import BOOKS
from corpusstudy.config import Translation

RESOURCES = Path(__file__).resolve().parents[1] / "resources"
LOCK_PATH = RESOURCES / "sources.lock.json"


@dataclass(frozen=True)
class SourceFile:
    name: str  # local file name under data/raw/<source id>/
    url: str


@dataclass(frozen=True)
class Source:
    id: str
    translation: Translation
    format: str  # "osis" | "usfm"
    description: str
    license: str
    homepage: str
    files: tuple[SourceFile, ...] = field(default=())


_KJV_COMMIT = "f257a3559025c3f873b48a75019f53a9354ed7de"
_LSG_COMMIT = "0a17bedd1c7ce26745bd6ca99c56224b5f9d647b"


def _lsg_files() -> tuple[SourceFile, ...]:
    base = f"https://raw.githubusercontent.com/BibleCorps/FRA-B-LSG1910-PD-UBS/{_LSG_COMMIT}/p.sfm/"
    files = []
    for book in BOOKS:
        # Paratext file numbering skips 40: Matthew is 41.
        num = book.order if book.order <= 39 else book.order + 1
        name = f"FRA[B]LSG1910[PD]UBS-{num:02d}-{book.usfm}.p.sfm"
        files.append(SourceFile(name, base + urllib.parse.quote(name)))
    return tuple(files)


SOURCES: dict[Translation, Source] = {
    Translation.KJV: Source(
        id="kjv",
        translation=Translation.KJV,
        format="osis",
        description="King James Version (1769 Oxford text), eBible.org OSIS "
        "via seven1m/open-bibles",
        license="Public domain (Crown copyright applies in the United Kingdom only)",
        homepage="https://github.com/seven1m/open-bibles",
        files=(
            SourceFile(
                "eng-kjv.osis.xml",
                f"https://raw.githubusercontent.com/seven1m/open-bibles/{_KJV_COMMIT}/eng-kjv.osis.xml",
            ),
        ),
    ),
    Translation.LSG1910: Source(
        id="lsg1910",
        translation=Translation.LSG1910,
        format="usfm",
        description="Louis Segond 1910, UBS USFM via BibleCorps/FRA-B-LSG1910-PD-UBS",
        license="Public domain",
        homepage="https://github.com/BibleCorps/FRA-B-LSG1910-PD-UBS",
        files=_lsg_files(),
    ),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def load_lock() -> dict[str, str]:
    if LOCK_PATH.exists():
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return {}


def save_lock(lock: dict[str, str]) -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(json.dumps(dict(sorted(lock.items())), indent=1) + "\n", encoding="utf-8")


class ChecksumError(RuntimeError):
    pass


def fetch(source: Source, raw_dir: Path, *, update_lock: bool = False) -> Path:
    """Download any missing file of `source` into raw_dir/<id>/ and verify its checksum."""
    target = raw_dir / source.id
    target.mkdir(parents=True, exist_ok=True)
    lock = load_lock()
    changed = False
    for f in source.files:
        path = target / f.name
        if not path.exists():
            tmp = path.with_suffix(path.suffix + ".part")
            with urllib.request.urlopen(f.url, timeout=120) as resp, open(tmp, "wb") as out:
                out.write(resp.read())
            tmp.replace(path)
        key = f"{source.id}/{f.name}"
        digest = sha256(path)
        if key in lock and lock[key] != digest and not update_lock:
            raise ChecksumError(f"{key}: expected sha256 {lock[key]}, got {digest}")
        if lock.get(key) != digest:
            lock[key] = digest
            changed = True
    if changed:
        save_lock(lock)
    return target
