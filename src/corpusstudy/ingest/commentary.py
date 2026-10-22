"""Public-domain commentaries: sources, import and processing.

Commentary files come from CCEL (ccel.org), which this project cannot always download from,
so they are imported by hand once:

    python -m corpusstudy.ingest --import calvin-lcc path/to/calcom.doc

The file is copied to data/raw/<source>/ and its SHA-256 recorded in the lock file; later
runs process it like any other source. Output: data/processed/commentary/<source>.jsonl, one
excerpt per line, keyed to the passage it comments on.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from corpusstudy.config import ALLOWED_COMMENTARIES, Translation
from corpusstudy.ingest.sources import ChecksumError, load_lock, save_lock, sha256
from corpusstudy.ingest.thml import Excerpt, parse_ccel_thml
from corpusstudy.schemas import PassageRef
from corpusstudy.versification import versification


@dataclass(frozen=True)
class CommentarySource:
    id: str
    work: str  # key in ALLOWED_COMMENTARIES
    format: str  # "ccel-thml"
    filename: str
    title: str
    edition: str
    license: str
    origin: str  # where to get the file by hand
    # Wrong references in the source, each checked by hand against the quoted text.
    corrections: dict[str, str] = field(default_factory=dict)


COMMENTARY_SOURCES: dict[str, CommentarySource] = {
    "calvin-lcc": CommentarySource(
        id="calvin-lcc",
        work="calvin",
        format="ccel-thml",
        filename="calcom.doc",
        title="Calvin: Commentaries (selections arranged by subject)",
        edition="Library of Christian Classics XXIII, tr. and ed. Joseph Haroutunian, 1958",
        license="Public domain (CCEL DC.Rights)",
        origin="https://ccel.org/ccel/calvin/calcom (Word / ThML download)",
        corrections={
            "Acts.6.22": "Acts.16.22",  # printed "Acts 6:22"; the quoted verse is 16:22
            "Heb.15.1": "Heb.11.1",  # printed "Heb. 15:1"; "things invisible and not seen"
            "Ps.10.31": "Ps.106.31",  # CCEL tag error; the text prints "Ps. 106:31"
        },
    ),
}

assert all(s.work in ALLOWED_COMMENTARIES for s in COMMENTARY_SOURCES.values())


@dataclass
class CommentaryReport:
    source: str
    status: str = "skipped"  # skipped | ok | failed
    excerpts: int = 0
    with_refs: int = 0
    dropped_refs: list[str] = field(default_factory=list)
    corrected_refs: list[str] = field(default_factory=list)
    message: str = ""


def raw_path(source: CommentarySource, raw_dir: Path) -> Path:
    return raw_dir / source.id / source.filename


def import_file(source_id: str, path: Path, raw_dir: Path) -> Path:
    """Copy a hand-downloaded file into data/raw and record its checksum."""
    source = COMMENTARY_SOURCES[source_id]
    target = raw_path(source, raw_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    lock = load_lock()
    lock[f"{source.id}/{source.filename}"] = sha256(target)
    save_lock(lock)
    return target


def _valid_passages(
    osis_refs: list[str], source: CommentarySource, report: CommentaryReport
) -> list[PassageRef]:
    kjv = versification(Translation.KJV)
    out = []
    for osis in osis_refs:
        if osis in source.corrections:
            fixed = source.corrections[osis]
            note = f"{osis} -> {fixed}"
            if note not in report.corrected_refs:
                report.corrected_refs.append(note)
            osis = fixed
        try:
            passage = PassageRef.from_osis(osis)
        except ValueError:
            report.dropped_refs.append(osis)
            continue
        if kjv.check_passage(passage):
            report.dropped_refs.append(osis)
            continue
        out.append(passage)
    return out


def excerpt_rows(source: CommentarySource, excerpts: list[Excerpt], report: CommentaryReport):
    for ex in excerpts:
        refs = _valid_passages(ex.refs, source, report)
        mentions = _valid_passages(ex.mentions, source, report)
        yield {
            "id": f"{source.id}:{ex.id}",
            "work": source.work,
            "source": source.id,
            "topic": list(ex.topic),
            "refs": [p.osis for p in refs],
            "mentions": [p.osis for p in mentions],
            "text": ex.text,
        }


def process(source: CommentarySource, raw_dir: Path, out_dir: Path) -> CommentaryReport:
    report = CommentaryReport(source.id)
    path = raw_path(source, raw_dir)
    if not path.exists():
        report.message = (
            f"not imported; download it from {source.origin} and run "
            f"`python -m corpusstudy.ingest --import {source.id} <file>`"
        )
        return report
    key = f"{source.id}/{source.filename}"
    expected = load_lock().get(key)
    if expected and sha256(path) != expected:
        raise ChecksumError(f"{key}: file differs from the imported one; re-import it")

    if source.format != "ccel-thml":
        raise ValueError(f"unknown commentary format {source.format!r}")
    rows = list(excerpt_rows(source, parse_ccel_thml(path), report))
    out = out_dir / "commentary" / f"{source.id}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    report.status = "ok" if rows else "failed"
    report.excerpts = len(rows)
    report.with_refs = sum(1 for r in rows if r["refs"])
    return report
