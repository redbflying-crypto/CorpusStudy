"""Phase 3: concept lifecycle (new/touch/verify/deprecate/log), the concept graph,
trust-aware selection, the CLI, and the review settings."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from conftest import copy_bundle_skeleton
from pydantic import ValidationError

from corpusstudy.config import Settings
from corpusstudy.okf import document, lifecycle
from corpusstudy.okf.__main__ import main
from corpusstudy.okf.bundle import load_bundle
from corpusstudy.okf.graph import build_graph, select, usable
from corpusstudy.okf.models import Status, TrustTier
from corpusstudy.okf.profile import check_bundle

T0 = datetime(2026, 10, 23, 10, tzinfo=UTC)
T1 = datetime(2026, 10, 24, 10, tzinfo=UTC)
T2 = datetime(2026, 10, 25, 10, tzinfo=UTC)
REVIEWER = "human:dhnguyen"
DRAFTER = "corpusstudy/drafter-1"


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A copy of the committed bundle (source references included), with a fake
    commentary store so anchors resolve without the built corpus."""
    fake = SimpleNamespace(get=lambda local: object() if local == "Rom.3.21-Rom.3.26" else None)
    monkeypatch.setattr("corpusstudy.okf.profile.commentary", lambda source: fake)
    dest = tmp_path / "knowledge"
    copy_bundle_skeleton(dest)
    return dest


def fill(root, concept_id: str, links: tuple[str, ...] = ()) -> None:
    """Complete a drafted skeleton so it passes the profile."""
    path = root / f"{concept_id}.md"
    meta, body = document.split(path.read_text(encoding="utf-8"))
    meta.update(
        description=f"Test concept {concept_id}.",
        title_fr="Titre",
        key_passages=["Rom 3:21-26"],
        sources=[
            {
                "id": "henry-rom3",
                "resource": "/references/sources/henry-concise.md#Rom.3.21-Rom.3.26",
            },
            {"id": "kjv-rom5", "resource": "/references/sources/kjv.md#Rom.5.1"},
        ],
    )
    see = " ".join(f"See [{t}](/{t}.md)." for t in links)
    body = (
        body.replace("\n", f"\n\nClaim.[^henry-rom3] Peace.[^kjv-rom5] {see}\n", 1)
        + "\n[^henry-rom3]: Henry\n[^kjv-rom5]: KJV\n"
    )
    path.write_text(document.render(meta, body), encoding="utf-8")


def errors(root) -> list[str]:
    bundle = load_bundle(root)
    return [str(i) for i in bundle.issues + check_bundle(bundle) if i.level == "error"]


# ----------------------------------------------------------------------------- lifecycle


def test_new_concept_skeleton(root):
    path = lifecycle.new_concept(root, "Doctrine", "justification", "Justification by Faith",
                                 by=DRAFTER, now=T0)  # fmt: skip
    assert path == root / "doctrines" / "justification.md"
    c = load_bundle(root).concepts["doctrines/justification"]
    assert c.meta.type == "Doctrine" and c.meta.lifecycle == Status.DRAFT
    assert c.meta.generated.by == DRAFTER and c.meta.generated.at == T0
    assert [line for line in c.body.splitlines() if line.startswith("# ")] == [
        "# Definition", "# Reformed view", "# Where other traditions differ", "# Key passages"
    ]  # fmt: skip
    assert "Justification by Faith" in (root / "doctrines" / "index.md").read_text()
    assert "(draft)" in (root / "doctrines" / "index.md").read_text()
    assert (
        "Drafted [Justification by Faith](/doctrines/justification.md)"
        in (root / "log.md").read_text()
    )
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.new_concept(root, "Doctrine", "justification", "Again", by=DRAFTER)
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.new_concept(root, "Dogma", "x", "X", by=DRAFTER)
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.new_concept(root, "Theme", "x", "X", by="claude")  # not an OKF actor


