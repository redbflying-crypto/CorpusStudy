"""Ask questions from the command line.

    python -m corpusstudy.generation ask "What does Romans 5:1 teach?" [--lang fr] [--drafts]
    python -m corpusstudy.generation route "Who was Abraham?"
    python -m corpusstudy.generation smoke       # one live API call; needs ANTHROPIC_API_KEY

`ask` prints the answer, its citations and sources, and any validation warnings.
"""

from __future__ import annotations

import argparse
import sys

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
    args = parser.parse_args(argv)

    from corpusstudy.generation.answer import Answerer
    from corpusstudy.retrieval.search import Search

    search = Search(use_drafts=True if args.drafts else None)
    if args.cmd == "route":
        from corpusstudy.router import route

        r = route(args.question, Language(args.lang) if args.lang else None, search)
        print(f"route={r.route} language={r.language} refs={[x.osis for x in r.refs]} "
              f"concepts={r.concepts} concern={r.concern} ({r.reason})")  # fmt: skip
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
