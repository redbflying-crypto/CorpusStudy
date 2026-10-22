"""The CorpusStudy producer profile: strict rules for our own knowledge bundle.

OKF itself is permissive (section 11); these rules are what *we* guarantee for content we
produce. They never make the tolerant loader reject anything; they are reported by
`python -m corpusstudy.okf check` and enforced in the tests.

Rules
- `type` from our vocabulary, matching the concept's directory.
- `title`, `description`, `generated` present; actors follow the OKF convention.
- `status: stable` only with a `human:` verification (Reference concepts: a `process:`
  verification suffices); a draft must not carry one older than its last change.
- Knowledge concepts: `title_fr`, valid `key_passages`, `tradition`.
- Provenance: every `sources` entry has an `id` and points at `/references/sources/...`
  with a resolvable anchor; every footnote matches a `sources` id and vice versa.
- Every quotation occurs in a source its footnote cites (`okf.quotes`).
- Standard markdown links only: no `[[wikilinks]]`.
"""

from __future__ import annotations

from typing import Any

from corpusstudy.commentary import commentary
from corpusstudy.config import Stance, Translation
from corpusstudy.okf import document
from corpusstudy.okf.bundle import Bundle, Concept, Issue
from corpusstudy.okf.models import Status, actor_kind
from corpusstudy.okf.references import SOURCES_DIR
from corpusstudy.schemas import PassageRef, parse_reference
from corpusstudy.versification import versification

# type -> directory
TYPES: dict[str, str] = {
    "Bible Book": "books",
    "Theme": "themes",
    "Person": "people",
    "Doctrine": "doctrines",
    "Event": "events",
    "Reference": "references",
}
KNOWLEDGE_TYPES = {t for t in TYPES if t != "Reference"}
_BIBLE_SOURCES = {"kjv": Translation.KJV, "lsg1910": Translation.LSG1910}


def _err(c: Concept, msg: str) -> Issue:
    return Issue(f"{c.id}.md", "error", msg)


def _warn(c: Concept, msg: str) -> Issue:
    return Issue(f"{c.id}.md", "warning", msg)


def _check_actor(c: Concept, field: str, actor: str, out: list[Issue]) -> None:
    if actor_kind(actor) == "unknown":
        out.append(
            _err(c, f"`{field}` actor {actor!r} is not <producer>/<version>, human:<id> "
                 "or process:<id>")
        )  # fmt: skip


def _check_anchor(c: Concept, src_id: str, resource: str, out: list[Issue]) -> None:
    path = document.resolve_path(resource, c.id)
    if not path.startswith(SOURCES_DIR + "/"):
        out.append(
            _err(c, f"source `{src_id}` must point into /{SOURCES_DIR}/ "
                 f"(public-domain sources only), got {resource!r}")
        )  # fmt: skip
        return
    source = path.removeprefix(SOURCES_DIR + "/").removesuffix(".md")
    fragment = resource.partition("#")[2]
    if not fragment:
        return  # the whole work
    if source in _BIBLE_SOURCES:
        tr = _BIBLE_SOURCES[source]
        try:
            passage = PassageRef.from_osis(fragment)
        except ValueError as exc:
            out.append(_err(c, f"source `{src_id}`: bad passage anchor {fragment!r}: {exc}"))
            return
        for problem in versification(tr).check_passage(passage):
            out.append(_err(c, f"source `{src_id}`: {problem} in {tr}"))
        return
    store = commentary(source)
    if store is None:
        out.append(_warn(c, f"source `{src_id}`: {source} not built, anchor not checked"))
    elif store.get(fragment) is None:
        out.append(_err(c, f"source `{src_id}`: no excerpt {fragment!r} in {source}"))


