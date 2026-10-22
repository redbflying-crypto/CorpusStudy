"""Checks on the committed versification tables and the KJV <-> LSG 1910 mapping."""

import pytest

from corpusstudy.canon import BOOKS
from corpusstudy.config import Language
from corpusstudy.config import Translation as T
from corpusstudy.ingest.align import align_sequences, anchors
from corpusstudy.ingest.raw import RawVerse
from corpusstudy.schemas import VerseRef, parse_reference
from corpusstudy.versification import map_passage, map_verse, versification


@pytest.mark.parametrize("tr,total", [(T.KJV, 31102), (T.LSG1910, 31170)])
def test_verse_totals(tr, total):
    assert versification(tr).total_verses == total


@pytest.mark.parametrize("tr", list(T))
def test_chapter_counts_match_canon(tr):
    v = versification(tr)
    for book in BOOKS:
        assert len(v.verse_counts[book.osis]) == book.chapters, book.osis
        assert all(n > 0 for n in v.verse_counts[book.osis]), book.osis


def test_kjv_psalm_titles():
    kjv = versification(T.KJV)
    assert sum(len(c) for c in kjv.superscriptions.values()) == 116
    assert kjv.has_title("Ps", 51) and not kjv.has_title("Ps", 1)
    assert kjv.exists(VerseRef(book="Ps", chapter=51, verse=0))
    assert not versification(T.LSG1910).exists(VerseRef(book="Ps", chapter=51, verse=0))


@pytest.mark.parametrize(
    "book,chapter,kjv,lsg",
    [("Ps", 51, 19, 21), ("Mal", 4, 6, 6), ("Joel", 2, 32, 32), ("3John", 1, 14, 15),
     ("Job", 38, 41, 38), ("Mark", 9, 50, 51), ("John", 3, 36, 36)],
)  # fmt: skip
def test_known_verse_counts(book, chapter, kjv, lsg):
    assert versification(T.KJV).verse_count(book, chapter) == kjv
    assert versification(T.LSG1910).verse_count(book, chapter) == lsg


def test_check_passage():
    assert versification(T.LSG1910).check_passage(parse_reference("Ps 51:21")) == []
    assert versification(T.KJV).check_passage(parse_reference("Ps 51:21")) == [
        "Psalms 51 has 19 verses, not 21"
    ]
    assert versification(T.KJV).check_passage(parse_reference("Rom 8:28-30")) == []


@pytest.mark.parametrize(
    "kjv,lsg",
    [
        ("Ps 51:1-4", "Psaumes 51.3-6"),  # two-verse title in LSG
        ("Ps 3:1", "Psaumes 3.2"),  # one-verse title
        ("Exod 8:1", "Exode 7.26"),  # Hebrew chapter break
        ("Job 38:39-41", "Job 39.1-3"),
        ("Song 6:13", "Cantique des cantiques 7.1"),
        ("Mark 9:50", "Marc 9.50-51"),  # one KJV verse, two LSG verses
        ("3 John 14", "3 Jean 14-15"),
        ("Mal 4:5-6", "Malachie 4.5-6"),  # same numbering
        ("John 3:16", "Jean 3.16"),
        ("Joel 2:28", "Joël 2.28"),
    ],
)
def test_map_kjv_to_lsg(kjv, lsg):
    assert map_passage(parse_reference(kjv), T.KJV, T.LSG1910).display(Language.FR) == lsg


def test_map_lsg_to_kjv():
    assert map_passage(parse_reference("Psaume 51.3"), T.LSG1910, T.KJV).osis == "Ps.51.1"
    # LSG's numbered title maps back to the KJV unnumbered title (verse 0).
    assert [
        r.osis for r in map_verse(VerseRef(book="Ps", chapter=51, verse=1), T.LSG1910, T.KJV)
    ] == ["Ps.51.0"]
    assert [
        r.osis for r in map_verse(VerseRef(book="Acts", chapter=19, verse=40), T.LSG1910, T.KJV)
    ] == [
        "Acts.19.40",
        "Acts.19.41",
    ]


def test_whole_chapters_keep_their_numbers():
    assert map_passage(parse_reference("Ps 23"), T.KJV, T.LSG1910).osis == "Ps.23"


def test_anchors_fold_names_and_numbers():
    assert {"absa", "davi"} <= anchors("A Psalm of David, when he fled from Absalom")
    assert anchors("Psaume de David. A l’occasion de sa fuite devant Absalom") >= {"davi", "absa"}
    assert "12" in anchors("twelve 12 tribes")


def test_align_sequences_title_and_split():
    en = [
        RawVerse("Ps", 9, 0, "A Psalm of David to the chief Musician upon Muthlabben."),
        RawVerse("Ps", 9, 1, "I will praise thee, O LORD, with my whole heart; I will shew forth all thy marvellous works."),
        RawVerse("Ps", 9, 2, "I will be glad and rejoice in thee: I will sing praise to thy name, O thou most High."),
    ]  # fmt: skip
    fr = [
        RawVerse("Ps", 9, 1, "Au chef des chantres. Sur Mut Labben. Psaume de David."),
        RawVerse("Ps", 9, 2, "Je louerai l’Éternel de tout mon cœur, Je raconterai toutes tes merveilles."),
        RawVerse("Ps", 9, 3, "Je ferai de toi le sujet de ma joie et de mon allégresse, Je chanterai ton nom, Dieu Très Haut!"),
    ]  # fmt: skip
    links = align_sequences(en, fr, ratio=1.0)
    assert [(link.left, link.right) for link in links] == [
        ((("Ps", 9, 0),), (("Ps", 9, 1),)),
        ((("Ps", 9, 1),), (("Ps", 9, 2),)),
        ((("Ps", 9, 2),), (("Ps", 9, 3),)),
    ]
