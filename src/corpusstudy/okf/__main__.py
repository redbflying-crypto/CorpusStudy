"""CLI for the OKF knowledge bundle: python -m corpusstudy.okf COMMAND [...]

check       OKF v0.2 conformance + the CorpusStudy producer profile (exit 1 on errors)
index       regenerate every index.md
references  regenerate references/sources/*.md from data/processed/manifest.json
graph       link report: links, backlinks, orphans, broken links, usable concepts
new         draft a concept skeleton:      new Doctrine justification "Justification by Faith"
touch       record an edit (back to draft): touch doctrines/justification
verify      human review sign-off:         verify doctrines/justification [--by human:<id>]
deprecate   retire a concept:              deprecate doctrines/old --reason "merged into X"
log         add a log entry:               log "Reorganised themes" [--kind Update]
maintain    maintenance report: checks, review queue, links, coverage, gaps (no API key)
            maintain [--questions FILE ...] [--out report.md] [--json]
propose     Claude drafts a concept for a gap, for your review (API key, built corpus):
            propose Theme prayer "Prayer" --question "How should I pray?" --passage "Matt 6:5-15"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from corpusstudy.config import REPO_ROOT, get_settings
from corpusstudy.okf import lifecycle
from corpusstudy.okf.bundle import load_bundle
from corpusstudy.okf.graph import build_graph, select
from corpusstudy.okf.indexes import add_log_entry, write_indexes
from corpusstudy.okf.profile import check_bundle, summary
from corpusstudy.okf.references import write_reference_concepts


def _rel(path: Path) -> Path:
    return path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path


def _check(root: Path, no_profile: bool) -> int:
    bundle = load_bundle(root)
    issues = list(bundle.issues)
    if not no_profile:
        issues += check_bundle(bundle)
    for issue in issues:
        print(issue)
    errors = [i for i in issues if i.level == "error"]
    s = summary(bundle)
    print(
        f"{root}: OKF {bundle.okf_version or '?'}, {s['concepts']} concepts, "
        f"trust tiers {s['trust_tiers']}, {len(errors)} errors"
    )
    return 1 if errors else 0


def _graph(root: Path) -> int:
    settings = get_settings()
    bundle = load_bundle(root)
    g = build_graph(bundle)
    usable = select(bundle, use_drafts=settings.use_drafts)
    links = sum(len(v) for v in g.out.values())
    print(f"{len(g.nodes)} concepts, {links} links, {len(usable)} usable in answers "
          f"(use_drafts={settings.use_drafts})")  # fmt: skip
    for cid in sorted(g.nodes):
        if cid.startswith("references/"):
            continue
        out, back = sorted(g.out.get(cid, ())), sorted(g.into.get(cid, ()))
        mark = "usable" if cid in usable else bundle.concepts[cid].meta.trust_tier.value
        print(f"  {cid} [{mark}] -> {out or '-'}  <- {back or '-'}  cites {sorted(g.cites[cid])}")
    for cid in g.orphans():
        print(f"orphan: {cid}")
    for cid, target in g.broken:
        print(f"broken link: {cid} -> {target}")
    return 0


def _maintain(root: Path, questions: list[Path] | None, out: Path | None, as_json: bool) -> int:
    from corpusstudy.indexing.build import index_status
    from corpusstudy.indexing.store import index_path
    from corpusstudy.okf import maintenance

    settings = get_settings()
    paths = list(maintenance.EVAL_SETS) if questions is None else list(questions)
    if questions is None and settings.gap_log is not None:
        paths.append(settings.gap_log)
    search = None
    if index_status(index_path()) != "missing":
        from corpusstudy.retrieval.search import Search

        search = Search(use_drafts=True, knowledge_root=root)
    report = maintenance.build_report(load_bundle(root), search=search,
                                      questions=maintenance.load_questions(paths))  # fmt: skip
    text = (json.dumps(maintenance.as_json(report), indent=2, ensure_ascii=False) + "\n"
            if as_json else maintenance.render(report))  # fmt: skip
    if out:
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(text, end="")
    return 1 if report.errors else 0


def _propose(root: Path, args: argparse.Namespace) -> int:
    from corpusstudy.generation.llm import LLM, LLMUnavailable
    from corpusstudy.okf.propose import propose
    from corpusstudy.retrieval.search import Search

    try:
        llm = LLM()
    except LLMUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    result = propose(root, args.type, args.slug, args.title, questions=args.question,
                     passages=args.passage, search=Search(use_drafts=True, knowledge_root=root),
                     llm=llm, by=args.by)  # fmt: skip
    print(
        f"proposed {_rel(result.path)} (status draft, for your review; "
        f"{len(result.sources)} sources{', repaired once' if result.repaired else ''})"
    )
    for issue in result.warnings:
        print(f"  {issue}")
    u = result.usage
    cost = f", about ${u.cost_usd:.2f}" if u.cost_usd is not None else ""
    print(f"  {u.calls} model calls, {u.input_tokens} input and {u.output_tokens} output "
          f"tokens{cost}")  # fmt: skip
    print(f"next: read it, then `okf check`; approve with `okf verify {result.concept_id}`")
    return 0


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="python -m corpusstudy.okf",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--bundle", type=Path, default=settings.knowledge_dir)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check")
    p.add_argument("--no-profile", action="store_true", help="OKF conformance only")
    sub.add_parser("index")
    sub.add_parser("references")
    sub.add_parser("graph")
    p = sub.add_parser("new")
    p.add_argument("type", choices=sorted(lifecycle.TEMPLATES))
    p.add_argument("slug")
    p.add_argument("title")
    p.add_argument("--by", default=settings.drafter)
    p = sub.add_parser("touch")
    p.add_argument("concepts", nargs="+")
    p.add_argument("--by", default=settings.drafter)
    p = sub.add_parser("verify")
    p.add_argument("concepts", nargs="+")
    p.add_argument("--by", default=settings.reviewer)
    p = sub.add_parser("deprecate")
    p.add_argument("concepts", nargs="+")
    p.add_argument("--reason", required=True)
    p = sub.add_parser("log")
    p.add_argument("message")
    p.add_argument("--kind", default="Update")
    p = sub.add_parser("maintain")
    p.add_argument("--questions", type=Path, nargs="+",
                   help="evaluation sets (.yaml) or gap logs (.jsonl); default: the "
                   "evaluation sets and CORPUS_GAP_LOG")  # fmt: skip
    p.add_argument("--out", type=Path)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("propose")
    p.add_argument("type", choices=sorted(lifecycle.TEMPLATES))
    p.add_argument("slug")
    p.add_argument("title")
    p.add_argument("--question", action="append", default=[],
                   help="a question the concept should answer (repeatable)")  # fmt: skip
    p.add_argument("--passage", action="append", default=[],
                   help='a passage to draw on, e.g. "Matt 6:5-15" (repeatable)')  # fmt: skip
    p.add_argument("--by", default=settings.drafter)
    args = parser.parse_args(argv)
    root: Path = args.bundle

    try:
        if args.command == "check":
            return _check(root, args.no_profile)
        if args.command == "graph":
            return _graph(root)
        if args.command == "maintain":
            return _maintain(root, args.questions, args.out, args.json)
        if args.command == "propose":
            if _propose(root, args):
                return 1
        if args.command == "references":
            manifest = settings.data_dir / "processed" / "manifest.json"
            written = write_reference_concepts(root, manifest)
            for rel in written:
                print(f"wrote {rel}")
                add_log_entry(root, "Update", f"Regenerated source reference [{rel}](/{rel}).")
            if not written:
                print("source references up to date")
        elif args.command == "new":
            path = lifecycle.new_concept(root, args.type, args.slug, args.title, by=args.by)
            print(f"drafted {_rel(path)}")
        elif args.command == "touch":
            for cid in args.concepts:
                c = lifecycle.touch(root, cid, by=args.by)
                print(f"touched {c.id} (generated by {args.by}; status draft)")
        elif args.command == "verify":
            for cid in args.concepts:
                c = lifecycle.verify(root, cid, by=args.by)
                print(f"verified {c.id} by {args.by}: status stable, {c.meta.trust_tier.value}")
        elif args.command == "deprecate":
            for cid in args.concepts:
                lifecycle.deprecate(root, cid, args.reason)
                print(f"deprecated {cid}")
        elif args.command == "log":
            add_log_entry(root, args.kind, args.message)
            print("logged")
    except lifecycle.LifecycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        for issue in exc.issues:
            print(f"  {issue}", file=sys.stderr)
        return 1

    for path in write_indexes(root):
        print(f"wrote {_rel(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
