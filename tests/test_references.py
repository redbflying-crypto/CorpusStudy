import pytest
from pydantic import ValidationError

from corpusstudy.config import Language
from corpusstudy.schemas import PassageRef, VerseRef, parse_reference, parse_references


@pytest.mark.parametrize(
    "text,osis",
    [
        ("John 3:16", "John.3.16"),
        ("Jean 3.16", "John.3.16"),
        ("Jean 3,16", "John.3.16"),
        ("Jn 3:16", "John.3.16"),
        ("Rom 8:28-30", "Rom.8.28-Rom.8.30"),
        ("Romains 8.28-30", "Rom.8.28-Rom.8.30"),
        ("Rom 8:28–9:2", "Rom.8.28-Rom.9.2"),
        ("Ps 23", "Ps.23"),
        ("Psaume 23", "Ps.23"),
        ("Gen 1-3", "Gen.1-Gen.3"),
        ("1 Cor 13:4-7", "1Cor.13.4-1Cor.13.7"),
        ("1 Corinthiens 13.4-7", "1Cor.13.4-1Cor.13.7"),
        ("I Rois 19:12", "1Kgs.19.12"),
        ("Jude 3", "Jude.1.3"),
        ("Jude 3-5", "Jude.1.3-Jude.1.5"),
        ("Philemon 1:6", "Phlm.1.6"),
        ("Ésaïe 53:5", "Isa.53.5"),
        ("Song of Solomon 2:4", "Song.2.4"),
    ],
)
def test_parse_reference(text, osis):
    assert parse_reference(text).osis == osis


@pytest.mark.parametrize(
    "text",
    ["Hezekiah 3:4", "John", "3:16", "Rom 17:1", "Jude 2:1", "Rom 8:30-28", "", "hello world"],
)
def test_parse_reference_rejects(text):
    with pytest.raises(ValueError):
        parse_reference(text)


def test_parse_references_list():
    refs = parse_references("Rom 8:28; Eph 1:4-5 ; Jean 3.16")
    assert [r.osis for r in refs] == ["Rom.8.28", "Eph.1.4-Eph.1.5", "John.3.16"]


@pytest.mark.parametrize(
    "text,en,fr",
    [
        ("John 3:16", "John 3:16", "Jean 3.16"),
        ("Rom 8:28-30", "Romans 8:28-30", "Romains 8.28-30"),
        ("Rom 8:28-9:2", "Romans 8:28-9:2", "Romains 8.28-9.2"),
        ("Ps 23", "Psalms 23", "Psaumes 23"),
        ("Gen 1-3", "Genesis 1-3", "Genèse 1-3"),
        ("Jude 3", "Jude 3", "Jude 3"),
    ],
)
def test_display(text, en, fr):
    ref = parse_reference(text)
    assert ref.display(Language.EN) == en
    assert ref.display(Language.FR) == fr


def test_contains():
    passage = parse_reference("Rom 8:28-9:2")
    assert passage.contains(VerseRef(book="Rom", chapter=8, verse=39))
    assert passage.contains(VerseRef(book="Rom", chapter=9, verse=2))
    assert not passage.contains(VerseRef(book="Rom", chapter=9, verse=3))
    assert not passage.contains(VerseRef(book="1Cor", chapter=8, verse=30))
    assert parse_reference("Ps 23").contains(VerseRef(book="Ps", chapter=23, verse=6))


def test_verse_ref_validation():
    assert VerseRef(book="Rom", chapter=8, verse=28).display(Language.FR) == "Romains 8.28"
    with pytest.raises(ValidationError):
        VerseRef(book="Rom", chapter=17, verse=1)
    with pytest.raises(ValidationError):
        VerseRef(book="Romans", chapter=1, verse=1)  # must be an OSIS code
    with pytest.raises(ValidationError):
        VerseRef(book="Rom", chapter=1, verse=0)


def test_passage_requires_end_verse_across_chapters():
    with pytest.raises(ValidationError):
        PassageRef(book="Rom", start_chapter=8, start_verse=28, end_chapter=9)


def test_passage_parse_classmethod():
    assert PassageRef.parse("Eph 2:8-9").display() == "Ephesians 2:8-9"
