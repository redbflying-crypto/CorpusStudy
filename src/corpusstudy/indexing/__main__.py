"""CLI: python -m corpusstudy.indexing [build | status]

build   (default) rebuild the search index from the corpus and the knowledge bundle
status  missing | stale | fresh, with the counts of the current index
"""

from __future__ import annotations

import sys

from corpusstudy.indexing.build import build_index, index_status
from corpusstudy.indexing.store import index_path, read_info


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "build"
    if command == "status":
        info = read_info(index_path())
        print(f"{index_path()}: {index_status()}" + (f", {info.counts}" if info else ""))
        return 0
    if command != "build":
        print(__doc__, file=sys.stderr)
        return 2
    info = build_index()
    print(f"built {index_path()} in {info['seconds']}s: {info['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
