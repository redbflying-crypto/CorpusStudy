"""Generated `index.md` (OKF section 8) and `log.md` (section 9) files."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from corpusstudy.okf.bundle import Bundle, load_bundle
from corpusstudy.okf.models import OKF_VERSION

# Headings and one-line descriptions for the directories of our bundle.
SECTIONS: dict[str, tuple[str, str]] = {
    "": ("CorpusStudy knowledge", "Curated Reformed Bible-study knowledge"),
    "books": ("Books of the Bible", "One concept per book: setting, purpose, outline"),
    "themes": ("Themes", "Biblical themes traced across the canon"),
    "people": ("People", "Biblical persons"),
    "doctrines": ("Doctrines", "Doctrines, framed from the Reformed view"),
    "events": ("Events", "Events of redemptive history"),
    "references": ("References", "Source works the knowledge is drawn from"),
    "references/sources": ("Sources", "Public-domain Bible texts and commentaries"),
}


def _section(rel: str) -> tuple[str, str]:
    name = rel.rsplit("/", 1)[-1]
    return SECTIONS.get(rel, (name.replace("-", " ").title(), ""))


def render_index(bundle: Bundle, directory: Path) -> str:
    rel = "" if directory == bundle.root else directory.relative_to(bundle.root).as_posix()
    heading, _ = _section(rel)
    lines = []
    if directory == bundle.root:
        lines += ["---", f'okf_version: "{OKF_VERSION}"', "---", ""]
    lines += [f"# {heading}", ""]

    subdirs = sorted(p for p in directory.iterdir() if p.is_dir() and not p.name.startswith("."))
    concepts = sorted(
        (c for c in bundle.concepts.values() if c.path.parent == directory),
        key=lambda c: c.title.lower(),
    )
    for sub in subdirs:
        sub_rel = sub.relative_to(bundle.root).as_posix()
        title, desc = _section(sub_rel)
        lines.append(f"* [{title}]({sub.name}/)" + (f" - {desc}" if desc else ""))
    for c in concepts:
        desc = c.meta.description or ""
        if c.meta.status != "stable":  # flag drafts and deprecated concepts for reviewers
            desc = f"{desc} ({c.meta.status})".strip()
        lines.append(f"* [{c.title}]({c.path.name})" + (f" - {desc}" if desc else ""))
    if not subdirs and not concepts:
        lines.append("_No concepts yet._")
    return "\n".join(lines) + "\n"


def write_indexes(root: str | Path) -> list[Path]:
    """Regenerate every directory's index.md. Returns the files that changed."""
    bundle = load_bundle(root)
    changed = []
    dirs = [bundle.root, *sorted(p for p in bundle.root.rglob("*") if p.is_dir())]
    for directory in dirs:
        if any(part.startswith(".") for part in directory.relative_to(bundle.root).parts):
            continue
        path = directory / "index.md"
        text = render_index(bundle, directory)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            changed.append(path)
    return changed


def add_log_entry(root: str | Path, kind: str, message: str, on: date | None = None) -> None:
    """Add `* **Kind**: message` under today's `## YYYY-MM-DD` heading, newest first."""
    path = Path(root) / "log.md"
    day = (on or date.today()).isoformat()
    entry = f"* **{kind}**: {message}"
    text = path.read_text(encoding="utf-8") if path.exists() else "# Knowledge Update Log\n"
    if entry in text.split(f"## {day}", 1)[-1].split("\n## ", 1)[0] and f"## {day}" in text:
        return  # already logged today
    if f"## {day}\n" in text:
        text = text.replace(f"## {day}\n", f"## {day}\n{entry}\n", 1)
    else:
        m = re.search(r"^## ", text, re.M)
        block = f"## {day}\n{entry}\n\n"
        if m:
            text = text[: m.start()] + block + text[m.start() :]
        else:
            text = text.rstrip() + "\n\n" + block
    path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
