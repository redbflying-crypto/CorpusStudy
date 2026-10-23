"""`references/sources/*.md`: one OKF `Reference` concept per ingested source work.

Generated from `data/processed/manifest.json` by the ingest pipeline. Knowledge concepts
cite a source through these files (OKF section 6.3, the `references/` convention):

    sources:
      - id: henry-rom3
        resource: /references/sources/henry-concise.md#Rom.3.21-Rom.3.26
      - id: kjv-rom3
        resource: /references/sources/kjv.md#Rom.3.21-Rom.3.26

The fragment is the excerpt's local id (commentaries) or an OSIS passage (Bibles).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from corpusstudy.config import Translation
from corpusstudy.ingest.commentary import COMMENTARY_SOURCES
from corpusstudy.ingest.sources import LOCK_PATH, SOURCES
from corpusstudy.okf import document

SOURCES_DIR = "references/sources"
INGEST_ACTOR = "process:ingest"

_LANGUAGE = {"en": "English", "fr": "French"}
_BIBLE_TAGS = {Translation.KJV: ["bible", "english"], Translation.LSG1910: ["bible", "french"]}


def source_path(source_id: str) -> str:
    return f"{SOURCES_DIR}/{source_id}.md"


def _bible_concept(tr: Translation, info: dict[str, Any], at: datetime) -> tuple[str, dict, str]:
    src = SOURCES[tr]
    meta = {
        "type": "Reference",
        "title": tr.full_name,
        "description": f"Public-domain Bible text quoted in {_LANGUAGE[tr.language]} answers.",
        "resource": src.homepage,
        "tags": [*_BIBLE_TAGS[tr], "public-domain"],
        "status": "stable",
        "generated": {"by": INGEST_ACTOR, "at": at},
        "verified": [{"by": INGEST_ACTOR, "at": at}],
        "source_kind": "bible",
        "translation": str(tr),
        "language": str(tr.language),
        "license": info["license"],
        "edition": info["description"],
        "verses": info["verses"],
        "psalm_titles": info["superscriptions"],
        "files": len(info["files"]),
        "checksum_lock": LOCK_PATH.relative_to(LOCK_PATH.parents[3]).as_posix(),  # repo path
        "anchor": "#<OSIS passage>, e.g. #Rom.3.21-Rom.3.26",
    }
    repairs = "".join(f"- {r}\n" for r in info["repairs"]) or "- none\n"
    body = (
        f"# {tr.full_name}\n\n"
        f"{info['description']}. Verse numbering is the translation's own; see "
        "`corpusstudy.versification` for the KJV-LSG mapping.\n\n"
        "# Citation\n\n"
        f"Cite a passage as `{source_path(src.id)}#<OSIS>`, for example "
        f"`/{source_path(src.id)}#John.3.16`.\n\n"
        "# Integrity\n\n"
        f"{len(info['files'])} source file(s) pinned to a git commit and checked against the "
        "SHA-256 lock on every rebuild. Repairs applied at ingestion:\n\n" + repairs
    )
    return source_path(src.id), meta, body


def _commentary_concept(
    source_id: str, info: dict[str, Any], at: datetime
) -> tuple[str, dict, str]:
    src = COMMENTARY_SOURCES[source_id]
    meta = {
        "type": "Reference",
        "title": src.title,
        "description": src.description,
        "resource": src.origin.split(" ")[0],
        "tags": ["commentary", src.work, "reformed", "public-domain"],
        "status": "stable",
        "generated": {"by": INGEST_ACTOR, "at": at},
        "verified": [{"by": INGEST_ACTOR, "at": at}],
        "source_kind": "commentary",
        "work": src.work,
        "language": "en",
        "license": src.license,
        "edition": src.edition,
        "excerpts": info["excerpts"],
        "sha256": info["sha256"],
        "anchor": "#<excerpt id>, e.g. " + _example_anchor(source_id),
    }
    notes = [f"- corrected {c}" for c in info.get("corrected_refs", [])]
    notes += [f"- extended {e}" for e in info.get("extended_refs", [])]
    body = (
        f"# {src.title}\n\n"
        f"{src.edition}. Imported by hand from {src.origin}; {info['excerpts']} excerpts, "
        f"{info['with_refs']} keyed to the passage they comment on.\n\n"
        "# Citation\n\n"
        f"Cite an excerpt as `/{source_path(source_id)}#<excerpt id>`, for example "
        f"`/{source_path(source_id)}{_example_anchor(source_id)}`. Excerpt ids are listed in "
        f"`data/processed/commentary/{source_id}.jsonl`.\n\n"
        "# Integrity\n\n"
        "The imported file's SHA-256 is locked; reference fixes applied at ingestion:\n\n"
        + ("\n".join(notes) if notes else "- none")
        + "\n"
    )
    return source_path(source_id), meta, body


def _example_anchor(source_id: str) -> str:
    return {"calvin-lcc": "#vi-p90", "henry-concise": "#Rom.3.21-Rom.3.26"}.get(source_id, "#…")


def reference_concepts(manifest: dict[str, Any]) -> list[tuple[str, dict, str]]:
    stamp = manifest.get("generated")
    at = datetime.fromisoformat(stamp) if stamp else datetime.now(UTC)
    out = []
    for tr in Translation:
        info = manifest.get("sources", {}).get(str(tr))
        if info:
            out.append(_bible_concept(tr, info, at))
    for source_id, info in manifest.get("commentaries", {}).items():
        if source_id in COMMENTARY_SOURCES:
            out.append(_commentary_concept(source_id, info, at))
    return out


def _without_times(meta: dict | None) -> dict:
    meta = dict(meta or {})
    for key in ("generated", "verified"):
        meta.pop(key, None)
    return meta


def write_reference_concepts(bundle_root: Path, manifest_path: Path) -> list[str]:
    """Write the source concepts; files whose content is unchanged keep their timestamps.

    Returns the concept paths written.
    """
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    written = []
    for rel, meta, body in reference_concepts(manifest):
        path = bundle_root / rel
        if path.exists():
            old_meta, old_body = document.split(path.read_text(encoding="utf-8"))
            if (
                _without_times(old_meta)
                == _without_times(document.split(document.render(meta, body))[0])
                and old_body.strip() == body.strip()
            ):
                continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document.render(meta, body), encoding="utf-8")
        written.append(rel)
    return written