def check_concept(c: Concept, bundle: Bundle) -> list[Issue]:
    out: list[Issue] = []
    meta = c.meta
    kind = meta.type
    folder = c.id.split("/", 1)[0]

    if kind not in TYPES:
        out.append(_err(c, f"type {kind!r} is not one of {sorted(TYPES)}"))
    elif TYPES[kind] != folder:
        out.append(_err(c, f"a {kind!r} concept belongs in {TYPES[kind]}/, not {folder}/"))
    for key in ("title", "description"):
        if not getattr(meta, key):
            out.append(_err(c, f"missing `{key}`"))

    # Trust and lifecycle (OKF sections 5.2-5.4).
    if meta.generated is None:
        out.append(_err(c, "missing `generated: { by, at }`"))
    else:
        _check_actor(c, "generated.by", meta.generated.by, out)
        if meta.generated.at is None:
            out.append(_err(c, "`generated.at` is required by this profile"))
    for v in meta.verified:
        _check_actor(c, "verified.by", v.by, out)
        if v.at is None:
            out.append(_err(c, "every `verified` entry needs `at`"))
    status = meta.lifecycle
    if status is None:
        out.append(_err(c, f"status {meta.status!r} is not draft, stable or deprecated"))
    if "status" not in c.raw:
        out.append(_err(c, "set `status` explicitly (draft until reviewed)"))
    human = [v for v in meta.verified if actor_kind(v.by) == "human"]
    if status == Status.STABLE and kind in KNOWLEDGE_TYPES and not human:
        out.append(_err(c, "`status: stable` needs a human verification (human:<id>)"))
    if status == Status.STABLE and kind == "Reference" and not meta.verified:
        out.append(_err(c, "`status: stable` needs a verification"))
    last = meta.last_verified
    gen_at = meta.generated.at if meta.generated else None
    if human and last and gen_at and gen_at > last and status == Status.STABLE:
        out.append(_err(c, "content changed after the last review: set `status: draft`"))

    # Project keys for knowledge concepts.
    extra = meta.extra
    if kind in KNOWLEDGE_TYPES:
        if not extra.get("title_fr"):
            out.append(_err(c, "missing `title_fr` (answers are bilingual)"))
        passages = extra.get("key_passages") or []
        if not passages:
            out.append(_err(c, "missing `key_passages`"))
        for p in passages:
            try:
                parsed = parse_reference(str(p))
            except ValueError as exc:
                out.append(_err(c, f"key passage {p!r}: {exc}"))
                continue
            for problem in versification(Translation.KJV).check_passage(parsed):
                out.append(_err(c, f"key passage {p!r}: {problem}"))
        tradition = extra.get("tradition", Stance.REFORMED)
        allowed = [s.value for s in Stance]
        if tradition not in allowed:
            out.append(_err(c, f"tradition {tradition!r} is not one of {allowed}"))

    # Provenance (OKF section 5.1).
    ids: list[str] = []
    for i, src in enumerate(meta.sources):
        if not src.id:
            out.append(_err(c, f"sources[{i}] needs an `id` (footnote key)"))
        else:
            ids.append(src.id)
        _check_anchor(c, src.id or f"#{i}", src.resource, out)
    if len(ids) != len(set(ids)):
        out.append(_err(c, "duplicate `sources` ids"))
    refs, defs = document.footnote_refs(c.body), document.footnote_defs(c.body)
    for ref in refs:
        if ref not in ids:
            out.append(_err(c, f"footnote [^{ref}] has no matching `sources` id"))
        if ref not in defs:
            out.append(_err(c, f"footnote [^{ref}] is not defined in the body"))
    for sid in ids:
        if sid not in refs:
            out.append(_warn(c, f"source `{sid}` is never cited by a footnote"))
    if kind in KNOWLEDGE_TYPES and not ids:
        out.append(_err(c, "a knowledge concept needs at least one source"))

    # Quotations must be found in the source their footnote cites.
    if kind in KNOWLEDGE_TYPES:
        from corpusstudy.okf.quotes import check_quotes

        out.extend(check_quotes(c))

    # Links (OKF section 6).
    if document.wikilinks(c.body):
        out.append(_err(c, "use standard markdown links, not [[wikilinks]]"))
    for link in c.links:
        if link.concept and link.concept not in bundle.concepts:
            out.append(_warn(c, f"link to missing concept {link.target!r}"))
    return out


def check_bundle(bundle: Bundle) -> list[Issue]:
    issues: list[Issue] = []
    if bundle.okf_version is None:
        issues.append(Issue("index.md", "error", 'root index.md must declare okf_version: "0.2"'))
    if not (bundle.root / "log.md").exists():
        issues.append(Issue("log.md", "error", "the bundle keeps a root log.md"))
    for c in bundle.concepts.values():
        issues.extend(check_concept(c, bundle))
    return issues


def summary(bundle: Bundle) -> dict[str, Any]:
    tiers: dict[str, int] = {}
    for c in bundle.concepts.values():
        tier = c.meta.trust_tier.value
        tiers[tier] = tiers.get(tier, 0) + 1
    return {"concepts": len(bundle.concepts), "trust_tiers": tiers}
