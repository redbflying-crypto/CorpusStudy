"""Phase 2 pipeline: fetch -> parse -> repair -> validate -> write.

Outputs
- data/processed/<TRANSLATION>.jsonl     one verse per line, in the translation's own numbering
- data/processed/manifest.json           sources, checksums and counts
- resources/versification/<TR>.json      verse counts per chapter (committed, used offline)
- resources/versification/KJV-LSG1910.json  verse links where the two numberings differ
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from corpusstudy.canon import BOOKS, BOOKS_BY_OSIS
from corpusstudy.config import Translation, get_settings
from corpusstudy.ingest.align import Link, align_translations
from corpusstudy.ingest.commentary import COMMENTARY_SOURCES, CommentaryReport, process, raw_path
from corpusstudy.ingest.osis import parse_osis
from corpusstudy.ingest.raw import RawVerse
from corpusstudy.ingest.sources import RESOURCES, SOURCES, Source, fetch, sha256
from corpusstudy.ingest.usfm import parse_usfm
from corpusstudy.okf.indexes import add_log_entry, write_indexes
from corpusstudy.okf.references import write_reference_concepts

log = logging.getLogger(__name__)

VERSIFICATION_DIR = RESOURCES / "versification"


@dataclass
class Report:
    translation: Translation
    verses: int = 0
    superscriptions: int = 0
    repairs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_source(source: Source, directory: Path) -> list[RawVerse]:
    if source.format == "osis":
        return [v for f in source.files for v in parse_osis(directory / f.name)]
    if source.format == "usfm":
        return [v for f in source.files for v in parse_usfm(directory / f.name)]
    raise ValueError(f"unknown source format {source.format!r}")


def repair_numbering(verses: Iterable[RawVerse], report: Report) -> list[RawVerse]:
    """Fix a verse number that repeats where the next number is missing (a source typo).

    LSG has Ps 119:115 typed as "\\v 11 5..." (a stray space): it reads as a second verse 11
    whose text starts with "5". The digits are moved back into the number and the repair logged.
    """
    by_chapter: dict[tuple[str, int], list[RawVerse]] = defaultdict(list)
    for v in verses:
        by_chapter[(v.book, v.chapter)].append(v)
    out: list[RawVerse] = []
    for (book, chapter), vs in by_chapter.items():
        seen: set[int] = set()
        numbers = {v.verse for v in vs}
        prev = -1
        for v in vs:
            if v.verse in seen and prev + 1 not in numbers:
                text = v.text
                m = re.match(r"(\d+)\s*", text)
                if m and int(f"{v.verse}{m.group(1)}") == prev + 1:
                    text = text[m.end() :]
                fixed = RawVerse(book, chapter, prev + 1, text)
                report.repairs.append(f"{book}.{chapter}.{v.verse} (repeat) -> {fixed.verse}")
                numbers.add(fixed.verse)
                v = fixed
            seen.add(v.verse)
            prev = v.verse
            out.append(v)
    return out


def validate(verses: list[RawVerse], report: Report) -> None:
    keys = [v.key for v in verses]
    if len(keys) != len(set(keys)):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        report.errors.append(f"duplicate verses: {dupes[:10]}")
    chapters: dict[tuple[str, int], list[int]] = defaultdict(list)
    for v in verses:
        if v.book not in BOOKS_BY_OSIS:
            report.errors.append(f"unknown book {v.book}")
            continue
        if v.verse == 0 and v.book != "Ps":
            report.errors.append(f"superscription outside Psalms: {v.key}")
        chapters[(v.book, v.chapter)].append(v.verse)
    for book in BOOKS:
        for ch in range(1, book.chapters + 1):
            nums = sorted(n for n in chapters.get((book.osis, ch), []) if n > 0)
            if not nums:
                report.errors.append(f"missing chapter {book.osis} {ch}")
            elif nums != list(range(1, nums[-1] + 1)):
                missing = sorted(set(range(1, nums[-1] + 1)) - set(nums))
                report.errors.append(f"{book.osis} {ch}: missing verses {missing}")
    extra = {c for c in chapters if c[1] > BOOKS_BY_OSIS[c[0]].chapters}
    if extra:
        report.errors.append(f"chapters beyond the canon: {sorted(extra)}")
    report.verses = sum(1 for v in verses if v.verse > 0)
    report.superscriptions = sum(1 for v in verses if v.verse == 0)


def _sort_key(v: RawVerse) -> tuple[int, int, int]:
    return (BOOKS_BY_OSIS[v.book].order, v.chapter, v.verse)


def write_jsonl(verses: list[RawVerse], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for v in sorted(verses, key=_sort_key):
            row = {"book": v.book, "chapter": v.chapter, "verse": v.verse, "text": v.text}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def versification_table(verses: list[RawVerse]) -> dict:
    counts: dict[str, list[int]] = {}
    supers: dict[str, list[int]] = defaultdict(list)
    for book in BOOKS:
        counts[book.osis] = [0] * book.chapters
    for v in verses:
        if v.verse == 0:
            supers[v.book].append(v.chapter)
        else:
            row = counts[v.book]
            row[v.chapter - 1] = max(row[v.chapter - 1], v.verse)
    return {
        "verse_counts": counts,
        "superscriptions": {b: sorted(chs) for b, chs in supers.items()},
    }


def _osis(key: tuple[str, int, int]) -> str:
    return f"{key[0]}.{key[1]}.{key[2]}"


def mapping_table(links: list[Link], ratio: float) -> dict:
    """Keep only the links that are not plain identity."""
    diff = [
        {"KJV": [_osis(k) for k in link.left], "LSG1910": [_osis(k) for k in link.right]}
        for link in links
        if not (len(link.left) == len(link.right) == 1 and link.left[0] == link.right[0])
    ]
    return {
        "description": "KJV <-> LSG1910 verse links where the numbering differs; "
        "every other verse maps to the same number.",
        "method": "Gale-Church length alignment with name/number anchors, per run of "
        "chapters whose verse numbering differs",
        "length_ratio": round(ratio, 4),
        "links": diff,
    }


def _dump(obj: dict, path: Path) -> None:
    """Pretty JSON with list-of-scalars and link objects kept on one line (small, diff-friendly)."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def compact(value: object) -> bool:
        if isinstance(value, list):
            return all(not isinstance(x, dict | list) for x in value)
        # A link object: {"KJV": [...], "LSG1910": [...]}
        return (
            isinstance(value, dict)
            and len(value) <= 2
            and all(isinstance(x, list) for x in value.values())
        )

    def render(value: object, indent: int) -> str:
        pad = " " * indent
        if compact(value) or not isinstance(value, dict | list):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, list):
            items = [pad + " " + render(x, indent + 1) for x in value]
            return "[\n" + ",\n".join(items) + "\n" + pad + "]"
        items = [
            f"{pad} {json.dumps(k, ensure_ascii=False)}: {render(v, indent + 1)}"
            for k, v in value.items()
        ]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"

    path.write_text(render(obj, 0) + "\n", encoding="utf-8")