def test_verify_refuses_unfinished_drafts_and_non_humans(root):
    lifecycle.new_concept(root, "Person", "paul", "Paul", by=DRAFTER, now=T0)
    with pytest.raises(lifecycle.LifecycleError) as exc:
        lifecycle.verify(root, "people/paul", by=REVIEWER, now=T1)
    messages = [i.message for i in exc.value.issues]
    assert any("missing `title_fr`" in m for m in messages)
    assert any("needs at least one source" in m for m in messages)
    fill(root, "people/paul")
    for actor in (DRAFTER, "process:ingest"):
        with pytest.raises(lifecycle.LifecycleError, match="only a person"):
            lifecycle.verify(root, "people/paul", by=actor, now=T1)
    assert load_bundle(root).concepts["people/paul"].meta.trust_tier == TrustTier.UNVERIFIED


def test_review_cycle(root):
    lifecycle.new_concept(root, "Doctrine", "grace", "Grace", by=DRAFTER, now=T0)
    fill(root, "doctrines/grace")
    lifecycle.verify(root, "doctrines/grace", by=REVIEWER, now=T1)
    c = load_bundle(root).concepts["doctrines/grace"]
    assert c.meta.lifecycle == Status.STABLE and c.meta.trust_tier == TrustTier.HUMAN_REVIEWED
    assert errors(root) == []
    assert usable(c)

    lifecycle.touch(root, "doctrines/grace", by=DRAFTER, now=T2)  # an edit after review
    c = load_bundle(root).concepts["doctrines/grace"]
    assert c.meta.lifecycle == Status.DRAFT and c.meta.generated.at == T2
    assert c.meta.trust_tier == TrustTier.HUMAN_REVIEWED  # the old review is history...
    assert not usable(c)  # ...but the edited content is not reviewed yet
    assert errors(root) == []

    lifecycle.verify(root, "doctrines/grace", by=REVIEWER, now=datetime(2026, 10, 26, tzinfo=UTC))
    c = load_bundle(root).concepts["doctrines/grace"]
    assert len(c.meta.verified) == 2 and usable(c)

    lifecycle.deprecate(root, "doctrines/grace", "merged into doctrines/salvation")
    c = load_bundle(root).concepts["doctrines/grace"]
    assert c.meta.lifecycle == Status.DEPRECATED and not usable(c)
    with pytest.raises(lifecycle.LifecycleError, match="deprecated"):
        lifecycle.verify(root, "doctrines/grace", by=REVIEWER)
    log = (root / "log.md").read_text()
    for kind in ("Creation", "Review", "Update", "Deprecation"):
        assert f"**{kind}**: " in log
    assert load_bundle(root).conformant


def test_stable_after_unreviewed_edit_is_an_error(root):
    lifecycle.new_concept(root, "Event", "exodus", "The Exodus", by=DRAFTER, now=T0)
    fill(root, "events/exodus")
    lifecycle.verify(root, "events/exodus", by=REVIEWER, now=T1)
    # Editing by hand without `touch` leaves status stable with a newer `generated`:
    path = root / "events" / "exodus.md"
    meta, body = document.split(path.read_text())
    meta["generated"] = {"by": DRAFTER, "at": T2}
    path.write_text(document.render(meta, body))
    assert any("content changed after the last review" in e for e in errors(root))


def test_unknown_concept(root):
    with pytest.raises(lifecycle.LifecycleError, match="no concept"):
        lifecycle.touch(root, "doctrines/nothing", by=DRAFTER)


# ----------------------------------------------------------------------------- graph


