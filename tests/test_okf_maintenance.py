"""Phase 9: knowledge maintenance: the report, the gap log and concept proposals."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from conftest import copy_bundle_skeleton
from test_generation import FakeClient, FakeSearch, reply
from test_okf_lifecycle import fill

from corpusstudy.config import Language, Settings, Translation
from corpusstudy.generation import Answerer
from corpusstudy.generation.llm import LLM
from corpusstudy.indexing.store import index_path, read_info
from corpusstudy.okf import document, lifecycle, maintenance, propose, quotes
from corpusstudy.okf.__main__ import main
from corpusstudy.okf.bundle import load_bundle
from corpusstudy.okf.profile import check_bundle
from corpusstudy.schemas import (
    ChatRequest,
    Chunk,
    PassageRef,
    RetrievalResult,
    Retriever,
    Route,
    SourceType,
)

T0 = datetime(2026, 10, 1, 10, tzinfo=UTC)
T1 = datetime(2026, 10, 10, 10, tzinfo=UTC)
NOW = datetime(2026, 10, 23, 10, tzinfo=UTC)
DRAFTER = "corpusstudy/drafter-1"
MATT6 = ("And when thou prayest, thou shalt not be as the hypocrites are. But thou, when thou "
         "prayest, enter into thy closet, and when thou hast shut thy door, pray to thy Father "
         "which is in secret")  # fmt: skip
HENRY1 = "It is taken for granted that all who are disciples of Christ pray."
HENRY2 = "If prayerless, then graceless."
TEXTS = {
    "/references/sources/kjv.md#Matt.6.5-Matt.6.7": MATT6,
    "/references/sources/henry-concise.md#Matt.6.5-Matt.6.8": f"{HENRY1} {HENRY2}",
    "/references/sources/henry-concise.md#Rom.3.21-Rom.3.26": "Free grace.",
    "/references/sources/kjv.md#Rom.5.1": "Therefore being justified by faith",
}


@pytest.fixture
def root(tmp_path, monkeypatch):
    """The committed bundle's skeleton, with commentary anchors and quotations resolved
    from TEXTS, so no built corpus is needed."""
    excerpts = {r.rsplit("#", 1)[1] for r in TEXTS if "henry" in r}
    fake = SimpleNamespace(get=lambda local: object() if local in excerpts else None)
    monkeypatch.setattr("corpusstudy.okf.profile.commentary", lambda source: fake)
    real = quotes.check_quotes
    monkeypatch.setattr(quotes, "check_quotes",
                        lambda c: real(c, resolve=lambda res, cid: TEXTS.get(res)))  # fmt: skip
    return copy_bundle_skeleton(tmp_path / "knowledge")


def concept(root, type_, slug, title, links=()):
    lifecycle.new_concept(root, type_, slug, title, by=DRAFTER, now=T0)
    cid = f"{lifecycle.TYPES[type_]}/{slug}"
    fill(root, cid, links)
    return cid


def set_meta(root, cid, **changes):
    path = root / f"{cid}.md"
    meta, body = document.split(path.read_text())
    meta.update(changes)
    path.write_text(document.render(meta, body))


# ----------------------------------------------------------------------------- report


def test_report_lists_what_needs_attention(root):
    election = concept(root, "Doctrine", "election", "Election", links=("doctrines/old",))
    grace = concept(root, "Theme", "grace", "Grace", links=("themes/nowhere",))
    old = concept(root, "Doctrine", "old", "Old view")
    lone = concept(root, "Person", "abraham", "Abraham")
    romans = concept(root, "Bible Book", "romans", "Romans")
    lifecycle.verify(root, grace, by="human:tester", now=T0)
    lifecycle.verify(root, romans, by="human:tester", now=T0)
    lifecycle.touch(root, romans, by=DRAFTER, now=T1)  # edited after review
    lifecycle.deprecate(root, old, "merged into election")
    set_meta(root, grace, stale_after=datetime(2026, 11, 5, tzinfo=UTC))  # due within 30 days
    set_meta(root, lone, stale_after=datetime(2026, 10, 1, tzinfo=UTC))  # stale
    # The Henry source was regenerated after the review of Grace, the KJV before it.
    for source, at in (("henry-concise", T1), ("kjv", datetime(2026, 9, 1, tzinfo=UTC))):
        set_meta(root, f"references/sources/{source}", generated={"by": "process:ingest", "at": at})

    r = maintenance.build_report(load_bundle(root), now=NOW)
    assert r.errors == [] and r.concepts == 5
    assert {i.concept for i in r.review_queue} == {election, lone, romans}
    assert r.review_queue[0].concept == romans and r.review_queue[0].changed_after_review
    assert [c for c, _ in r.stale] == [lone] and [c for c, _ in r.due] == [grace]
    assert r.source_updates == [(grace, "references/sources/henry-concise")]
    assert r.broken == [(grace, "/themes/nowhere.md")]
    assert r.deprecated_links == [(election, old)]
    assert lone in r.orphans
    assert "Romans" not in r.missing_books and len(r.missing_books) == 65
    assert r.coverage is None and "not built" in r.note

    text = maintenance.render(r)
    for expected in ("## Review queue", "edited since your review", f"`{lone}` is stale",
                     "regenerated after your review", "to a deprecated concept",
                     "Question coverage not computed"):  # fmt: skip
        assert expected in text
    data = maintenance.as_json(r)
    assert data["coverage"] is None and data["stale"] == [lone]
    json.dumps(data)


class CoverageSearch:
    """Covers questions by the concepts their words name; 'weak' only via keywords."""

    def __init__(self, bundle):
        self.bundle = bundle

    def named_concepts(self, text, language=None):
        return [cid for cid in self.bundle.concepts if cid.split("/")[-1] in text.lower()]

    def search(self, text, language=None, k=10, **kw):
        chunk = Chunk(id="themes/grace#overview", source_type=SourceType.KNOWLEDGE, text="x",
                      language=Language.EN, concept_id="themes/grace")  # fmt: skip
        if "grace" in text.lower():
            via = ["phrase:knowledge"]
        elif "weak" in text:
            via = ["keyword:knowledge"]
        else:
            return []
        return [RetrievalResult(chunk=chunk, score=1, retriever=Retriever.KEYWORD, rank=1, via=via)]


def test_coverage_finds_gaps_and_ranks_the_review_queue(root, monkeypatch):
    election = concept(root, "Doctrine", "election", "Election")
    grace = concept(root, "Theme", "grace", "Grace")
    lifecycle.verify(root, grace, by="human:tester", now=T0)

    def fake_route(text, language, search):
        advice = "medical" if "doctor" in text else None
        kind = Route.LOOKUP if text.startswith("John") else Route.RAG
        return SimpleNamespace(route=kind, advice=advice, concern=None, invalid_refs=[])

    monkeypatch.setattr("corpusstudy.router.route", fake_route)
    bundle = load_bundle(root)
    questions = [maintenance.Question(q, Language.EN, "test") for q in (
        "What is election?", "weak question about prayer", "Grace and election",
        "John 3:16", "Should I see a doctor?")]  # fmt: skip
    questions[1].count = 3
    r = maintenance.build_report(bundle, search=CoverageSearch(bundle), questions=questions,
                                 now=NOW)  # fmt: skip
    status = {c.question.text: c.status for c in r.coverage}
    assert status == {"What is election?": "drafts only",
                      "weak question about prayer": "gap",  # loose keywords do not count
                      "Grace and election": "covered"}  # fmt: skip
    assert [g.question.text for g in r.gaps] == ["weak question about prayer"]
    assert [(i.concept, i.answers) for i in r.review_queue] == [(election, 1)]
    text = maintenance.render(r)
    assert "weak question about prayer [en, rag, test] (asked 3 times)" in text
    assert "okf propose" in text


# ----------------------------------------------------------------------------- gap log


def test_gap_log_and_questions(tmp_path):
    log = tmp_path / "data" / "gaps.jsonl"
    maintenance.record_gap(log, "How should I pray?", Language.EN, "rag", now=NOW)
    maintenance.record_gap(log, "how should I  pray?", Language.EN, "rag", now=NOW)
    maintenance.record_gap(log, "Comment prier ?", Language.FR, "rag", now=NOW)
    row = json.loads(log.read_text().splitlines()[0])
    assert row == {"at": "2026-10-23T10:00:00+00:00", "q": "How should I pray?", "lang": "en",
                   "route": "rag"}  # fmt: skip
    evalset = tmp_path / "set.yaml"
    evalset.write_text(
        '- {q: "How should I pray?", lang: en}\n- {q: "Qui est Moïse ?", lang: fr}\n'
    )
    qs = maintenance.load_questions([log, evalset, tmp_path / "missing.jsonl"])
    assert [(q.text, q.language, q.origin, q.count) for q in qs] == [
        ("How should I pray?", Language.EN, "gap log", 3),
        ("Comment prier ?", Language.FR, "gap log", 1),
        ("Qui est Moïse ?", Language.FR, "set.yaml", 1),
    ]


def gap_answerer(monkeypatch, tmp_path, gap_log, trust="human-reviewed"):
    settings = Settings(_env_file=None, gap_log=gap_log)
    monkeypatch.setattr("corpusstudy.generation.answer.get_settings", lambda: settings)
    text = 'Henry adds that "Christ bought it, and paid the price" [E1]. Note [E2]'
    a = Answerer(FakeSearch(), LLM(FakeClient(reply(text))))
    a._concept_errors["doctrines/justification"] = []
    import test_generation

    real = test_generation.evidence

    def evidence(language=Language.EN):
        out = real(language)
        knowledge = out[-1]
        meta = {**knowledge.chunk.metadata, "trust_tier": trust}
        return [*out[:-1], knowledge.__class__(knowledge.key, knowledge.chunk.model_copy(
            update={"metadata": meta}), knowledge.label)]  # fmt: skip

    monkeypatch.setattr(test_generation, "evidence", evidence)
    return a


def test_the_answerer_logs_gaps_only_when_asked(monkeypatch, tmp_path):
    log = tmp_path / "gaps.jsonl"
    a = gap_answerer(monkeypatch, tmp_path, None)
    a.answer(ChatRequest(message="How should I pray?"))
    assert not log.exists()  # off by default: questions are not written down

    a = gap_answerer(monkeypatch, tmp_path, log)  # a reviewed concept answered it
    a.answer(ChatRequest(message="How should I pray?"))
    assert not log.exists()

    a = gap_answerer(monkeypatch, tmp_path, log, trust="unverified")
    a.answer(ChatRequest(message="How should I pray?"))
    a.answer(ChatRequest(message="John 3:16"))  # a lookup needs no concept
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(r["q"], r["route"]) for r in rows] == [("How should I pray?", "rag")]


def test_gap_log_setting(monkeypatch):
    monkeypatch.delenv("CORPUS_GAP_LOG", raising=False)
    assert Settings(_env_file=None).gap_log is None
    s = Settings(_env_file=None, gap_log="data/gaps.jsonl")
    assert s.gap_log.is_absolute() and s.gap_log.name == "gaps.jsonl"
    assert Settings(_env_file=None, gap_log="").gap_log is None


# ----------------------------------------------------------------------------- proposals


def result(c):
    return RetrievalResult(chunk=c, score=1.0, retriever=Retriever.KEYWORD, rank=1)


class EvidenceSearch:
    def __init__(self):
        self.queries = []

    def search(self, text, language=None, k=10, kinds=()):
        self.queries.append((text, language, kinds))
        matt = PassageRef.parse("Matt 6:5-7")
        henry = {"source": "henry-concise", "excerpt_id": "henry-concise:Matt.6.5-Matt.6.8"}
        chunks = [
            Chunk(id="KJV:Matt.6.5-Matt.6.7", source_type=SourceType.SCRIPTURE,
                  translation=Translation.KJV, language=Language.EN, refs=[matt],
                  text="[5] And when thou prayest, thou shalt not be as the hypocrites are."),
            Chunk(id="henry-concise:Matt.6.5-Matt.6.8#1", source_type=SourceType.COMMENTARY,
                  work="henry", language=Language.EN, refs=[PassageRef.parse("Matt 6:5-8")],
                  text=HENRY1, metadata=henry),
            Chunk(id="henry-concise:Matt.6.5-Matt.6.8#2", source_type=SourceType.COMMENTARY,
                  work="henry", language=Language.EN, refs=[PassageRef.parse("Matt 6:5-8")],
                  text=HENRY2, metadata=henry),
            Chunk(id="LSG1910:Matt.6.5-Matt.6.7", source_type=SourceType.SCRIPTURE,
                  translation=Translation.LSG1910, language=Language.FR, refs=[matt],
                  text="[5] Lorsque vous priez"),
            Chunk(id="themes/grace#overview", source_type=SourceType.KNOWLEDGE, text="x",
                  language=Language.EN, concept_id="themes/grace"),
        ]  # fmt: skip
        return [result(c) for c in chunks]


def draft(**changes):
    data = {
        "description": "Calling on God as Father, as Christ taught.",
        "tags": ["prayer", "worship"],
        "title_fr": "La prière",
        "aliases": ["the life of prayer"],
        "aliases_fr": ["la vie de prière"],
        "key_passages": ["Matt 6:5-15"],
        "sections": [
            {"heading": "Overview", "text": 'Jesus taught, "when thou prayest, enter into thy '
             'closet".[^kjv-matt6-5] See [grace](/themes/grace.md).'},
            {"heading": "Development through Scripture", "text": "Christ assumes his "
             "disciples pray.[^henry-matt6-5]"},
            {"heading": "Reformed view", "text": f'Henry: "{HENRY2}"[^henry-matt6-5]\n\n'
             "[^henry-matt6-5]: written by the model"},
            {"heading": "Key passages", "text": "Matthew 6:5-15."},
        ],
    }  # fmt: skip
    data.update(changes)
    return reply(data=data)


def test_gather_merges_excerpt_parts_and_keeps_public_domain_sources():
    search = EvidenceSearch()
    sources = propose.gather(search, "Prayer", ["Comment prier ?"], ["Matt 6:5-15"])
    assert [(s.id, s.resource) for s in sources] == [
        ("kjv-matt6-5", "/references/sources/kjv.md#Matt.6.5-Matt.6.7"),
        ("henry-matt6-5", "/references/sources/henry-concise.md#Matt.6.5-Matt.6.8"),
    ]
    assert sources[0].text == "(5) And when thou prayest, thou shalt not be as the hypocrites are."
    assert sources[1].text == f"{HENRY1} {HENRY2}" and sources[0].title == "Matthew 6:5-7 (KJV)"
    assert [(q, lang) for q, lang, _ in search.queries] == [
        ("Matt 6:5-15", Language.EN),
        ("Prayer", Language.EN),
        ("Comment prier ?", Language.FR),
    ]
    assert all(kinds == ("bible", "commentary") for *_, kinds in search.queries)


def test_propose_writes_a_checked_draft_after_one_repair(root):
    bad = draft(aliases=["King James Version (1769)"], sections=[
        {"heading": "Overview", "text": 'Jesus said "pray without ceasing".[^kjv-matt6-5]'},
        {"heading": "Reformed view", "text": "Grace.[^calvin-made-up]"}])  # fmt: skip
    client = FakeClient(bad, draft())
    proposal = propose.propose(root, "Theme", "prayer", "Prayer", questions=["How should I pray?"],
                               search=EvidenceSearch(), llm=LLM(client), now=NOW)  # fmt: skip
    assert proposal.concept_id == "themes/prayer" and proposal.repaired
    assert proposal.sources == ["kjv-matt6-5", "henry-matt6-5"]
    assert proposal.usage.calls == 2

    repair = client.calls[1]["messages"][-1]["content"]
    for problem in (
        "pray without ceasing",
        "[^calvin-made-up] is not one of the source ids",
        "'Key passages' is missing",
        "already used by references/sources/kjv",
    ):
        assert problem in repair
    first = client.calls[0]
    assert first["system"][0]["text"] == propose.DRAFT_SYSTEM
    assert "How should I pray?" in first["messages"][0]["content"]
    assert (
        '<source id="kjv-matt6-5" title="Matthew 6:5-7 (KJV)">' in first["messages"][0]["content"]
    )

    c = load_bundle(root).concepts["themes/prayer"]
    assert c.meta.status == "draft" and c.meta.verified == []  # never published by itself
    assert c.meta.generated.by == DRAFTER and c.meta.generated.at == NOW
    assert [s.resource for s in c.meta.sources] == [
        "/references/sources/kjv.md#Matt.6.5-Matt.6.7",
        "/references/sources/henry-concise.md#Matt.6.5-Matt.6.8"]  # fmt: skip
    assert c.meta.extra["tradition"] == "reformed" and c.meta.extra["title_fr"] == "La prière"
    assert "written by the model" not in c.body
    assert "[^henry-matt6-5]: Matthew Henry, Concise Commentary, on Matthew 6:5-8" in c.body
    assert [i for i in check_bundle(load_bundle(root)) if i.level == "error"] == []
    assert "Proposed [Prayer](/themes/prayer.md) for review" in (root / "log.md").read_text()


def test_propose_refuses_a_draft_that_fails_twice(root):
    bad = draft(sections=[{"heading": "Overview", "text": 'He said "pray always".[^kjv-matt6-5]'}])
    with pytest.raises(propose.ProposalError, match="failed the checks twice") as exc:
        propose.propose(root, "Theme", "prayer", "Prayer", questions=[], search=EvidenceSearch(),
                        llm=LLM(FakeClient(bad, bad)), now=NOW)  # fmt: skip
    assert any("pray always" in i.message for i in exc.value.issues)
    assert not (root / "themes" / "prayer.md").exists()
    assert "Prayer" not in (root / "log.md").read_text()


def test_propose_guards(root):
    llm = LLM(FakeClient())  # never called
    args = dict(questions=[], search=EvidenceSearch(), llm=llm)
    with pytest.raises(propose.ProposalError, match="type must be"):
        propose.propose(root, "Sermon", "x", "X", **args)
    with pytest.raises(propose.ProposalError, match="lowercase words"):
        propose.propose(root, "Theme", "Prayer Life", "X", **args)
    concept(root, "Theme", "grace", "Grace")
    with pytest.raises(propose.ProposalError, match="already exists"):
        propose.propose(root, "Theme", "grace", "Grace", **args)
    empty = SimpleNamespace(search=lambda *a, **k: [])
    with pytest.raises(propose.ProposalError, match="no Scripture or commentary"):
        propose.propose(root, "Theme", "fasting", "Fasting", **{**args, "search": empty})
    with pytest.raises(propose.ProposalError, match="no draft"):
        propose.propose(
            root,
            "Theme",
            "fasting",
            "Fasting",
            **{**args, "llm": LLM(FakeClient(reply(stop="refusal", raw="")))},
        )


# ----------------------------------------------------------------------------- CLI


def test_cli_maintain(root, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("corpusstudy.indexing.build.index_status", lambda path=None: "missing")
    concept(root, "Theme", "grace", "Grace")
    b = ["--bundle", str(root)]
    assert main([*b, "maintain"]) == 0
    out = capsys.readouterr().out
    assert "# Knowledge maintenance report" in out and "`themes/grace` (Grace)" in out
    report = tmp_path / "report.json"
    assert main([*b, "maintain", "--json", "--out", str(report)]) == 0
    assert json.loads(report.read_text())["review_queue"][0]["concept"] == "themes/grace"
    set_meta(root, "themes/grace", status="stable")  # stable without review: an error
    assert main([*b, "maintain"]) == 1


def test_cli_propose_needs_the_key(root, capsys, monkeypatch):
    def unavailable():
        from corpusstudy.generation.llm import LLMUnavailable

        raise LLMUnavailable("ANTHROPIC_API_KEY is not set")

    monkeypatch.setattr("corpusstudy.generation.llm.LLM", unavailable)
    assert main(["--bundle", str(root), "propose", "Theme", "prayer", "Prayer"]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


# ----------------------------------------------------------------------------- real bundle

needs_index = pytest.mark.skipif(read_info(index_path()) is None, reason="search index not built")


@needs_index
def test_the_committed_bundle_and_evaluation_sets():
    from corpusstudy.retrieval.search import Search

    search = Search(use_drafts=True)
    questions = maintenance.load_questions(list(maintenance.EVAL_SETS))
    r = maintenance.build_report(search.bundle, search=search, questions=questions)
    status = {c.question.text: c.status for c in r.coverage}
    assert status["What is justification?"] in ("covered", "drafts only")
    assert status["How should I pray?"] == "gap"
    assert "John 3:16" not in status and "I want to die" not in status  # no concept needed
    assert not any("medication" in q for q in status)  # advice
    assert r.errors == [] and r.broken == []
