"""Loading an OKF bundle and checking OKF v0.2 conformance (section 11).

The loader is a tolerant consumer: a concept with an invalid optional field is still
loaded (the field is dropped and a warning recorded); only a missing frontmatter block or
an empty `type` makes a file non-conformant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from corpusstudy.okf import document
from corpusstudy.okf.document import FrontmatterError, Link
from corpusstudy.okf.models import OkfFrontmatter

RESERVED = ("index.md", "log.md")


@dataclass(frozen=True)
class Issue:
    path: str  # bundle-relative
    level: str  # "error" | "warning"
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.path}: {self.message}"


@dataclass
class Concept:
    id: str  # bundle path without .md (OKF section 2)
    path: Path
    meta: OkfFrontmatter
    raw: dict[str, Any]  # frontmatter as written, unknown keys and order kept
    body: str
    links: list[Link] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.meta.title or self.id.rsplit("/", 1)[-1].replace("-", " ").title()

    def render(self) -> str:
        return document.render(self.raw, self.body)


@dataclass
class Bundle:
    root: Path
    concepts: dict[str, Concept] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    okf_version: str | None = None

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def conformant(self) -> bool:
        return not self.errors

    def broken_links(self) -> list[tuple[str, Link]]:
        """Links to concepts that do not exist. Allowed by OKF (section 6.1); reported only."""
        return [
            (c.id, link)
            for c in self.concepts.values()
            for link in c.links
            if link.concept and link.concept not in self.concepts
        ]


def _parse_meta(raw: dict[str, Any], rel: str, issues: list[Issue]) -> OkfFrontmatter | None:
    data = dict(raw)
    for _ in range(len(data) + 1):
        try:
            return OkfFrontmatter.model_validate(data)
        except ValidationError as exc:
            bad = {str(e["loc"][0]) for e in exc.errors() if e["loc"]}
            if "type" in bad or not bad:
                return None
            for key in sorted(bad):
                issues.append(Issue(rel, "warning", f"ignored invalid `{key}`: {data[key]!r}"))
                data.pop(key, None)
    return None


def _check_index(path: Path, rel: str, is_root: bool, issues: list[Issue]) -> str | None:
    try:
        meta, _ = document.split(path.read_text(encoding="utf-8"))
    except FrontmatterError as exc:
        issues.append(Issue(rel, "error", str(exc)))
        return None
    if meta is None:
        return None
    if not is_root or set(meta) - {"okf_version"}:
        issues.append(
            Issue(rel, "error", "index.md may only carry `okf_version`, and only at the root")
        )
    version = meta.get("okf_version")
    return str(version) if version is not None else None


def _check_log(path: Path, rel: str, issues: list[Issue]) -> None:
    text = path.read_text(encoding="utf-8")
    try:
        meta, body = document.split(text)
    except FrontmatterError:
        meta, body = {}, text
    if meta is not None:
        issues.append(Issue(rel, "error", "log.md must not have frontmatter"))
    for heading in re.findall(r"^##\s+(.+?)\s*$", body, re.M):
        try:
            date.fromisoformat(heading)
        except ValueError:
            issues.append(Issue(rel, "error", f"log date heading is not YYYY-MM-DD: {heading!r}"))


def load_bundle(root: str | Path) -> Bundle:
    root = Path(root)
    bundle = Bundle(root=root)
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if path.name == "index.md":
            version = _check_index(path, rel, path.parent == root, bundle.issues)
            if path.parent == root:
                bundle.okf_version = version
            continue
        if path.name == "log.md":
            _check_log(path, rel, bundle.issues)
            continue
        try:
            raw, body = document.split(path.read_text(encoding="utf-8"))
        except FrontmatterError as exc:
            bundle.issues.append(Issue(rel, "error", str(exc)))
            continue
        if raw is None:
            bundle.issues.append(Issue(rel, "error", "concept has no YAML frontmatter"))
            continue
        meta = _parse_meta(raw, rel, bundle.issues)
        if meta is None:
            bundle.issues.append(Issue(rel, "error", "frontmatter has no non-empty `type`"))
            continue
        concept_id = rel.removesuffix(".md")
        bundle.concepts[concept_id] = Concept(
            id=concept_id,
            path=path,
            meta=meta,
            raw=raw,
            body=body,
            links=document.links(body, concept_id),
        )
    return bundle
