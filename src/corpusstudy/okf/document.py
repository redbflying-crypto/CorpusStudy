"""OKF documents on disk: YAML frontmatter + markdown body, links and footnotes.

Parsing keeps the frontmatter as an ordered dict so unknown keys survive a round trip
(OKF section 4.1: consumers SHOULD preserve unknown keys).
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import yaml

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
# [text](target "title"), not images.
_LINK = re.compile(r"(?<!!)\[([^\]\[]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_FOOTNOTE_REF = re.compile(r"\[\^([^\]\s]+)\](?!:)")
_FOOTNOTE_DEF = re.compile(r"^\[\^([^\]\s]+)\]:", re.M)
_WIKILINK = re.compile(r"\[\[[^\]\n]+\]\]")
_FENCE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)


class FrontmatterError(ValueError):
    pass


def split(text: str) -> tuple[dict[str, Any] | None, str]:
    """(frontmatter, body). Frontmatter is None when the file has no `---` block.

    Raises FrontmatterError when a block is present but is not a YAML mapping.
    """
    m = _FRONTMATTER.match(text)
    if not m:
        return None, text
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"unparseable YAML frontmatter: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter is not a YAML mapping")
    return data, text[m.end() :]


# ----------------------------------------------------------------------------- rendering


def _scalar(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        text = value.isoformat()
        return text.replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    dumped = yaml.safe_dump(value, allow_unicode=True, width=10_000).strip()
    return dumped.removesuffix("\n...").removesuffix("...").strip()


def _flow(value: Any) -> str:
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k}: {_flow(v)}" for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_flow(v) for v in value) + "]"
    return _scalar(value)


def _is_flat(value: Any) -> bool:
    if isinstance(value, dict):
        return all(not isinstance(v, dict | list) for v in value.values())
    if isinstance(value, list):
        return all(not isinstance(v, dict | list) for v in value)
    return True


def render_frontmatter(data: dict[str, Any]) -> str:
    """YAML in the style of the OKF spec: flow style for flat mappings and lists,
    block lists for lists of mappings (e.g. `sources`)."""
    lines = []
    for key, value in data.items():
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            if key == "verified" or all(len(v) <= 2 for v in value):
                lines.append(f"{key}:")
                lines.extend(f"  - {_flow(v)}" for v in value)
            else:
                lines.append(f"{key}:")
                for entry in value:
                    first = True
                    for k, v in entry.items():
                        lines.append(f"  {'- ' if first else '  '}{k}: {_flow(v)}")
                        first = False
        elif isinstance(value, dict) and not _is_flat(value):
            lines.append(f"{key}:")
            lines.extend(f"  {k}: {_flow(v)}" for k, v in value.items())
        else:
            lines.append(f"{key}: {_flow(value)}")
    text = "\n".join(lines)
    yaml.safe_load(text)  # must stay parseable
    return text


def render(frontmatter: dict[str, Any] | None, body: str) -> str:
    if frontmatter is None:
        return body
    body = body.lstrip("\n")
    return f"---\n{render_frontmatter(frontmatter)}\n---\n\n{body}".rstrip("\n") + "\n"


# ----------------------------------------------------------------------------- body


@dataclass(frozen=True)
class Link:
    text: str
    target: str  # as written
    concept: str | None  # resolved concept id for bundle-internal .md links
    fragment: str
    external: bool


def _strip_code(body: str) -> str:
    return _FENCE.sub("", body)


def links(body: str, concept_id: str) -> list[Link]:
    """Markdown links in the body, resolved against the concept's directory.

    Absolute paths (`/doctrines/faith.md`) are bundle-relative (OKF section 6.1).
    """
    out = []
    base = posixpath.dirname(concept_id)
    for m in _LINK.finditer(_strip_code(body)):
        text, target = m.group(1), m.group(2)
        path, _, fragment = target.partition("#")
        external = bool(re.match(r"^[a-z][a-z0-9+.-]*:", path, re.I))
        concept = None
        if path and not external and path.endswith(".md"):
            joined = path.lstrip("/") if path.startswith("/") else posixpath.join(base, path)
            concept = posixpath.normpath(joined).removesuffix(".md")
        out.append(Link(text, target, concept, fragment, external))
    return out


def footnote_refs(body: str) -> list[str]:
    return list(dict.fromkeys(_FOOTNOTE_REF.findall(_strip_code(body))))


def footnote_defs(body: str) -> list[str]:
    return list(dict.fromkeys(_FOOTNOTE_DEF.findall(_strip_code(body))))


def wikilinks(body: str) -> list[str]:
    return _WIKILINK.findall(_strip_code(body))


def resolve_path(path: str, concept_id: str) -> str:
    """A path-valued field (OKF section 6.2) as a bundle path, fragment removed."""
    path = path.partition("#")[0]
    if path.startswith("/"):
        return posixpath.normpath(path.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(concept_id), path))
