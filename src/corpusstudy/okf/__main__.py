"""CLI: python -m corpusstudy.okf {check,index,references} [--bundle DIR]

check        OKF v0.2 conformance + the CorpusStudy producer profile (exit 1 on errors)
index        regenerate every index.md
references   regenerate references/sources/*.md from data/processed/manifest.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from corpusstudy.config import REPO_ROOT, get_settings
from corpusstudy.okf.bundle import load_bundle
from corpusstudy.okf.indexes import add_log_entry, write_indexes
from corpusstudy.okf.profile import check_bundle, summary
from corpusstudy.okf.references import write_reference_concepts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m corpusstudy.okf",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=["check", "index", "references"])
    parser.add_argument("--bundle", type=Path, default=get_settings().knowledge_dir)
    parser.add_argument("--no-profile", action="store_true", help="OKF conformance only")
    args = parser.parse_args(argv)
    root: Path = args.bundle

    if args.command == "check":
        bundle = load_bundle(root)
        issues = list(bundle.issues)
        if not args.no_profile:
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

    if args.command == "references":
        manifest = get_settings().data_dir / "processed" / "manifest.json"
        written = write_reference_concepts(root, manifest)
        for rel in written:
            print(f"wrote {rel}")
            add_log_entry(root, "Update", f"Regenerated source reference [{rel}](/{rel}).")
        if not written:
            print("source references up to date")

    for path in write_indexes(root):
        print(f"wrote {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