def run(
    translations: Iterable[Translation] = tuple(Translation),
    *,
    data_dir: Path | None = None,
    update_lock: bool = False,
    write_resources: bool = True,
    commentaries: bool = True,
) -> tuple[dict[Translation, Report], dict[str, CommentaryReport]]:
    data_dir = data_dir or get_settings().data_dir
    raw_dir, out_dir = data_dir / "raw", data_dir / "processed"
    reports: dict[Translation, Report] = {}
    parsed: dict[Translation, list[RawVerse]] = {}
    manifest: dict = {"generated": datetime.now(UTC).isoformat(timespec="seconds"), "sources": {}}

    for tr in translations:
        source = SOURCES[tr]
        report = Report(tr)
        directory = fetch(source, raw_dir, update_lock=update_lock)
        verses = repair_numbering(parse_source(source, directory), report)
        validate(verses, report)
        reports[tr] = report
        if report.errors:
            log.error("%s: %d validation errors, not written", tr, len(report.errors))
            continue
        parsed[tr] = verses
        write_jsonl(verses, out_dir / f"{tr}.jsonl")
        if write_resources:
            _dump(versification_table(verses), VERSIFICATION_DIR / f"{tr}.json")
        manifest["sources"][str(tr)] = {
            "description": source.description,
            "license": source.license,
            "homepage": source.homepage,
            "files": {
                f.name: {"url": f.url, "sha256": sha256(directory / f.name)} for f in source.files
            },
            "verses": report.verses,
            "superscriptions": report.superscriptions,
            "repairs": report.repairs,
            "output": f"{tr}.jsonl",
            "output_sha256": sha256(out_dir / f"{tr}.jsonl"),
        }

    if Translation.KJV in parsed and Translation.LSG1910 in parsed:
        links, ratio = align_translations(parsed[Translation.KJV], parsed[Translation.LSG1910])
        if write_resources:
            _dump(mapping_table(links, ratio), VERSIFICATION_DIR / "KJV-LSG1910.json")
        manifest["alignment"] = {"links": len(links), "length_ratio": round(ratio, 4)}

    commentary_reports: dict[str, CommentaryReport] = {}
    if commentaries:
        for source in COMMENTARY_SOURCES.values():
            cr = process(source, raw_dir, out_dir)
            commentary_reports[source.id] = cr
            if cr.status == "ok":
                manifest.setdefault("commentaries", {})[source.id] = {
                    "work": source.work,
                    "title": source.title,
                    "edition": source.edition,
                    "license": source.license,
                    "origin": source.origin,
                    "sha256": sha256(raw_path(source, raw_dir)),
                    "excerpts": cr.excerpts,
                    "with_refs": cr.with_refs,
                    "corrected_refs": cr.corrected_refs,
                    "extended_refs": cr.extended_refs,
                    "dropped_refs": cr.dropped_refs,
                    "output": f"commentary/{source.id}.jsonl",
                }

    if parsed or commentary_reports:
        _dump(manifest, out_dir / "manifest.json")
        if write_resources:
            update_knowledge_references(out_dir / "manifest.json")
    return reports, commentary_reports


def update_knowledge_references(manifest_path: Path) -> list[str]:
    """Refresh the OKF source concepts (knowledge/references/sources) and the indexes."""
    root = get_settings().knowledge_dir
    written = write_reference_concepts(root, manifest_path)
    for rel in written:
        add_log_entry(root, "Update", f"Regenerated source reference [{rel}](/{rel}).")
    if written:
        write_indexes(root)
    return written
