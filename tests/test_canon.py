import pytest

from corpusstudy.canon import (
    BOOKS,
    BOOKS_BY_OSIS,
    BOOKS_BY_USFM,
    find_book,
    get_book,
    total_chapters,
)
from corpusstudy.canon import (
    Testament as Covenant,
)


def test_sixty_six_books_in_order():
    assert len(BOOKS) == 66
    assert [b.order for b in BOOKS] == list(range(1, 67))


def test_testaments():
    assert sum(b.testament == Covenant.OT for b in BOOKS) == 39
    assert sum(b.testament == Covenant.NT for b in BOOKS) == 27
    assert BOOKS_BY_OSIS["Mal"].testament == Covenant.OT
    assert BOOKS_BY_OSIS["Matt"].testament == Covenant.NT


def test_chapter_total_matches_kjv():
    assert total_chapters() == 1189


def test_codes_unique():
    assert len(BOOKS_BY_OSIS) == 66
    assert len(BOOKS_BY_USFM) == 66


@pytest.mark.parametrize(
    "osis,chapters",
    [("Gen", 50), ("Ps", 150), ("Isa", 66), ("Joel", 3), ("Mal", 4), ("Obad", 1), ("Rev", 22)],
)
def test_chapter_counts(osis, chapters):
    assert BOOKS_BY_OSIS[osis].chapters == chapters


@pytest.mark.parametrize(
    "name,osis",
    [
        ("Genesis", "Gen"),
        ("Genèse", "Gen"),
        ("genese", "Gen"),
        ("GEN", "Gen"),
        ("Ésaïe", "Isa"),
        ("Esaie", "Isa"),
        ("Isaiah", "Isa"),
        ("1 Rois", "1Kgs"),
        ("I Rois", "1Kgs"),
        ("II Kings", "2Kgs"),
        ("1 Cor", "1Cor"),
        ("1 Co", "1Cor"),
        ("1Corinthiens", "1Cor"),
        ("Jean", "John"),
        ("Jn", "John"),
        ("1 Jean", "1John"),
        ("Psalm", "Ps"),
        ("Psaumes", "Ps"),
        ("Apocalypse", "Rev"),
        ("Song of Songs", "Song"),
        ("Cantique des cantiques", "Song"),
        ("Jacques", "Jas"),
        ("Hébreux", "Heb"),
        ("Philem", "Phlm"),
    ],
)
def test_find_book_en_fr_and_codes(name, osis):
    book = find_book(name)
    assert book is not None, name
    assert book.osis == osis


def test_ambiguous_abbreviation_is_rejected():
    # 'Es' is Esther in English usage and Ésaïe in French: must be spelled out.
    assert find_book("Es") is None
    assert find_book("Esther").osis == "Esth"


def test_get_book_unknown_raises():
    with pytest.raises(KeyError):
        get_book("Maccabees")


def test_localised_name():
    assert BOOKS_BY_OSIS["Rom"].name("en") == "Romans"
    assert BOOKS_BY_OSIS["Rom"].name("fr") == "Romains"
