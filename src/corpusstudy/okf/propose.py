"""Phase 9: Claude proposes a knowledge concept for a gap; you review it.

    python -m corpusstudy.okf propose Theme prayer "Prayer" --question "How should I pray?"

1. Evidence: Scripture (KJV) and commentary (Calvin, Matthew Henry) retrieved for the title
   and the questions it should answer. Each piece gets a footnote id (`kjv-matt6-5`).
2. The model writes the concept from that evidence only, under the house rules: the type's
   sections, a footnote after every claim, quotations word for word, other traditions
   summarised without quotation, specific aliases.
3. Code assembles the file: `sources` and footnote definitions come from the evidence, never
   from the model, so every anchor is a real passage or excerpt. The draft must pass the
   producer profile (quotations included); one repair round, then it is refused and nothing
   is written.
4. The concept is written as `status: draft`, `generated` by the drafter, never verified,
   and logged. Answers ignore it until you review it (`okf verify`).

Needs the API key and the built corpus and index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from corpusstudy.config import Language, get_settings
from corpusstudy.generation.evidence import label
from corpusstudy.indexing.analysis import detect_language, fold
from corpusstudy.okf import document
from corpusstudy.okf.bundle import Bundle, Concept, Issue, load_bundle, parse_meta
from corpusstudy.okf.indexes import add_log_entry
from corpusstudy.okf.lifecycle import TEMPLATES, LifecycleError
from corpusstudy.okf.models import Status
from corpusstudy.okf.profile import TYPES, check_concept
from corpusstudy.okf.references import SOURCES_DIR
from corpusstudy.schemas import Chunk, SourceType, Usage

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SOURCES = 24
PER_QUERY = 8
_SHORT = {"henry-concise": "henry", "calvin-lcc": "calvin"}

DRAFT_SYSTEM = """\
You draft knowledge concepts for CorpusStudy, a personal Bible-study assistant in the
Reformed (Protestant, Calvinist) tradition. A person reviews every draft before the
assistant may use it, so write for that reviewer: plain, accurate, checkable.

Rules:
- Use only the sources given. Every factual sentence or group of sentences ends with a
  footnote marker naming the source it comes from, e.g. [^kjv-rom5-1] or
  [^henry-rom5-1][^kjv-rom5-1]. Use only the ids given; do not write footnote definitions.
- Quotations in "double quotes" must be copied word for word from the source whose
  footnote follows them in the same paragraph (an ellipsis "..." may join fragments).
  Quote the King James wording for Scripture; the verse numbers in parentheses, "(5)",
  are not part of the text. Do not quote anything else.
- Present the Reformed reading first and as the concept's own view, citing Calvin or
  Matthew Henry where they speak to it. Where other traditions read the subject
  differently, add a short paragraph starting "These are summaries for orientation, not
  drawn from the corpus:" followed by a bullet list, without quotations or footnotes.
- Write the sections named, in that order, in English. "Key passages" lists the main
  passages as references ("Matthew 6:5-15; Luke 11:1-13") and may link related concepts.
- Link related concepts only from the list given, as Markdown links: [prayer](/themes/prayer.md).
- description: one sentence. title_fr: the French title. key_passages: 2 to 6 references
  in the form "Matt 6:5-15". tags: a few lowercase words.
- aliases / aliases_fr: specific names a question would use for this subject, preferably
  phrases. Never a bare common word, a name shared by several people, or another concept's
  title. Keep them few.
- Do not invent facts, dates or sources. If the sources say little about a section, keep
  it short.
