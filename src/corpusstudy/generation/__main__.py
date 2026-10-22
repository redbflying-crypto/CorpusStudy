"""Ask questions from the command line.

    python -m corpusstudy.generation ask "What does Romans 5:1 teach?" [--lang fr] [--drafts]
    python -m corpusstudy.generation route "Who was Abraham?"
    python -m corpusstudy.generation smoke       # one live API call; needs ANTHROPIC_API_KEY
    python -m corpusstudy.generation eval [--live] [--json OUT]   # eval/answers.yaml

`ask` prints the answer, its citations and sources, and any validation warnings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from corpusstudy.config import Language, get_settings
from corpusstudy.schemas import ChatRequest, ChatResponse, Outcome

SMOKE_QUESTIONS = [
    ("What does Romans 5:1 teach about peace with God?", Language.EN),
    ("Que dit Jean 3.16 sur l'amour de Dieu ?", Language.FR),
]


def show(response: ChatResponse) -> None:
    a = response.answer
    print(f"[route={a.route} outcome={a.outcome} language={a.language} "
          f"interpretive={a.interpretive}]\n")  # fmt: skip
    print(a.text)
    if a.citations:
        print("\nScripture: " + ", ".join(
            f"{c.ref.display(a.language)} ({c.translation}){'' if c.verified else ' UNVERIFIED'}"
            for c in a.citations))  # fmt: skip
    for s in a.sources:
        tier = f" [{s.trust_tier}]" if s.trust_tier else ""
        print(f"  [{s.marker or '-'}] {s.label}{tier}")
    for w in response.warnings:
        print(f"warning: {w}", file=sys.stderr)


def run_eval(args: argparse.Namespace) -> int:
    from corpusstudy.generation.answer import Answerer
    from corpusstudy.generation.evaluation import (
        EVAL_ANSWERS,
        evaluate_live,
        evaluate_offline,
        load_cases,
        summarise,
        targets_met,
    )
    from corpusstudy.retrieval.search import Search

    cases = load_cases(args.cases or EVAL_ANSWERS)
    search = Search(use_drafts=True)  # the bundle as it will be once reviewed
    results = evaluate_offline(cases, search)
    if args.live:
        if not get_settings().has_api_key:
            print("live evaluation skipped: ANTHROPIC_API_KEY is not set", file=sys.stderr)
            return 2
        evaluate_live(results, Answerer(search), judge=args.judge)
    s = summarise(results)

    def pct(pair: tuple[int, int]) -> str:
        n, d = pair
        return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"

    print(f"{s['cases']} cases")
    labels = [("routing", "routing"), ("language", "language detected"),
              ("concept", "concept named"), ("advice_domain", "advice domain"),
              ("safety_cases", "safety message first"),
              ("no_false_alarm", "no false safety alarm"), ("lookups", "lookups"),
              ("comparisons", "each book compared"), ("crossrefs", "cross-refs found (info)"),
              ("refusals", "hateful requests declined"),
              ("advice_notes", "advice note first")]  # fmt: skip
    for key, label in labels:
        print(f"  {label:24} {pct(s[key])}")
    if "live" in s:
        live = s["live"]
        print("live")
        for key in ("citation_accuracy", "answers_all_verified", "answered", "safety_first",
                    "cannot_verify", "cited_expected", "faithful"):  # fmt: skip
            print(f"  {key.replace('_', ' '):24} {pct(live[key])}")
        print(f"  {'median seconds':24} {live['median_seconds']}")
        print(f"  {'cost per question':24} ${live['cost_usd_mean']} "
              f"(total ${live['cost_usd_total']})")  # fmt: skip
    print("by category: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in s["by_category"].items()))
    for r in results:
        if not r.ok:
            failed = [k for k, v in r.checks.items() if not v]
            print(f"  FAIL {r.case['q']!r}: {failed} {'; '.join(r.notes)}")
    if args.json:
        rows = [{"case": r.case, "checks": r.checks, "notes": r.notes, "live": r.live}
                for r in results]  # fmt: skip
        args.json.write_text(json.dumps({"summary": s, "results": rows}, ensure_ascii=False,
                                        indent=1, default=str), encoding="utf-8")  # fmt: skip
    met = targets_met(s)
    print("targets met" if met else "targets NOT met")
    return 0 if met else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m corpusstudy.generation")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("ask", "route"):
        p = sub.add_parser(name)
        p.add_argument("question")
        p.add_argument("--lang", choices=[lang.value for lang in Language])
        p.add_argument("--drafts", action="store_true", help="use draft knowledge concepts")
    smoke = sub.add_parser("smoke", help="live API check: answers two questions")
    smoke.add_argument("--drafts", action="store_true")
    ev = sub.add_parser("eval", help="answer evaluation (offline; --live calls the API)")
    ev.add_argument("--live", action="store_true", help="also answer through the API")
    ev.add_argument("--cases", type=Path, help="cases file (default eval/answers.yaml)")
    ev.add_argument("--judge", action="store_true", help="live: check faithfulness too")
    ev.add_argument("--json", type=Path, help="write per-case results to this file")
    args = parser.parse_args(argv)
    if args.cmd == "eval":
        return run_eval(args)

    from corpusstudy.generation.answer import Answerer
    from corpusstudy.retrieval.search import Search

    search = Search(use_drafts=True if args.drafts else None)
    if args.cmd == "route":
        from corpusstudy.router import route

        r = route(args.question, Language(args.lang) if args.lang else None, search)
        extras = {"books": r.books, "advice": r.advice, "translation": r.translation,
                  "unavailable": r.unavailable_translation,
                  "secondary": [str(x) for x in r.secondary]}  # fmt: skip
        shown = " ".join(f"{k}={v}" for k, v in extras.items() if v)
        print(f"route={r.route} language={r.language} refs={[x.osis for x in r.refs]} "
              f"concepts={r.concepts} concern={r.concern} {shown} ({r.reason})")  # fmt: skip
        return 0

    if args.cmd == "smoke":
        if not get_settings().has_api_key:
            print("smoke test skipped: ANTHROPIC_API_KEY is not set", file=sys.stderr)
            return 2
        answerer = Answerer(search)
        failed = 0
        for question, lang in SMOKE_QUESTIONS:
            print(f"\n=== {question}")
            response = answerer.answer(ChatRequest(message=question, language=lang))
            show(response)
            ok = response.answer.outcome in (Outcome.ANSWERED, Outcome.REPAIRED)
            ok = ok and response.answer.all_verified and bool(response.answer.citations)
            print(f"\n{'PASS' if ok else 'FAIL'}")
            failed += not ok
        return 1 if failed else 0

    answerer = Answerer(search)
    request = ChatRequest(
        message=args.question, language=Language(args.lang) if args.lang else None
    )
    show(answerer.answer(request))
    return 0


if __name__ == "__main__":
    sys.exit(main())
