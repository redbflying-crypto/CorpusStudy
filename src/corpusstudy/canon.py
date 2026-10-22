"""The 66-book Protestant canon: English and Louis Segond names, OSIS/USFM codes, chapter counts.

Chapter counts follow KJV versification, which Louis Segond 1910 shares at the chapter level
(e.g. Joel 3 and Malachi 4 chapters, unlike the Hebrew 4 / 3). Verse-level differences are
handled in Phase 2 ingestion.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum


class Testament(StrEnum):
    OT = "OT"
    NT = "NT"


@dataclass(frozen=True)
class Book:
    order: int
    osis: str
    usfm: str
    name_en: str
    name_fr: str
    chapters: int
    aliases: tuple[str, ...] = field(default=())

    @property
    def testament(self) -> Testament:
        return Testament.OT if self.order <= 39 else Testament.NT

    def name(self, language: str = "en") -> str:
        return self.name_fr if language == "fr" else self.name_en


# order, OSIS, USFM, English, French (LSG), chapters, extra aliases (EN + FR abbreviations)
_BOOKS: list[tuple[int, str, str, str, str, int, tuple[str, ...]]] = [
    (1, "Gen", "GEN", "Genesis", "Genèse", 50, ("Ge", "Gn")),
    (2, "Exod", "EXO", "Exodus", "Exode", 40, ("Ex", "Exo")),
    (3, "Lev", "LEV", "Leviticus", "Lévitique", 27, ("Le", "Lv")),
    (4, "Num", "NUM", "Numbers", "Nombres", 36, ("Nu", "Nb", "Nomb")),
    (5, "Deut", "DEU", "Deuteronomy", "Deutéronome", 34, ("Dt", "De")),
    (6, "Josh", "JOS", "Joshua", "Josué", 24, ("Jos",)),
    (7, "Judg", "JDG", "Judges", "Juges", 21, ("Jg", "Jdg", "Jug")),
    (8, "Ruth", "RUT", "Ruth", "Ruth", 4, ("Ru", "Rt")),
    (9, "1Sam", "1SA", "1 Samuel", "1 Samuel", 31, ("1 Sa", "1 S")),
    (10, "2Sam", "2SA", "2 Samuel", "2 Samuel", 24, ("2 Sa", "2 S")),
    (11, "1Kgs", "1KI", "1 Kings", "1 Rois", 22, ("1 Ki", "1 Kgs", "1 Kin", "1 R")),
    (12, "2Kgs", "2KI", "2 Kings", "2 Rois", 25, ("2 Ki", "2 Kgs", "2 Kin", "2 R")),
    (13, "1Chr", "1CH", "1 Chronicles", "1 Chroniques", 29, ("1 Ch", "1 Chr", "1 Chron")),
    (14, "2Chr", "2CH", "2 Chronicles", "2 Chroniques", 36, ("2 Ch", "2 Chr", "2 Chron")),
    (15, "Ezra", "EZR", "Ezra", "Esdras", 10, ("Ezr", "Esd")),
    (16, "Neh", "NEH", "Nehemiah", "Néhémie", 13, ("Ne",)),
    (17, "Esth", "EST", "Esther", "Esther", 10, ("Est", "Es")),
    (18, "Job", "JOB", "Job", "Job", 42, ("Jb",)),
    (19, "Ps", "PSA", "Psalms", "Psaumes", 150, ("Psalm", "Psa", "Pss", "Psaume")),
    (20, "Prov", "PRO", "Proverbs", "Proverbes", 31, ("Pr", "Prv", "Pro")),
    (21, "Eccl", "ECC", "Ecclesiastes", "Ecclésiaste", 12, ("Ec", "Ecc", "Qo")),
    (
        22,
        "Song",
        "SNG",
        "Song of Solomon",
        "Cantique des cantiques",
        8,
        ("Song of Songs", "SS", "Cant", "Ct", "Cantique"),
    ),
    (23, "Isa", "ISA", "Isaiah", "Ésaïe", 66, ("Is", "Es")),
    (24, "Jer", "JER", "Jeremiah", "Jérémie", 52, ("Je", "Jr")),
    (25, "Lam", "LAM", "Lamentations", "Lamentations", 5, ("La", "Lm")),
    (26, "Ezek", "EZK", "Ezekiel", "Ézéchiel", 48, ("Eze", "Ez")),
    (27, "Dan", "DAN", "Daniel", "Daniel", 12, ("Da", "Dn")),
    (28, "Hos", "HOS", "Hosea", "Osée", 14, ("Ho", "Os")),
    (29, "Joel", "JOL", "Joel", "Joël", 3, ("Jl", "Joe")),
    (30, "Amos", "AMO", "Amos", "Amos", 9, ("Am",)),
    (31, "Obad", "OBA", "Obadiah", "Abdias", 1, ("Ob", "Ab")),
    (32, "Jonah", "JON", "Jonah", "Jonas", 4, ("Jon",)),
    (33, "Mic", "MIC", "Micah", "Michée", 7, ("Mi",)),
    (34, "Nah", "NAM", "Nahum", "Nahum", 3, ("Na",)),
    (35, "Hab", "HAB", "Habakkuk", "Habacuc", 3, ("Ha",)),
    (36, "Zeph", "ZEP", "Zephaniah", "Sophonie", 3, ("Zep", "So")),
    (37, "Hag", "HAG", "Haggai", "Aggée", 2, ("Ag", "Hg")),
    (38, "Zech", "ZEC", "Zechariah", "Zacharie", 14, ("Zec", "Za")),
    (39, "Mal", "MAL", "Malachi", "Malachie", 4, ("Ml",)),
    (40, "Matt", "MAT", "Matthew", "Matthieu", 28, ("Mt", "Mat")),
    (41, "Mark", "MRK", "Mark", "Marc", 16, ("Mk", "Mc", "Mr")),
    (42, "Luke", "LUK", "Luke", "Luc", 24, ("Lk", "Lu", "Lc")),
    (43, "John", "JHN", "John", "Jean", 21, ("Jn", "Jhn", "Joh", "Jea")),
    (44, "Acts", "ACT", "Acts", "Actes", 28, ("Ac",)),
    (45, "Rom", "ROM", "Romans", "Romains", 16, ("Ro", "Rm")),
    (46, "1Cor", "1CO", "1 Corinthians", "1 Corinthiens", 16, ("1 Co", "1 Cor")),
    (47, "2Cor", "2CO", "2 Corinthians", "2 Corinthiens", 13, ("2 Co", "2 Cor")),
    (48, "Gal", "GAL", "Galatians", "Galates", 6, ("Ga",)),
    (49, "Eph", "EPH", "Ephesians", "Éphésiens", 6, ("Ep",)),
    (50, "Phil", "PHP", "Philippians", "Philippiens", 4, ("Php", "Ph")),
    (51, "Col", "COL", "Colossians", "Colossiens", 4, ("Co",)),
    (52, "1Thess", "1TH", "1 Thessalonians", "1 Thessaloniciens", 5, ("1 Th", "1 Thess")),
    (53, "2Thess", "2TH", "2 Thessalonians", "2 Thessaloniciens", 3, ("2 Th", "2 Thess")),
    (54, "1Tim", "1TI", "1 Timothy", "1 Timothée", 6, ("1 Ti", "1 Tm")),
    (55, "2Tim", "2TI", "2 Timothy", "2 Timothée", 4, ("2 Ti", "2 Tm")),
    (56, "Titus", "TIT", "Titus", "Tite", 3, ("Tt", "Tit")),
    (57, "Phlm", "PHM", "Philemon", "Philémon", 1, ("Phm", "Philem")),
    (58, "Heb", "HEB", "Hebrews", "Hébreux", 13, ("He", "Hé")),
    (59, "Jas", "JAS", "James", "Jacques", 5, ("Jm", "Jc", "Ja")),
    (60, "1Pet", "1PE", "1 Peter", "1 Pierre", 5, ("1 Pe", "1 Pt", "1 Pet", "1 P")),
    (61, "2Pet", "2PE", "2 Peter", "2 Pierre", 3, ("2 Pe", "2 Pt", "2 Pet", "2 P")),
    (62, "1John", "1JN", "1 John", "1 Jean", 5, ("1 Jn", "1 Jhn", "1 Jo")),
    (63, "2John", "2JN", "2 John", "2 Jean", 1, ("2 Jn", "2 Jhn", "2 Jo")),
    (64, "3John", "3JN", "3 John", "3 Jean", 1, ("3 Jn", "3 Jhn", "3 Jo")),
    (65, "Jude", "JUD", "Jude", "Jude", 1, ("Jud", "Jd")),
    (66, "Rev", "REV", "Revelation", "Apocalypse", 22, ("Re", "Rv", "Ap", "Apoc")),
]

BOOKS: tuple[Book, ...] = tuple(Book(*row) for row in _BOOKS)
BOOKS_BY_OSIS: dict[str, Book] = {b.osis: b for b in BOOKS}
BOOKS_BY_USFM: dict[str, Book] = {b.usfm: b for b in BOOKS}


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, dots and spaces; map leading numerals: 'I Rois' -> '1rois'."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().strip()
    s = re.sub(r"^(iii|ii|i)(?=[\s.])", lambda m: str(len(m.group(1))), s)
    s = re.sub(r"^(1st|first|premier|premiere)\b", "1", s)
    s = re.sub(r"^(2nd|second|seconde|deuxieme)\b", "2", s)
    s = re.sub(r"^(3rd|third|troisieme)\b", "3", s)
    return re.sub(r"[\s.]", "", s)


def _build_alias_index() -> dict[str, Book]:
    index: dict[str, Book] = {}
    ambiguous: set[str] = set()
    for book in BOOKS:
        names = (book.osis, book.usfm, book.name_en, book.name_fr, *book.aliases)
        for name in names:
            key = normalize_name(name)
            if key in index and index[key] is not book:
                ambiguous.add(key)
            index.setdefault(key, book)
    # Abbreviations shared by two books (e.g. 'Es' = Esther / Ésaïe) must be spelled out.
    for key in ambiguous:
        index.pop(key, None)
    return index


_ALIASES: dict[str, Book] = _build_alias_index()


def find_book(name: str) -> Book | None:
    """Resolve an English or French name, abbreviation, OSIS or USFM code to a Book."""
    return _ALIASES.get(normalize_name(name))


def get_book(name: str) -> Book:
    book = find_book(name)
    if book is None:
        raise KeyError(f"unknown book: {name!r}")
    return book


def total_chapters() -> int:
    return sum(b.chapters for b in BOOKS)
