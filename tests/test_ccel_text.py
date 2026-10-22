import json

import pytest

from corpusstudy.config import get_settings
from corpusstudy.ingest.ccel_text import (
    close_gaps,
    find_refs,
    join_lines,
    parse_ccel_text,
    strip_page_furniture,
)
from corpusstudy.ingest.raw import Excerpt

ABBR = {"Jud": "Judg", "So": "Song"}

# A miniature of the CCEL PDF text: contents, page numbers + running headers, both layouts,
# a missing "Chapter 1" line, a wrapped verse range and end-of-line hyphenation.
TEXT = """Matthew Henry's Concise Commentary on the Bible
Contents
Title Page 1
Genesis 2
Indexes 9
xlii
1
Title Page
Genesis
Genesis is a name taken from the Greek, and signifies the book of generation.
2
Genesis
Chapter 1
Chapter Outline
God creates heaven and earth. (1, 2)
God separates the earth from the wa- (3–31)
ters, and makes it fruitful.
Verses 1, 2
The first verse of the Bible gives us a satis-
factory account; see 1Jo 5:20; 2:1, and Jud 1:7.
3
Chapter 1
The page goes on, ver. #(11–13), here.
Verses 3–31
God said, Let there be light.
Chapter 2
The seventh day is sanctified.
Exodus
Exodus intro.
Chapter 1
The Israelites increase. (Ex. 1:1-7) They are oppressed. (Ex. 1:8-22)
Ex. 1:1-7 The children of Israel multiplied
greatly in Egypt; see Ex.
1:20.
Ex. 1:8-14. The new king feared them.
Ex. 1:15,16 The midwives feared God.
Chapter 2
Moses is born.
Leviticus
Chapter Outline
The burnt offerings. (1–17)
Verses 1–2
The first offering.
Indexes
Index of Scripture References
Genesis 1:1
"""


@pytest.fixture
def excerpts(tmp_path):
    path = tmp_path / "mhcc.txt"
    path.write_text(TEXT.replace("\n", "\r\n"), encoding="utf-8")
    return {e.id: e for e in parse_ccel_text(path, abbreviations=ABBR)}


def test_ids_in_order(excerpts):
    assert list(excerpts) == [
        "Gen.intro",
        "Gen.1.outline",
        "Gen.1.1-Gen.1.2",
        "Gen.1.3-Gen.1.31",
        "Gen.2",
        "Exod.intro",
        "Exod.1.outline",
        "Exod.1.1-Exod.1.7",
        "Exod.1.8-Exod.1.14",
        "Exod.1.15-Exod.1.16",
        "Exod.2",
        "Lev.1.outline",  # no "Chapter 1" line: the outline follows the book name
        "Lev.1.1-Lev.1.2",
    ]


def test_refs_and_topics(excerpts):
    assert excerpts["Gen.intro"].refs == ["Gen.1-Gen.50"]
    assert excerpts["Gen.1.outline"].refs == ["Gen.1"]
    assert excerpts["Gen.1.1-Gen.1.2"].topic == ("Genesis", "Chapter 1")


def test_page_furniture_and_hyphenation(excerpts):
    text = excerpts["Gen.1.1-Gen.1.2"].text
    assert text.startswith("The first verse of the Bible gives us a satisfactory account")
    assert "The page goes on, ver. 11–13, here." in text  # page "3" + header removed
    assert "Chapter 1" not in text


def test_outline_rebuilt_from_two_columns(excerpts):
    assert excerpts["Gen.1.outline"].text == (
        "Chapter outline: God creates heaven and earth. (1-2) "
        "God separates the earth from the waters, and makes it fruitful. (3-31)"
    )


def test_mentions_use_source_abbreviations(excerpts):
    mentions = excerpts["Gen.1.1-Gen.1.2"].mentions
    assert mentions == ["1John.5.20", "1John.2.1", "Judg.1.7"]


def test_reference_led_sections(excerpts):
    assert excerpts["Exod.1.1-Exod.1.7"].text.startswith("The children of Israel")
    # "Ex.\n1:20" wrapped mid-sentence is a mention, not a new section
    assert "Exod.1.20" in excerpts["Exod.1.1-Exod.1.7"].mentions
    assert excerpts["Exod.1.8-Exod.1.14"].text == "The new king feared them."
    assert excerpts["Exod.1.15-Exod.1.16"].text == "The midwives feared God."


def test_index_not_parsed(excerpts):
    assert not any("Index" in e.text for e in excerpts.values())


def test_strip_page_furniture():
    lines = ["text", "12", "Chapter 3", "more", "13", "Genesis", "Genesis", "intro"]
    assert strip_page_furniture(lines, {"Genesis"}) == ["text", "more", "Genesis", "intro"]


def test_join_lines():
    assert (
        join_lines(["a satis-", "factory", "ver. 1-", "14 here"]) == "a satisfactory ver. 1-14 here"
    )
    assert join_lines(["self-", "Denial"]) == "self- Denial"  # capital: not a hyphenated word


@pytest.mark.parametrize(
    "text,refs",
    [
        ("see Ro 7:11; Heb 3:13.", ["Rom.7.11", "Heb.3.13"]),
        ("I Kin. 11:1 and II Kin. 18:17-37", ["1Kgs.11.1", "2Kgs.18.17-2Kgs.18.37"]),
        ("Is. 54:5,6; 62:5", ["Isa.54.5", "Isa.62.5"]),
        ("So 1:3 and Jud 20:28", ["Song.1.3", "Judg.20.28"]),
        ("In verse 3:16 nothing", []),
    ],
)
def test_find_refs(text, refs):
    assert list(find_refs(text, ABBR)) == refs


def test_close_gaps():
    exs = [
        Excerpt(id="a", topic=(), refs=["John.3.1-John.3.8"]),
        Excerpt(id="b", topic=(), refs=["John.3.22-John.3.30"]),
        Excerpt(id="c", topic=(), refs=["John.3"]),  # whole-chapter rows are left alone
    ]
    notes = close_gaps(exs, lambda book, ch: 36)
    assert [e.refs[0] for e in exs] == ["John.3.1-John.3.21", "John.3.22-John.3.36", "John.3"]
    assert notes == [
        "John.3.1-John.3.8 -> John.3.1-John.3.21",
        "John.3.22-John.3.30 -> John.3.22-John.3.36",
    ]


PROCESSED = get_settings().data_dir / "processed" / "commentary" / "henry-concise.jsonl"


@pytest.mark.skipif(not PROCESSED.exists(), reason="Matthew Henry not imported")
def test_imported_henry():
    rows = [json.loads(line) for line in PROCESSED.read_text(encoding="utf-8").splitlines()]
    assert len(rows) > 5000
    assert len({r["id"] for r in rows}) == len(rows)
    chapters = {
        r["id"].split(":")[1].rsplit(".", 1)[0] for r in rows if r["id"].endswith("outline")
    }
    assert len(chapters) > 900
    john3 = [r for r in rows if r["refs"] == ["John.3.1-John.3.21"]]
    assert john3 and "Nicodemus" in john3[0]["text"]
