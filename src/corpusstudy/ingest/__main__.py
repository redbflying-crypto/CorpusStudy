"""CLI: python -m corpusstudy.ingest [--translation KJV|LSG1910] [--update-lock]
[--import SOURCE FILE]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from corpusstudy.config import Translation, get_settings
from corpusstudy.ingest.commentary import COMMENTARY_SOURCES, import_file
from corpusstudy.ingest.pipeline import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m corpusstudy.ingest", description=__doc__)
    parser.add_argument(
        "--translation",
        "-t",
        action="append",
        choices=[t.value for t in Translation],
        help="translation(s) to ingest (default: all)",
    )
    parser.add_argument(
        "--update-lock",
        action="store_true",
        help="accept new source checksums instead of failing on a mismatch",
    )
    parser.add_argument(
        "--import",
        dest="import_",
        nargs=2,
        metavar=("SOURCE", "FILE"),
        action="append",
        help=f"import a hand-downloaded commentary file ({', '.join(COMMENTARY_SOURCES)})",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    translations = (
        [Translation(t) for t in args.translation] if args.translation else list(Translation)
    )
    for source_id, path in args.import_ or []:
        if source_id not in COMMENTARY_SOURCES:
            parser.error(f"unknown commentary source {source_id!r}")
        target = import_file(source_id, Path(path), get_settings().data_dir / "raw")
        print(f"imported {path} -> {target}")

    reports, commentary_reports = run(translations, update_lock=args.update_lock)
    ok = True
    for tr, r in reports.items():
        status = "OK" if not r.errors else "FAILED"
        print(f"{tr}: {status}  verses={r.verses}  superscriptions={r.superscriptions}")
        for repair in r.repairs:
            print(f"  repaired {repair}")
        for err in r.errors:
            print(f"  error: {err}")
            ok = False
    for cr in commentary_reports.values():
        if cr.status == "skipped":
            print(f"{cr.source}: skipped, {cr.message}")
            continue
        print(f"{cr.source}: {cr.status.upper()}  excerpts={cr.excerpts}  with_refs={cr.with_refs}")
        for note in cr.corrected_refs:
            print(f"  corrected {note}")
        if cr.dropped_refs:
            print(f"  dropped {len(cr.dropped_refs)} invalid references: {cr.dropped_refs[:5]}")
        ok = ok and cr.status == "ok"
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
