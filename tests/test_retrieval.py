"""Phase 4: analysis, reference extraction, chunking, the index, OKF-aware search, and the
evaluation gates (the last ones run only when the real index is built)."""

from types import SimpleNamespace

import pytest
from conftest import copy_bundle_skeleton

from corpusstudy.config import Language, Translation
from corpusstudy.indexing.analysis import detect_language, query_terms, stems
from corpusstudy.indexing.chunking import _split, bible_chunks, commentary_chunks, knowledge_chunks
from corpusstudy.indexing.store import index_path, read_info, write_index
from corpusstudy.okf import document, lifecycle
from corpusstudy.okf.bundle import load_bundle
from corpusstudy.retrieval.references import extract_references, strip_references
from corpusstudy.schemas import Chunk, PassageRef, Retriever, SourceType, Verse, VerseRef

# ----------------------------------------------------------------------------- analysis


def test_kjv_archaisms_fold_onto_modern_stems():
    assert stems("he that believeth", Language.EN) == stems("he that believes", Language.EN)
    assert stems("thou knowest", Language.EN) == ["you", "know"]
    assert stems("he saith unto thee", Language.EN) == ["he", "say", "to", "you"]
    assert stems("Japheth and Nazareth", Language.EN)[0] == "japheth"  # names untouched


def test_french_elision_and_accents():
    assert stems("l’Éternel est mon berger", Language.FR) == ["eternel", "est", "mon", "berg"]
    assert query_terms("Que dit la Bible sur la justification par la foi ?", Language.FR) == [
        "justif", "foi"]  # fmt: skip


def test_query_terms_drop_stopwords():
    assert query_terms("What does the Bible say about the love of God?", Language.EN) == [
        "love", "god"]  # fmt: skip


@pytest.mark.parametrize(
    "text,lang",
    [("Que dit la Bible sur la grâce ?", "fr"), ("What is grace?", "en"), ("Psaume 23", "fr"),
     ("Jean 3.16", "fr"), ("John 3:16", "en"), ("Genèse 1", "fr"), ("Genesis 1", "en")],
)  # fmt: skip
def test_detect_language(text, lang):
    assert detect_language(text) == lang


# ----------------------------------------------------------------------------- references


@pytest.mark.parametrize(
    "text,osis",
    [
        ("What does Romans 9 say about election?", ["Rom.9"]),
        ("What does Isaiah 53 teach?", ["Isa.53"]),  # not "I saiah"
        ("In 1 John 4:8 God is love", ["1John.4.8"]),
        ("Lis 1 Corinthiens 13", ["1Cor.13"]),
        ("compare Gen 1-3 with John 1:1-3", ["Gen.1-Gen.3", "John.1.1-John.1.3"]),
        ("Que dit Jean 3.16 ?", ["John.3.16"]),
        ("Psaume 23 et Psaume 51.3", ["Ps.23", "Ps.51.3"]),
        ("Rom 8:28–9:2", ["Rom.8.28-Rom.9.2"]),
        ("Jean 3,16", ["John.3.16"]),
        ("Ps 1, 2", ["Ps.1"]),  # a comma with a space separates chapters
        ("the numbers 3 and 4", []),  # chapter-only needs a capital
        ("I am 25 years old", []),
    ],
)
def test_extract_references(text, osis):
    assert [r.osis for r in extract_references(text)] == osis


def test_strip_references():
    assert strip_references("What does Romans 9 say?").split() == ["What", "does", "say?"]


# ----------------------------------------------------------------------------- chunking


class FakeBible(list):
    def __init__(self, translation, verses):
        super().__init__(verses)
        self.translation = translation


def verses(tr, book, chapter, n, title=False):
    out = [Verse(ref=VerseRef(book=book, chapter=chapter, verse=0), translation=tr,
                 text="A Psalm of David.")] if title else []  # fmt: skip
    out += [Verse(ref=VerseRef(book=book, chapter=chapter, verse=v), translation=tr,
                  text=f"verse {v} text") for v in range(1, n + 1)]  # fmt: skip
    return out


