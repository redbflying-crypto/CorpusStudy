"""Checks on the ingested texts. Skipped until `python -m corpusstudy.ingest` has been run."""

import pytest

from corpusstudy.config import Translation as T
from corpusstudy.config import get_settings
from corpusstudy.corpus import BibleText, CorpusNotBuilt
from corpusstudy.schemas import VerseRef, parse_reference
from corpusstudy.versification import versification

BUILT = all((get_settings().data_dir / "processed" / f"{t}.jsonl").exists() for t in T)
needs_corpus = pytest.mark.skipif(not BUILT, reason="corpus not built; run the ingest first")


@pytest.fixture(scope="module")
def kjv():
    return BibleText.load(T.KJV)


@pytest.fixture(scope="module")
def lsg():
    return BibleText.load(T.LSG1910)


def test_missing_corpus_raises(tmp_path):
    with pytest.raises(CorpusNotBuilt):
        BibleText.load(T.KJV, data_dir=tmp_path)


@needs_corpus
def test_counts_match_versification(kjv, lsg):
    assert len(kjv) == 31102 + 116
    assert len(lsg) == 31170
    for text, tr in ((kjv, T.KJV), (lsg, T.LSG1910)):
        assert sum(1 for v in text if v.ref.verse > 0) == versification(tr).total_verses


@needs_corpus
@pytest.mark.parametrize(
    "tr,ref,expected",
    [
        (T.KJV, "John 3:16", "For God so loved the world, that he gave his only begotten Son"),
        (T.LSG1910, "Jean 3.16", "Car Dieu a tant aimé le monde qu’il a donné son Fils unique"),
        (T.KJV, "Gen 1:1", "In the beginning God created the heaven and the earth."),
        (T.LSG1910, "Ps 119:115", "Éloignez-vous de moi, méchants"),
        (T.KJV, "Mal 4:6", "And he shall turn the heart of the fathers to the children"),
        (T.LSG1910, "Malachie 4.6", "Il ramènera le cœur des pères à leurs enfants"),
    ],
)
def test_known_verses(kjv, lsg, tr, ref, expected):
    text = {T.KJV: kjv, T.LSG1910: lsg}[tr]
    p = parse_reference(ref)
    verse = text.get(VerseRef(book=p.book, chapter=p.start_chapter, verse=p.start_verse))
    assert verse is not None and verse.text.startswith(expected)


@needs_corpus
def test_no_markup_left(kjv, lsg):
    for text in (kjv, lsg):
        for v in text:
            assert "\\" not in v.text and "<" not in v.text and "|" not in v.text, v.ref.osis
            assert v.text == v.text.strip() and "  " not in v.text, v.ref.osis


@needs_corpus
def test_passage(kjv):
    verses = kjv.passage(parse_reference("Ps 23"))
    assert [v.ref.verse for v in verses] == [0, 1, 2, 3, 4, 5, 6]
    verses = kjv.passage(parse_reference("Rom 8:38-9:1"))
    assert [v.ref.osis for v in verses] == ["Rom.8.38", "Rom.8.39", "Rom.9.1"]
