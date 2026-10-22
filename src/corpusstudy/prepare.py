"""Get the corpus and the search index ready: on the container's first start, and after
anything they are built from changes.

1. Commentary files dropped in the imports folder (CORPUS_IMPORTS_DIR, default `imports/`)
   are imported when new or changed: `calcom.doc` (Calvin, LCC XXIII) and `mhcc.txt`
   (Matthew Henry, Concise Commentary).
2. The corpus is built when a Bible or an imported commentary has no processed output, or
   a commentary was just imported. The Bibles are downloaded from their pinned sources and
   checked against the committed checksums. Committed tables and the knowledge bundle are
   not rewritten here (`write_resources=False`): that stays a developer step.
3. The search index is rebuilt when it is missing or older than its inputs (the corpus or
   the knowledge bundle, for example after a concept is reviewed).

    python -m corpusstudy.prepare        (the API runs it first with --prepare)
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from corpusstudy.config import Translation, get_settings
from corpusstudy.indexing.build import build_index, index_status
from corpusstudy.indexing.store import index_path
from corpusstudy.ingest.commentary import COMMENTARY_SOURCES, import_file, raw_path
from corpusstudy.ingest.pipeline import run
from corpusstudy.ingest.sources import load_lock, sha256


class PrepareError(RuntimeError):
    pass


def _import_commentaries(imports_dir: Path, raw_dir: Path, log: Callable[[str], None]) -> bool:
    imported = False
    lock = load_lock()
    for source in COMMENTARY_SOURCES.values():
        candidate = imports_dir / source.filename
        if not candidate.is_file():
            continue
        digest = sha256(candidate)
        target = raw_path(source, raw_dir)
        key = f"{source.id}/{source.filename}"
        if target.exists() and sha256(target) == digest and lock.get(key) == digest:
            continue
        import_file(source.id, candidate, raw_dir)
        log(f"imported {candidate.name} ({source.title})")
        imported = True
    return imported


def _corpus_missing(data_dir: Path) -> list[str]:
    raw, out = data_dir / "raw", data_dir / "processed"
    missing = [str(tr) for tr in Translation if not (out / f"{tr}.jsonl").exists()]
    missing += [s.id for s in COMMENTARY_SOURCES.values()
                if raw_path(s, raw).exists()
                and not (out / "commentary" / f"{s.id}.jsonl").exists()]  # fmt: skip
    return missing


def prepare(imports_dir: Path | None = None, log: Callable[[str], None] = print) -> list[str]:
    """Bring the corpus and index up to date; returns what was done."""
    settings = get_settings()
    data_dir = settings.data_dir
    imports_dir = imports_dir or settings.imports_dir
    done: list[str] = []

    if imports_dir.is_dir() and _import_commentaries(imports_dir, data_dir / "raw", log):
        done.append("imported commentaries")

    missing = _corpus_missing(data_dir)
    if done or missing:
        log(f"building the corpus ({', '.join(missing) or 'commentaries changed'}); the first "
            "start downloads the Bibles and takes a few minutes")  # fmt: skip
        try:
            reports, commentary_reports = run(write_resources=False)
        except OSError as exc:  # network errors are OSErrors (URLError)
            raise PrepareError(f"could not build the corpus: {exc}") from exc
        errors = [f"{tr}: {e}" for tr, r in reports.items() for e in r.errors]
        errors += [f"{c.source}: {c.message or c.status}" for c in commentary_reports.values()
                   if c.status not in ("ok", "skipped")]  # fmt: skip
        if errors:
            raise PrepareError("the corpus did not build: " + "; ".join(errors))
        done.append("built the corpus")
        for c in commentary_reports.values():
            log(f"{c.source}: {c.status}" + (f" ({c.excerpts} excerpts)" if c.excerpts else ""))

    status = index_status(index_path())
    if status != "fresh":
        log(f"building the search index (it was {status})")
        info = build_index()
        log(f"index built: {info['counts']}")
        done.append("built the index")
    if not done:
        log("corpus and index are up to date")
    return done


def main() -> int:
    try:
        prepare()
    except PrepareError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