def test_bible_windows_cover_every_verse():
    text = FakeBible(Translation.KJV, verses(Translation.KJV, "Ps", 3, 8, title=True)
                     + verses(Translation.KJV, "Ps", 117, 2))  # fmt: skip
    chunks = list(bible_chunks(text, size=3, stride=2))
    ids = [c.id for c in chunks]
    assert ids[0] == "KJV:Ps.3.0-Ps.3.3" and chunks[0].text.startswith("A Psalm of David. [1]")
    assert ids[-2] == "KJV:Ps.3.6-Ps.3.8"  # the last window reaches the chapter end
    assert ids[-1] == "KJV:Ps.117.1-Ps.117.2"  # a short chapter is one window
    covered = {(c.refs[0].start_chapter, v) for c in chunks
               for v in range(c.refs[0].start_verse or 1, c.refs[0].end_verse + 1)}  # fmt: skip
    assert {(3, v) for v in range(1, 9)} <= covered
    assert all(c.metadata["numbering"] == "KJV" for c in chunks)


def test_split_long_excerpts_on_sentences():
    text = "First sentence here. " * 40 + "\n\n" + "Second paragraph sentence. " * 40
    parts = _split(text.strip(), 300)
    assert len(parts) > 3 and all(len(p) <= 320 for p in parts)
    assert "".join(parts).replace(" ", "").replace("\n", "") == text.replace(" ", "").replace(
        "\n", "")  # fmt: skip
    assert _split("short", 300) == ["short"]


def test_commentary_chunks_keep_refs_and_ids():
    ex = SimpleNamespace(id="henry-concise:Luke.15.11-Luke.15.16", work="henry",
                         source="henry-concise", topic=("Luke", "Chapter 15"),
                         refs=("Luke.15.11-Luke.15.16",), mentions=("Hos.14.4",),
                         text="The prodigal son. " * 120)  # fmt: skip
    chunks = list(commentary_chunks([ex], max_chars=500))
    assert [c.id for c in chunks][:2] == [f"{ex.id}#1", f"{ex.id}#2"]
    assert all(c.refs[0].osis == "Luke.15.11-Luke.15.16" and c.work == "henry" for c in chunks)


# ----------------------------------------------------------------------------- a small world

DRAFTER, REVIEWER = "corpusstudy/drafter-1", "human:dhnguyen"