"""

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "title_fr": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "aliases_fr": {"type": "array", "items": {"type": "string"}},
        "key_passages": {"type": "array", "items": {"type": "string"}},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"heading": {"type": "string"}, "text": {"type": "string"}},
                "required": ["heading", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "description",
        "tags",
        "title_fr",
        "aliases",
        "aliases_fr",
        "key_passages",
        "sections",
    ],  # fmt: skip
    "additionalProperties": False,
}


class ProposalError(LifecycleError):
    pass


@dataclass(frozen=True)
class Source:
    id: str  # footnote key and `sources` id
    resource: str  # /references/sources/<work>.md#<anchor>
    title: str  # footnote text
    text: str


@dataclass
class Proposal:
    path: Path
    concept_id: str
    sources: list[str]
    warnings: list[Issue] = field(default_factory=list)
    repaired: bool = False
    usage: Usage = field(default_factory=Usage)


# ----------------------------------------------------------------------------- evidence


def _source(chunk: Chunk) -> tuple[str, str] | None:
    """(id stem, resource) for a KJV or commentary chunk; None for anything else."""
    if chunk.source_type == SourceType.SCRIPTURE:
        if str(chunk.translation) != "KJV":
            return None
        local, work, short = chunk.id.split(":", 1)[1], "kjv", "kjv"
    elif chunk.source_type == SourceType.COMMENTARY:
        work = chunk.metadata.get("source") or ""
        excerpt = chunk.metadata.get("excerpt_id") or ""
        if not work or ":" not in excerpt:
            return None
        local, short = excerpt.split(":", 1)[1], _SHORT.get(work, work)
    else:
        return None
    ref = chunk.refs[0] if chunk.refs else None
    if ref and ref.start_verse:
        stem = f"{short}-{ref.book.lower()}{ref.start_chapter}-{ref.start_verse}"
    elif ref:  # a chapter outline
        stem = f"{short}-{ref.book.lower()}{ref.start_chapter}-{local.rsplit('.', 1)[-1].lower()}"
    else:
        stem = f"{short}-" + re.sub(r"[^a-z0-9]+", "-", local.lower()).strip("-")
    return stem, f"/{SOURCES_DIR}/{work}.md#{local}"


def gather(search: Any, title: str, questions: list[str], passages: list[str] = (),
           limit: int = MAX_SOURCES) -> list[Source]:  # fmt: skip
    """KJV passages and commentary excerpts for the given passages, the title and the
    questions, in that order. The parts of one excerpt are joined into one source."""
    found: dict[str, dict[str, Any]] = {}  # resource -> id, title, texts
    used_ids: set[str] = set()
    queries = [(p, Language.EN) for p in passages] + [(title, Language.EN)]
    queries += [(q, detect_language(q)) for q in questions]
    for query, language in queries:
        for r in search.search(query, language, k=PER_QUERY, kinds=("bible", "commentary")):
            source = _source(r.chunk)
            if source is None:
                continue
            stem, resource = source
            text = r.chunk.text
            if r.chunk.source_type == SourceType.SCRIPTURE:
                text = re.sub(r"\[(\d+)\]\s*", r"(\1) ", text)
            if resource in found:
                if text not in found[resource]["texts"]:
                    found[resource]["texts"].append(text)
                continue
            if len(found) >= limit:
                continue
            sid, n = stem, 2
            while sid in used_ids:
                sid, n = f"{stem}-{n}", n + 1
            used_ids.add(sid)
            found[resource] = {"id": sid, "title": label(r.chunk), "texts": [text]}
    return [Source(f["id"], resource, f["title"], " ".join(f["texts"]))
            for resource, f in found.items()]  # fmt: skip


def _related(bundle: Bundle) -> list[str]:
    return [f"- /{cid}.md: {c.title}" for cid, c in sorted(bundle.concepts.items())
            if c.meta.type != "Reference" and c.meta.lifecycle != Status.DEPRECATED]  # fmt: skip


def request(type_: str, title: str, questions: list[str], sources: list[Source],
            bundle: Bundle) -> str:  # fmt: skip
    blocks = "\n".join(f'<source id="{s.id}" title="{s.title}">\n{s.text}\n</source>'
                       for s in sources)  # fmt: skip
    asked = "\n".join(f"- {q}" for q in questions) or "- (none given)"
    return (
        f'Draft a {type_} concept titled "{title}".\n\n'
        f"Sections, in order: {', '.join(TEMPLATES[type_])}.\n\n"
        f"Questions it should help answer:\n{asked}\n\n"
        f"Concepts you may link to:\n{chr(10).join(_related(bundle))}\n\n"
        f"<sources>\n{blocks}\n</sources>"
    )


# ----------------------------------------------------------------------------- assembly


def _names(bundle: Bundle) -> dict[str, str]:
    """Folded title or alias -> the concept that uses it."""
    names: dict[str, str] = {}
    for cid, c in bundle.concepts.items():
        extra = c.meta.extra
        for name in [c.title, extra.get("title_fr") or "", *(extra.get("aliases") or []),
                     *(extra.get("aliases_fr") or [])]:  # fmt: skip
            if name:
                names.setdefault(fold(str(name)).strip(), cid)
    return names


def assemble(type_: str, slug: str, title: str, data: dict[str, Any], sources: list[Source],
             bundle: Bundle, *, by: str, now: datetime) -> tuple[Concept, list[str]]:  # fmt: skip
    """The concept file from the model's reply, and the problems code can see itself."""
    problems: list[str] = []
    given = {str(s.get("heading", "")).strip().lower(): str(s.get("text", "")).strip()
             for s in data.get("sections") or []}  # fmt: skip
    parts = []
    for heading in TEMPLATES[type_]:
        text = given.pop(heading.lower(), "")
        if not text:
            problems.append(f"section {heading!r} is missing or empty")
        text = re.sub(r"^\[\^[^\]]+\]:.*$\n?", "", text, flags=re.M).strip()  # own definitions
        parts.append(f"# {heading}\n\n{text}\n")
    for heading in given:
        problems.append(f"section {heading!r} is not one of {TEMPLATES[type_]}")
    body = "\n".join(parts)

    by_id = {s.id: s for s in sources}
    cited = list(dict.fromkeys(document.footnote_refs(body)))
    for ref in cited:
        if ref not in by_id:
            problems.append(f"footnote [^{ref}] is not one of the source ids given")
    used = [by_id[ref] for ref in cited if ref in by_id]
    if used:
        body = body.rstrip() + "\n\n" + "\n".join(f"[^{s.id}]: {s.title}" for s in used) + "\n"

    names, cid = _names(bundle), f"{TYPES[type_]}/{slug}"
    aliases = [str(a).strip() for a in data.get("aliases") or [] if str(a).strip()]
    aliases_fr = [str(a).strip() for a in data.get("aliases_fr") or [] if str(a).strip()]
    for name in [title, *aliases, *aliases_fr]:
        other = names.get(fold(name).strip())
        if other and other != cid:
            problems.append(f"name {name!r} is already used by {other}")

    raw = {
        "type": type_,
        "title": title,
        "description": str(data.get("description") or "").strip(),
        "tags": [str(t).strip() for t in data.get("tags") or [] if str(t).strip()],
        "status": Status.DRAFT.value,
        "generated": {"by": by, "at": now},
        "sources": [{"id": s.id, "resource": s.resource, "title": s.title} for s in used],
        "title_fr": str(data.get("title_fr") or "").strip(),
        "aliases": aliases,
        "aliases_fr": aliases_fr,
        "key_passages": [str(p).strip() for p in data.get("key_passages") or []],
        "tradition": "reformed",
        "other_views": True,
    }
    text = document.render(raw, body)
    meta, body = document.split(text)
    parsed = parse_meta(meta or {}, f"{cid}.md", [])
    if parsed is None:
        raise ProposalError(f"{cid}: frontmatter unreadable")
    path = bundle.root / f"{cid}.md"
    return Concept(cid, path, parsed, meta or {}, body, document.links(body, cid)), problems


