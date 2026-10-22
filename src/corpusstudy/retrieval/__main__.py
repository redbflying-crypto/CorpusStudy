"""CLI: python -m corpusstudy.retrieval {search QUERY | eval [--cases FILE]} [-k N] [--lang en|fr]

search   show the fused results for a question, with the lists that found each one
eval     recall@1/5/10 and MRR on eval/retrieval.yaml, per category
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from corpusstudy.config import Language
from corpusstudy.retrieval.evaluation import (
    EVAL_PATH,
    UNGATED,
    evaluate,
    load_cases,
    summarise,
)
from corpusstudy.retrieval.search import Search


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m corpusstudy.retrieval",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=["search", "eval"])
    parser.add_argument("query", nargs="?")
    parser.add_argument("-k", type=int, default=10)
    parser.add_argument("--lang", choices=["en", "fr"])
    parser.add_argument("--drafts", action="store_true", help="include unreviewed concepts")
    parser.add_argument("--cases", type=Path, default=EVAL_PATH, help="evaluation file")
    args = parser.parse_args(argv)
    search = Search(use_drafts=True if args.drafts else None)

    if args.command == "search":
        if not args.query:
            parser.error("search needs a query")
        q = search.analyse(args.query, Language(args.lang) if args.lang else None)
        print(f"language={q.language} refs={[r.osis for r in q.refs]} terms={q.terms}")
        for r in search.search(args.query, q.language, k=args.k):
            print(f"{r.rank:>2}. {r.chunk.id}  [{', '.join(r.via)}]")
            print(f"    {r.chunk.text[:160]!s}")
        return 0

    results = evaluate(search, load_cases(args.cases), k=args.k)
    summary = summarise(results)
    print(f"{'category':<12} {'n':>3} {'R@1':>6} {'R@5':>6} {'R@10':>6} {'MRR':>6}")
    for cat, s in summary["categories"].items():
        flag = "  (not gated)" if cat in UNGATED else ""
        print(f"{cat:<12} {s['n']:>3} {s['recall@1']:>6.2f} {s['recall@5']:>6.2f} "
              f"{s['recall@10']:>6.2f} {s['mrr']:>6.2f}{flag}")  # fmt: skip
    for name in ("gated", "all"):
        s = summary[name]
        print(f"{name:<12} {s['n']:>3} {s['recall@1']:>6.2f} {s['recall@5']:>6.2f} "
              f"{s['recall@10']:>6.2f} {s['mrr']:>6.2f}")  # fmt: skip
    misses = [r for r in results if r.rank is None]
    for r in misses:
        print(f"miss: [{r.case['category']}] {r.case['q']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
