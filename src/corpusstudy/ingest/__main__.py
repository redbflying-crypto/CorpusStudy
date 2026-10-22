"""CLI: python -m corpusstudy.ingest [--translation KJV|LSG1910] [--update-lock]"""

from __future__ import annotations

import argparse
import logging
import sys

from corpusstudy.config import Translation
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
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    translations = (
        [Translation(t) for t in args.translation] if args.translation else list(Translation)
    )
    reports = run(translations, update_lock=args.update_lock)
    ok = True
    for tr, r in reports.items():
        status = "OK" if not r.errors else "FAILED"
        print(f"{tr}: {status}  verses={r.verses}  superscriptions={r.superscriptions}")
        for repair in r.repairs:
            print(f"  repaired {repair}")
        for err in r.errors:
            print(f"  error: {err}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