# ----------------------------------------------------------------------------- the command


def propose(root: str | Path, type_: str, slug: str, title: str, *, questions: list[str],
            search: Any, llm: Any, passages: list[str] = (), by: str | None = None,
            now: datetime | None = None) -> Proposal:  # fmt: skip
    """Draft a concept for review. Raises ProposalError and writes nothing when the draft
    does not pass the producer profile after one repair round."""
    root = Path(root)
    by = by or get_settings().drafter
    if type_ not in TEMPLATES:
        raise ProposalError(f"type must be one of {sorted(TEMPLATES)}")
    if not SLUG.match(slug):
        raise ProposalError(f"slug {slug!r}: lowercase words joined by hyphens")
    path = root / TYPES[type_] / f"{slug}.md"
    if path.exists():
        raise ProposalError(f"{path} already exists: edit it and run `okf touch` instead")
    bundle = load_bundle(root)
    sources = gather(search, title, questions, passages)
    if not sources:
        raise ProposalError(f"no Scripture or commentary found for {title!r}")
    now = (now or datetime.now(UTC)).replace(microsecond=0)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": request(type_, title, questions, sources, bundle)}
    ]
    repaired = False
    for attempt in range(2):
        reply = llm.draft(DRAFT_SYSTEM, messages, DRAFT_SCHEMA)
        if reply.declined or not reply.data:
            raise ProposalError(f"the model returned no draft ({reply.stop_reason})")
        concept, problems = assemble(type_, slug, title, reply.data, sources, bundle,
                                     by=by, now=now)  # fmt: skip
        issues = check_concept(concept, bundle)
        problems += [i.message for i in issues if i.level == "error"]
        if not problems:
            break
        if attempt == 1:
            failed = [Issue(f"{concept.id}.md", "error", p) for p in problems]
            raise ProposalError(f"{concept.id}: the draft failed the checks twice", failed)
        repaired = True
        messages.append({"role": "assistant", "content": [
            b.to_dict() if hasattr(b, "to_dict") else b for b in reply.content]})  # fmt: skip
        messages.append({"role": "user", "content": (
            "The draft has these problems:\n" + "\n".join(f"- {p}" for p in problems)
            + "\nReturn the whole concept again, corrected. Remove a quotation rather than "
            "guess its wording.")})  # fmt: skip

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(concept.render(), encoding="utf-8")
    asked = f" for {len(questions)} unanswered question{'s' * (len(questions) != 1)}"
    add_log_entry(root, "Creation", f"Proposed [{title}](/{concept.id}.md) for review, "
                  f"drafted by {by} from the corpus{asked if questions else ''}.")  # fmt: skip
    usage = llm.take_usage() if hasattr(llm, "take_usage") else Usage()
    return Proposal(path, concept.id, [s["id"] for s in concept.raw["sources"]],
                    [i for i in issues if i.level == "warning"], repaired, usage)  # fmt: skip