def make_concept(root, type_, slug, title, body, *, links=(), reviewed=False, fr=""):
    lifecycle.new_concept(root, type_, slug, title, by=DRAFTER)
    folder = lifecycle.TYPES[type_]
    path = root / folder / f"{slug}.md"
    meta, _ = document.split(path.read_text())
    meta.update(
        description=f"{title}.", title_fr=fr or f"{title} (fr)", key_passages=["Rom 5:1"],
        sources=[{"id": "kjv-rom5", "resource": "/references/sources/kjv.md#Rom.5.1"}],
    )  # fmt: skip
    see = " ".join(f"See [{t}](/{t}.md)." for t in links)
    path.write_text(document.render(meta, f"# Definition\n\n{body}[^kjv-rom5] {see}\n\n"
                                          "[^kjv-rom5]: Romans 5:1\n"))  # fmt: skip
    if reviewed:
        lifecycle.verify(root, f"{folder}/{slug}", by=REVIEWER)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A tiny index: a few verses, two commentaries, and an OKF bundle with one reviewed
    concept linked to a reviewed neighbour, plus an unreviewed draft."""
    root = tmp_path / "knowledge"
    copy_bundle_skeleton(root)
    make_concept(root, "Doctrine", "justification", "Justification",
                 "Sinners are declared righteous by faith.", links=("doctrines/faith",),
                 reviewed=True, fr="La justification")  # fmt: skip
    make_concept(root, "Doctrine", "faith", "Faith", "Trust in Christ.", reviewed=True)
    make_concept(root, "Doctrine", "sanctification", "Sanctification",
                 "Being made holy by faith and the Spirit.")  # draft, unreviewed  # fmt: skip

    kjv = FakeBible(Translation.KJV, [
        Verse(ref=VerseRef(book="Rom", chapter=5, verse=1), translation=Translation.KJV,
              text="Therefore being justified by faith, we have peace with God"),
        Verse(ref=VerseRef(book="Ps", chapter=51, verse=1), translation=Translation.KJV,
              text="Have mercy upon me, O God, according to thy lovingkindness"),
        Verse(ref=VerseRef(book="Gen", chapter=1, verse=1), translation=Translation.KJV,
              text="In the beginning God created the heaven and the earth."),
    ])  # fmt: skip
    lsg = FakeBible(Translation.LSG1910, [
        Verse(ref=VerseRef(book="Ps", chapter=51, verse=3), translation=Translation.LSG1910,
              text="O Dieu! Aie pitié de moi dans ta bonté"),
    ])  # fmt: skip
    notes = [
        SimpleNamespace(id="henry-concise:Ps.51.1-Ps.51.6", work="henry", source="henry-concise",
                        topic=("Psalms",), refs=("Ps.51.1-Ps.51.6",), mentions=(),
                        text="David pours out his soul in penitence."),
        SimpleNamespace(id="calvin-lcc:x-p1", work="calvin", source="calvin-lcc",
                        topic=("VI Providence",), refs=("Ps.51.1",), mentions=(),
                        text="Calvin on penitence and the mercy of God."),
        SimpleNamespace(id="henry-concise:Rom.5.1-Rom.5.5", work="henry", source="henry-concise",
                        topic=("Romans",), refs=("Rom.5.1-Rom.5.5",), mentions=(),
                        text="Peace with God is the fruit of being justified."),
    ]  # fmt: skip
    chunks = [*bible_chunks(kjv, size=1, stride=1), *bible_chunks(lsg, size=1, stride=1),
              *commentary_chunks(notes), *knowledge_chunks(load_bundle(root))]  # fmt: skip
    path = tmp_path / "index.sqlite"
    write_index(path, chunks, {"inputs": "test"})

    by_tr = {Translation.KJV: kjv, Translation.LSG1910: lsg}

    def fake_bible(tr):
        verses = list(by_tr[tr])
        return SimpleNamespace(passage=lambda p: [v for v in verses if p.contains(v.ref)])

    monkeypatch.setattr("corpusstudy.retrieval.search.bible", fake_bible)
    from corpusstudy.retrieval.search import Search

    return SimpleNamespace(root=root, path=path, Search=Search)


def ids(results):
    return [r.chunk.id for r in results]


def test_index_contents(world):
    info = read_info(world.path)
    assert info.counts == {"bible": 4, "commentary": 3, "knowledge": 3}


def test_phrase_and_prefix_matching(world):
    s = world.Search(world.path, knowledge_root=world.root)
    results = s.search("justification by faith", Language.EN)
    assert results[0].chunk.id in ("KJV:Rom.5.1", "doctrines/justification#definition")
    assert "KJV:Rom.5.1" in ids(results)  # "justification" reaches "justified"
    # The verse that matched pulls in commentary on it.
    hit = next(r for r in results if r.chunk.id == "henry-concise:Rom.5.1-Rom.5.5")
    assert any(v.startswith("hits:") for v in hit.via)


def test_okf_trust_rules_and_graph(world):
    reviewed_only = world.Search(world.path, knowledge_root=world.root, use_drafts=False)
    found = ids(reviewed_only.search("justification sanctification", Language.EN, k=20))
    assert "doctrines/justification#definition" in found
    assert "doctrines/sanctification#definition" not in found  # a draft
    assert "doctrines/faith#definition" in found  # reached through the OKF link graph
    faith = next(r for r in reviewed_only.search("justification", Language.EN, k=20)
                 if r.chunk.id == "doctrines/faith#definition")  # fmt: skip
    assert "graph:knowledge" in faith.via
    with_drafts = world.Search(world.path, knowledge_root=world.root, use_drafts=True)
    assert "doctrines/sanctification#definition" in ids(
        with_drafts.search("sanctification", Language.EN)
    )
    # Deprecating a concept removes it.
    lifecycle.deprecate(world.root, "doctrines/justification", "test")
    after = world.Search(world.path, knowledge_root=world.root)
    assert "doctrines/justification#definition" not in ids(after.search("justification", "en"))


def test_french_title_reaches_an_english_concept(world):
    s = world.Search(world.path, knowledge_root=world.root)
    assert "doctrines/justification#definition" in ids(s.search("la justification", Language.FR))


def test_naming_a_concept_brings_its_opening_section(world):
    s = world.Search(world.path, knowledge_root=world.root)
    faith = next(r for r in s.search("What is faith?", Language.EN)
                 if r.chunk.id == "doctrines/faith#definition")  # fmt: skip
    assert "concept:knowledge" in faith.via
    fr = s.search("Qu'est-ce que la justification ?", Language.FR)
    assert any("concept:knowledge" in r.via for r in fr)
    assert s._concepts(s.analyse("sanctification", Language.EN)) == []  # a draft
    drafts = world.Search(world.path, knowledge_root=world.root, use_drafts=True)
    assert drafts._concepts(drafts.analyse("sanctification", Language.EN)) == [
        "doctrines/sanctification#definition"]  # fmt: skip


def test_french_reference_maps_to_kjv_commentary(world):
    s = world.Search(world.path, knowledge_root=world.root)
    results = s.search("Psaume 51.3", Language.FR)
    assert results[0].chunk.id == "LSG1910:Ps.51.3" and results[0].retriever == Retriever.LOOKUP
    found = ids(results)
    # LSG Ps 51:3 is KJV Ps 51:1: commentary keyed in KJV numbering is found by reference.
    assert "henry-concise:Ps.51.1-Ps.51.6" in found and "calvin-lcc:x-p1" in found


def test_naming_a_commentator_filters_commentary(world):
    s = world.Search(world.path, knowledge_root=world.root)
    henry = s.search("Matthew Henry on Psalm 51:1", Language.EN)
    assert henry[0].chunk.source_type == SourceType.COMMENTARY  # asked for commentary first
    commentary = [r.chunk.work for r in henry if r.chunk.source_type == SourceType.COMMENTARY]
    assert commentary and set(commentary) == {"henry"}
    calvin = s.search("Calvin on Psalm 51:1", Language.EN)
    assert {r.chunk.work for r in calvin if r.chunk.work} == {"calvin"}
    assert "henri" not in s.analyse("Matthew Henry on the prodigal son").terms


def test_results_interleave_kinds(world):
    s = world.Search(world.path, knowledge_root=world.root)
    kinds = [r.chunk.source_type for r in s.search("justification by faith peace", "en", k=6)]
    assert len(set(kinds[:3])) == 3  # Scripture, commentary and knowledge side by side


def test_chunk_round_trip(world):
    s = world.Search(world.path, knowledge_root=world.root)
    chunk = s._chunk("doctrines/justification#definition")
    assert isinstance(chunk, Chunk) and chunk.concept_id == "doctrines/justification"
    assert chunk.metadata["trust_tier"] == "human-reviewed"
    assert chunk.refs == [PassageRef.parse("Rom 5:1")]


# ----------------------------------------------------------------------------- evaluation gates

INFO = read_info(index_path())
needs_index = pytest.mark.skipif(INFO is None, reason="search index not built")


def gated_summary(path):
    from corpusstudy.retrieval.evaluation import evaluate, load_cases, summarise
    from corpusstudy.retrieval.search import Search

    cases = load_cases(path)
    if "commentary" not in INFO.counts:  # CI builds the Bibles only
        cases = [c for c in cases if c["kind"] != "commentary"]
    return summarise(evaluate(Search(), cases, k=10))


@needs_index
def test_retrieval_eval_gates():
    from corpusstudy.retrieval.evaluation import EVAL_PATH

    s = gated_summary(EVAL_PATH)
    assert s["gated"]["recall@10"] >= 0.95, s
    assert s["gated"]["recall@5"] >= 0.9, s
    assert s["gated"]["mrr"] >= 0.75, s
    for cat in ("quote_en", "quote_fr", "ref_en", "ref_fr"):
        assert s["categories"][cat]["recall@5"] == 1.0, (cat, s["categories"][cat])


@needs_index
def test_retrieval_holdout_gates():
    from corpusstudy.config import REPO_ROOT

    s = gated_summary(REPO_ROOT / "eval" / "retrieval_holdout.yaml")
    assert s["gated"]["recall@10"] >= 0.9, s