def test_graph_links_backlinks_orphans_and_citations(root):
    for slug, title in (("justification", "Justification"), ("faith", "Faith"), ("grace", "Grace")):
        lifecycle.new_concept(root, "Doctrine", slug, title, by=DRAFTER, now=T0)
    lifecycle.new_concept(root, "Person", "abraham", "Abraham", by=DRAFTER, now=T0)
    fill(root, "doctrines/justification", links=("doctrines/faith", "doctrines/missing"))
    fill(root, "doctrines/faith", links=("people/abraham",))
    fill(root, "doctrines/grace")
    fill(root, "people/abraham")
    g = build_graph(load_bundle(root))
    assert g.out["doctrines/justification"] == {"doctrines/faith"}
    assert g.into["doctrines/faith"] == {"doctrines/justification"}
    assert g.neighbours("doctrines/faith") == {"doctrines/justification", "people/abraham"}
    assert g.broken == [("doctrines/justification", "/doctrines/missing.md")]
    assert g.orphans() == ["doctrines/grace"]
    assert g.cites["doctrines/faith"] == {
        "references/sources/henry-concise",
        "references/sources/kjv",
    }
    assert g.expand(["doctrines/justification"], hops=1) == [
        "doctrines/justification", "doctrines/faith"]  # fmt: skip
    assert g.expand(["doctrines/justification"], hops=2) == [
        "doctrines/justification", "doctrines/faith", "people/abraham"]  # fmt: skip
    # A filter (e.g. reviewed-only) prunes the expansion.
    assert g.expand(["doctrines/justification"], hops=2, keep=lambda c: c != "doctrines/faith") == [
        "doctrines/justification"]  # fmt: skip
    assert g.expand(["doctrines/unknown"]) == []


def test_select_trust_rules(root):
    lifecycle.new_concept(root, "Theme", "covenant", "Covenant", by=DRAFTER, now=T0)
    fill(root, "themes/covenant")
    bundle = load_bundle(root)
    default = select(bundle)
    assert "themes/covenant" not in default  # unreviewed draft
    assert {"references/sources/kjv", "references/sources/henry-concise"} <= set(default)
    assert "themes/covenant" in select(bundle, use_drafts=True)
    lifecycle.verify(root, "themes/covenant", by=REVIEWER, now=T1)
    assert "themes/covenant" in select(load_bundle(root))
    # Staleness removes a concept even when reviewed.
    path = root / "themes" / "covenant.md"
    meta, body = document.split(path.read_text())
    meta["stale_after"] = datetime(2026, 11, 1, tzinfo=UTC)
    path.write_text(document.render(meta, body))
    c = load_bundle(root).concepts["themes/covenant"]
    assert usable(c, now=datetime(2026, 10, 30, tzinfo=UTC))
    assert not usable(c, now=datetime(2026, 11, 2, tzinfo=UTC))


# ----------------------------------------------------------------------------- CLI, settings


def test_cli_round_trip(root, capsys):
    b = ["--bundle", str(root)]
    assert main([*b, "new", "Doctrine", "election", "Election"]) == 0
    assert main([*b, "verify", "doctrines/election"]) == 1  # unfinished
    assert "not ready for review sign-off" in capsys.readouterr().err
    fill(root, "doctrines/election")
    assert main([*b, "verify", "doctrines/election", "--by", "human:tester"]) == 0
    assert "human-reviewed" in capsys.readouterr().out
    assert main([*b, "check"]) == 0
    assert main([*b, "graph"]) == 0
    assert "orphan: doctrines/election" in capsys.readouterr().out
    assert main([*b, "touch", "doctrines/election"]) == 0
    assert main([*b, "deprecate", "doctrines/election", "--reason", "test"]) == 0
    assert main([*b, "log", "Reorganised", "--kind", "Update"]) == 0
    assert main([*b, "index"]) == 0
    assert "**Update**: Reorganised" in (root / "log.md").read_text()
    assert main([*b, "check"]) == 0


def test_review_settings(monkeypatch):
    for var in ("CORPUS_REVIEWER", "CORPUS_USE_DRAFTS", "CORPUS_EMBEDDINGS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.reviewer == "human:dhnguyen" and s.use_drafts is False and s.embeddings == "none"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, reviewer="dhnguyen")  # must be a human: actor
    with pytest.raises(ValidationError):
        Settings(_env_file=None, embeddings="openai")
