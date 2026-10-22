"""OKF v0.2: documents, tolerant models, bundle conformance, producer profile, indexes,
source references, and the committed knowledge bundle."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from corpusstudy.config import get_settings
from corpusstudy.okf import document
from corpusstudy.okf.bundle import load_bundle
from corpusstudy.okf.indexes import add_log_entry, render_index, write_indexes
from corpusstudy.okf.models import OkfFrontmatter, Status, TrustTier, actor_kind
from corpusstudy.okf.profile import check_bundle
from corpusstudy.okf.references import reference_concepts, write_reference_concepts

# ----------------------------------------------------------------------------- documents


def test_split_and_render_round_trip_keeps_unknown_keys():
    text = (
        "---\ntype: Doctrine\nmy_key: {nested: [1, 2]}\ntitle: Grace\n"
        "generated: { by: human:me, at: 2026-10-23T10:00:00Z }\n---\n\n# Body\n"
    )
    meta, body = document.split(text)
    assert list(meta) == ["type", "my_key", "title", "generated"]
    assert meta["generated"]["at"] == datetime(2026, 10, 23, 10, tzinfo=UTC)
    again, body2 = document.split(document.render(meta, body))
    assert again == meta and body2.strip() == "# Body"
    assert "at: 2026-10-23T10:00:00Z" in document.render(meta, body)  # unquoted datetime


def test_split_without_and_with_bad_frontmatter():
    assert document.split("# Just markdown\n") == (None, "# Just markdown\n")
    with pytest.raises(document.FrontmatterError):
        document.split("---\n- a list\n---\nbody")
    with pytest.raises(document.FrontmatterError):
        document.split("---\ntype: [unclosed\n---\nbody")


def test_links_resolve_bundle_absolute_relative_and_external():
    body = (
        "See [faith](/doctrines/faith.md#definition), [grace](./grace.md), "
        "[Paul](../people/paul.md), [web](https://example.com/x.md), ![img](/a.md).\n"
        "```\n[not a link](/code.md)\n```\n"
    )
    found = {link.text: link for link in document.links(body, "doctrines/justification")}
    assert found["faith"].concept == "doctrines/faith" and found["faith"].fragment == "definition"
    assert found["grace"].concept == "doctrines/grace"
    assert found["Paul"].concept == "people/paul"
    assert found["web"].external and found["web"].concept is None
    assert "img" not in found and "not a link" not in found


def test_footnotes_and_wikilinks():
    body = "A claim.[^henry] Another.[^kjv]\n\n[^henry]: Matthew Henry\n[^kjv]: KJV\n[[old link]]"
    assert document.footnote_refs(body) == ["henry", "kjv"]
    assert document.footnote_defs(body) == ["henry", "kjv"]
    assert document.wikilinks(body) == ["[[old link]]"]


# ----------------------------------------------------------------------------- models


def test_minimal_concept_is_just_a_type():
    meta = OkfFrontmatter.model_validate({"type": "Anything At All"})
    assert meta.status == Status.STABLE  # absent => stable
    assert meta.trust_tier == TrustTier.UNVERIFIED
    assert meta.sources == [] and meta.tags == []


def test_type_must_be_non_empty():
    for bad in ({}, {"type": ""}, {"type": "   "}, {"type": None}):
        with pytest.raises(ValueError):
            OkfFrontmatter.model_validate(bad)


def test_unknown_keys_are_kept():
    meta = OkfFrontmatter.model_validate({"type": "Doctrine", "title_fr": "Grâce", "x": 1})
    assert meta.extra == {"title_fr": "Grâce", "x": 1}


def test_bare_verified_mapping_and_trust_tiers():
    at = "2026-10-25T09:00:00Z"
    process = OkfFrontmatter.model_validate(
        {"type": "T", "verified": {"by": "process:ingest", "at": at}}
    )
    assert len(process.verified) == 1 and process.trust_tier == TrustTier.MACHINE_CONFIRMED
    human = OkfFrontmatter.model_validate(
        {"type": "T", "verified": [{"by": "process:x", "at": at}, {"by": "human:dh", "at": at}]}
    )
    assert human.trust_tier == TrustTier.HUMAN_REVIEWED
    assert human.last_verified == datetime(2026, 10, 25, 9, tzinfo=UTC)


def test_lifecycle_and_staleness():
    meta = OkfFrontmatter.model_validate(
        {"type": "T", "status": "archived", "stale_after": "2026-01-01T00:00:00Z"}
    )
    assert meta.lifecycle is None
    assert meta.is_stale(datetime(2026, 6, 1, tzinfo=UTC))
    assert not meta.is_stale(datetime(2025, 6, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    "actor,kind",
    [("human:dh", "human"), ("process:ingest", "process"), ("corpusstudy/drafter-1", "agent"),
     ("someone", "unknown")],
)  # fmt: skip
def test_actor_kind(actor, kind):
    assert actor_kind(actor) == kind


# ----------------------------------------------------------------------------- bundles

NOW = "2026-10-23T10:00:00Z"
LATER = "2026-10-25T09:00:00Z"


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def doctrine(**overrides) -> str:
    meta = {
        "type": "Doctrine",
        "title": "Justification by Faith",
        "description": "God declares sinners righteous through faith.",
        "tags": ["soteriology"],
        "status": "draft",
        "generated": {"by": "corpusstudy/drafter-1", "at": NOW},
        "sources": [
            {
                "id": "henry-rom3",
                "resource": "/references/sources/henry-concise.md#Rom.3.21-Rom.3.26",
            },
            {"id": "kjv-rom5", "resource": "/references/sources/kjv.md#Rom.5.1"},
        ],
        "title_fr": "La justification par la foi",
        "key_passages": ["Rom 3:21-26", "Rom 5:1"],
        "tradition": "reformed",
    }
    body = overrides.pop("body", None)
    meta.update(overrides)
    meta = {k: v for k, v in meta.items() if v is not None}
    body = body or (
        "# Definition\nA legal declaration.[^henry-rom3] Peace with God.[^kjv-rom5]\n"
        "See [faith](/doctrines/faith.md).\n\n"
        "[^henry-rom3]: Matthew Henry on Romans 3\n[^kjv-rom5]: Romans 5:1\n"
    )
    return document.render(meta, body)


@pytest.fixture
def bundle_root(tmp_path, monkeypatch):
    # A fake commentary store so anchors resolve without the built corpus.
    excerpts = {"Rom.3.21-Rom.3.26"}
    fake = SimpleNamespace(get=lambda local: object() if local in excerpts else None)
    monkeypatch.setattr("corpusstudy.okf.profile.commentary", lambda source: fake)
    root = tmp_path / "knowledge"
    write(root, "index.md", '---\nokf_version: "0.2"\n---\n\n# Root\n')
    write(root, "log.md", "# Log\n\n## 2026-10-23\n* **Creation**: test bundle\n")
    write(root, "doctrines/justification.md", doctrine())
    return root


def profile_errors(root: Path) -> list[str]:
    bundle = load_bundle(root)
    return [i.message for i in bundle.issues + check_bundle(bundle) if i.level == "error"]


def test_valid_bundle(bundle_root):
    bundle = load_bundle(bundle_root)
    assert bundle.conformant and bundle.okf_version == "0.2"
    assert list(bundle.concepts) == ["doctrines/justification"]
    assert profile_errors(bundle_root) == []


def test_conformance_errors(bundle_root):
    write(bundle_root, "themes/no-frontmatter.md", "# Grace\n")
    write(bundle_root, "themes/empty-type.md", "---\ntype: ''\n---\nx")
    write(bundle_root, "themes/index.md", "---\ntitle: nope\n---\n# Themes\n")
    write(bundle_root, "themes/log.md", "# Log\n\n## yesterday\n* x\n")
    messages = [str(i) for i in load_bundle(bundle_root).errors]
    assert any("no-frontmatter.md: concept has no YAML frontmatter" in m for m in messages)
    assert any("empty-type.md: frontmatter has no non-empty `type`" in m for m in messages)
    assert any("themes/index.md" in m and "okf_version" in m for m in messages)
    assert any("themes/log.md" in m and "YYYY-MM-DD" in m for m in messages)


def test_tolerant_loading(bundle_root):
    # Unknown type, unknown keys, an invalid optional field and a broken link all load.
    write(
        bundle_root,
        "misc/odd.md",
        "---\ntype: Mystery\nverified: 42\nzzz: 1\n---\n[x](/nowhere.md)",
    )
    bundle = load_bundle(bundle_root)
    assert bundle.conformant
    odd = bundle.concepts["misc/odd"]
    assert odd.meta.type == "Mystery" and odd.raw["zzz"] == 1 and odd.meta.verified == []
    assert any("ignored invalid `verified`" in str(i) for i in bundle.issues)
    assert ("misc/odd", odd.links[0]) in bundle.broken_links()


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"type": "Dogma"}, "is not one of"),
        ({"type": "Person"}, "belongs in people/"),
        ({"description": None}, "missing `description`"),
        ({"generated": None}, "missing `generated"),
        ({"generated": {"by": "claude", "at": NOW}}, "is not <producer>/<version>"),
        ({"status": "stable"}, "needs a human verification"),
        ({"status": "stable", "verified": [{"by": "human:dh", "at": "2026-10-20T00:00:00Z"}]},
         "content changed after the last review"),
        ({"title_fr": None}, "missing `title_fr`"),
        ({"key_passages": ["Rom 30:1"]}, "key passage"),
        ({"key_passages": ["Ps 151:1"]}, "key passage"),
        ({"tradition": "pelagian"}, "tradition"),
        ({"sources": [{"resource": "/references/sources/kjv.md#Rom.5.1"}]}, "needs an `id`"),
        ({"sources": [{"id": "w", "resource": "https://en.wikipedia.org/wiki/Grace"}]},
         "public-domain sources only"),
        ({"sources": [{"id": "kjv-rom5", "resource": "/references/sources/kjv.md#Rom.17.1"}]},
         "chapters"),
        ({"sources": [{"id": "henry-rom3", "resource": "/references/sources/henry-concise.md#Rom.99"}]},
         "no excerpt"),
        ({"body": "Claim.[^nobody]\n\n[^nobody]: ?\n"}, "has no matching `sources` id"),
        ({"body": "Old style [[faith]].[^henry-rom3][^kjv-rom5]\n\n[^henry-rom3]: a\n[^kjv-rom5]: b\n"},
         "wikilinks"),
    ],
)  # fmt: skip
def test_profile_violations(bundle_root, overrides, expected):
    write(bundle_root, "doctrines/justification.md", doctrine(**overrides))
    errors = profile_errors(bundle_root)
    assert any(expected in e for e in errors), errors


def test_reviewed_concept_passes(bundle_root):
    write(bundle_root, "doctrines/justification.md",
          doctrine(status="stable", verified=[{"by": "human:dh", "at": LATER}]))  # fmt: skip
    assert profile_errors(bundle_root) == []
    concept = load_bundle(bundle_root).concepts["doctrines/justification"]
    assert concept.meta.trust_tier == TrustTier.HUMAN_REVIEWED


def test_bundle_level_rules(bundle_root):
    (bundle_root / "log.md").unlink()
    write(bundle_root, "index.md", "# Root without version\n")
    errors = profile_errors(bundle_root)
    assert any("okf_version" in e for e in errors) and any("log.md" in e for e in errors)


# ----------------------------------------------------------------------------- indexes, log


def test_indexes(bundle_root):
    (bundle_root / "people").mkdir()
    write_indexes(bundle_root)
    root = (bundle_root / "index.md").read_text(encoding="utf-8")
    assert root.startswith('---\nokf_version: "0.2"\n---\n')
    assert "* [Doctrines](doctrines/) - Doctrines, framed from the Reformed view" in root
    doctrines = (bundle_root / "doctrines" / "index.md").read_text(encoding="utf-8")
    assert "* [Justification by Faith](justification.md) - God declares sinners" in doctrines
    assert "_No concepts yet._" in (bundle_root / "people" / "index.md").read_text()
    assert write_indexes(bundle_root) == []  # idempotent
    assert load_bundle(bundle_root).conformant
    assert render_index(load_bundle(bundle_root), bundle_root) == root


def test_log_entries_newest_first(bundle_root):
    add_log_entry(bundle_root, "Update", "second", on=date(2026, 10, 24))
    add_log_entry(bundle_root, "Update", "third", on=date(2026, 10, 24))
    add_log_entry(bundle_root, "Update", "third", on=date(2026, 10, 24))  # no duplicate
    text = (bundle_root / "log.md").read_text(encoding="utf-8")
    assert text.index("## 2026-10-24") < text.index("## 2026-10-23")
    assert text.index("third") < text.index("second") and text.count("third") == 1
    assert load_bundle(bundle_root).conformant


# ----------------------------------------------------------------------------- references

MANIFEST = {
    "generated": "2026-10-22T19:54:06+00:00",
    "sources": {
        "KJV": {"description": "King James Version", "license": "Public domain", "files": {"a": {}},
                "verses": 31102, "superscriptions": 116, "repairs": []},
    },
    "commentaries": {
        "henry-concise": {"excerpts": 5164, "with_refs": 5164, "sha256": "ab" * 32,
                          "corrected_refs": ["Eccl.5.9-Eccl.5.7 -> Eccl.5.9-Eccl.5.17"]},
    },
}  # fmt: skip


def test_reference_concepts_pass_the_profile(bundle_root):
    manifest = bundle_root.parent / "manifest.json"
    manifest.write_text(json.dumps(MANIFEST), encoding="utf-8")
    written = write_reference_concepts(bundle_root, manifest)
    assert written == ["references/sources/kjv.md", "references/sources/henry-concise.md"]
    assert profile_errors(bundle_root) == []
    kjv = load_bundle(bundle_root).concepts["references/sources/kjv"]
    assert kjv.meta.type == "Reference" and kjv.meta.trust_tier == TrustTier.MACHINE_CONFIRMED
    assert kjv.raw["verses"] == 31102
    # A newer manifest with the same content does not rewrite the files.
    manifest.write_text(json.dumps(MANIFEST | {"generated": "2026-11-01T00:00:00+00:00"}))
    assert write_reference_concepts(bundle_root, manifest) == []


def test_reference_concept_ids():
    paths = [rel for rel, _, _ in reference_concepts(MANIFEST)]
    assert paths == ["references/sources/kjv.md", "references/sources/henry-concise.md"]


# ----------------------------------------------------------------------------- the real bundle


def test_committed_bundle_is_conformant_and_passes_the_profile():
    root = get_settings().knowledge_dir
    bundle = load_bundle(root)
    assert bundle.okf_version == "0.2"
    assert bundle.conformant, bundle.errors
    errors = [i for i in check_bundle(bundle) if i.level == "error"]
    assert errors == [], errors
    assert {"references/sources/kjv", "references/sources/lsg1910"} <= set(bundle.concepts)
    for directory in ("", "books", "themes", "people", "doctrines", "events", "references"):
        assert (root / directory / "index.md").exists()
    assert not (root / "README.md").exists()  # would be a concept without frontmatter


@pytest.mark.skipif(
    not (get_settings().data_dir / "processed" / "manifest.json").exists(),
    reason="corpus not built",
)
def test_documented_anchor_examples_resolve_against_the_corpus():
    """Every `#anchor` example in the source references and docs must exist in the data."""
    import re

    from corpusstudy.commentary import commentary
    from corpusstudy.okf.references import reference_concepts

    manifest = json.loads((get_settings().data_dir / "processed" / "manifest.json").read_text())
    texts = [body + str(meta) for _, meta, body in reference_concepts(manifest)]
    texts.append((get_settings().knowledge_dir.parent / "docs" / "KNOWLEDGE_FORMAT.md").read_text())
    found = set()
    for text in texts:
        for source, anchor in re.findall(r"/references/sources/([\w-]+)\.md#([\w.\-]+)", text):
            found.add((source, anchor))
    assert found
    for source, anchor in found:
        if source in ("kjv", "lsg1910"):
            from corpusstudy.schemas import PassageRef

            PassageRef.from_osis(anchor)
        else:
            assert commentary(source).get(anchor) is not None, f"{source}#{anchor}"
