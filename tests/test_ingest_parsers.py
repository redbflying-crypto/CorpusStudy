import json

import pytest

from corpusstudy.ingest.osis import parse_osis
from corpusstudy.ingest.pipeline import Report, repair_numbering, validate
from corpusstudy.ingest.raw import RawVerse
from corpusstudy.ingest.tabular import parse_tabular
from corpusstudy.ingest.text import clean_text
from corpusstudy.ingest.usfm import parse_usfm_lines

OSIS = """<?xml version="1.0" encoding="utf-8"?>
<osis xmlns="http://www.bibletechnologies.net/2003/OSIS/namespace">
 <osisText osisIDWork="Bible.en.kjv">
  <header><work osisWork="kjv"><title>Header title</title></work></header>
  <div type="book" osisID="Gen">
   <title type="main" short="Genesis">The First Book of Moses</title>
   <chapter osisRef="Gen.1" sID="Gen.1.seID.1" n="1" />
   <p>
    <verse osisID="Gen.1.1" sID="Gen.1.1.seID.2" n="1" />In the beginning God created
    the heaven and the earth.<verse eID="Gen.1.1.seID.2" />
    <verse osisID="Gen.1.2" sID="Gen.1.2.seID.3" n="2" />And the earth was without form,
    and void; and darkness <transChange type="added">was</transChange> upon the face of
    the deep.<note type="study">a note</note><verse eID="Gen.1.2.seID.3" />
   </p>
   <chapter eID="Gen.1.seID.1" />
  </div>
  <div type="book" osisID="Ps">
   <chapter osisRef="Ps.3" sID="Ps.3.seID.4" n="3" />
   <title type="psalm" canonical="true">A Psalm of David, when he fled from Absalom his son.</title>
   <p><verse osisID="Ps.3.1" sID="Ps.3.1.seID.5" n="1" />LORD, how are they increased that
   trouble me!<verse eID="Ps.3.1.seID.5" /></p>
   <chapter eID="Ps.3.seID.4" />
   <chapter osisRef="Ps.119" sID="Ps.119.seID.6" n="119" />
   <title type="psalm" canonical="true">ALEPH.</title>
   <p><verse osisID="Ps.119.1" sID="Ps.119.1.seID.7" n="1" />Blessed are the
   undefiled in the way.<verse eID="Ps.119.1.seID.7" /></p>
   <chapter eID="Ps.119.seID.6" />
  </div>
  <div type="book" osisID="John">
   <chapter osisID="John.3">
    <verse osisID="John.3.16">For God so loved the world.</verse>
   </chapter>
  </div>
  <div type="book" osisID="Tob">
   <chapter osisRef="Tob.1" sID="Tob.1.seID.8" n="1" />
   <p><verse osisID="Tob.1.1" sID="Tob.1.1.seID.9" n="1" />Apocrypha.<verse eID="Tob.1.1.seID.9" /></p>
  </div>
 </osisText>
</osis>
"""


@pytest.fixture
def osis_verses(tmp_path):
    path = tmp_path / "kjv.osis.xml"
    path.write_text(OSIS, encoding="utf-8")
    return {v.key: v.text for v in parse_osis(path)}


def test_osis_milestone_verses(osis_verses):
    assert osis_verses[("Gen", 1, 1)] == "In the beginning God created the heaven and the earth."


def test_osis_keeps_supplied_words_and_drops_notes(osis_verses):
    text = osis_verses[("Gen", 1, 2)]
    assert "darkness was upon" in text
    assert "a note" not in text


def test_osis_psalm_title_is_verse_zero(osis_verses):
    assert osis_verses[("Ps", 3, 0)].startswith("A Psalm of David")
    assert osis_verses[("Ps", 3, 1)].startswith("LORD, how are they")


def test_osis_ps119_acrostic_is_not_a_title(osis_verses):
    assert ("Ps", 119, 0) not in osis_verses
    assert "ALEPH" not in osis_verses[("Ps", 119, 1)]


def test_osis_container_verse(osis_verses):
    assert osis_verses[("John", 3, 16)] == "For God so loved the world."


def test_osis_skips_apocrypha_and_headings(osis_verses):
    assert not any(k[0] == "Tob" for k in osis_verses)
    assert not any("Header title" in t or "First Book" in t for t in osis_verses.values())


USFM = r"""\id PSA FRA - Louis Segond
\h PSAUMES
\mt1 LES PSAUMES
\ip Une introduction moderne \x - \xo 1.1 \xt Né 9:5\x* à ignorer.
\c 3
\s Confiance
\r (2 S 15:13.)
\q \v 1 Psaume de David. A l’occasion de sa fuite devant Absalom, son fils.
\q \v 2 O Éternel, que mes ennemis \f + \fr 3.2 \ft note\f* sont nombreux!
\q Quelle multitude se lève contre moi!
\s Un titre au milieu
\q \v 3 \wj Paroles\wj* et \w mot|lemma="x"\w* \qs Pause.\qs*
\c 4
\q \v 1-2 Deux versets réunis.
\id ZZZ Livre inconnu
\c 1
\v 1 Ignoré.
"""


@pytest.fixture
def usfm_verses():
    return {v.key: v.text for v in parse_usfm_lines(USFM.splitlines())}


def test_usfm_numbered_title_stays_verse_one(usfm_verses):
    assert usfm_verses[("Ps", 3, 1)].startswith("Psaume de David.")


def test_usfm_continuation_lines_and_notes(usfm_verses):
    assert usfm_verses[("Ps", 3, 2)] == (
        "O Éternel, que mes ennemis sont nombreux! Quelle multitude se lève contre moi!"
    )


def test_usfm_character_styles_unwrapped(usfm_verses):
    assert usfm_verses[("Ps", 3, 3)] == "Paroles et mot Pause."


def test_usfm_skips_intro_headings_and_unknown_books(usfm_verses):
    joined = " ".join(usfm_verses.values())
    for unwanted in ("introduction", "Confiance", "2 S 15", "Un titre", "Ignoré"):
        assert unwanted not in joined


def test_usfm_verse_range_uses_first_number(usfm_verses):
    assert usfm_verses[("Ps", 4, 1)] == "Deux versets réunis."


def test_clean_text():
    assert clean_text("  a\n  b ,  c . ") == "a b, c."
    assert clean_text("Pourquoi ? Car !") == "Pourquoi ? Car !"  # French spacing kept


def test_repair_stray_space_in_verse_number():
    verses = [RawVerse("Ps", 119, n, f"v{n}") for n in range(1, 115)]
    verses.append(RawVerse("Ps", 119, 11, "5Éloignez-vous de moi"))
    verses.append(RawVerse("Ps", 119, 116, "Soutiens-moi"))
    report = Report(translation="LSG1910")
    fixed = repair_numbering(verses, report)
    assert [v.verse for v in fixed][-2:] == [115, 116]
    assert fixed[-2].text == "Éloignez-vous de moi"
    assert report.repairs == ["Ps.119.11 (repeat) -> 115"]


def test_validate_reports_gaps_and_unknown_titles():
    verses = [RawVerse("Obad", 1, 1, "a"), RawVerse("Obad", 1, 3, "c"), RawVerse("Gen", 1, 0, "x")]
    report = Report(translation="KJV")
    validate(verses, report)
    assert "Obad 1: missing verses [2]" in report.errors
    assert any("superscription outside Psalms" in e for e in report.errors)
    assert any(e == "missing chapter Gen 2" for e in report.errors)


@pytest.mark.parametrize("suffix", [".csv", ".jsonl", ".json"])
def test_tabular_formats(tmp_path, suffix):
    rows = [
        {"book": "Jean", "chapter": 3, "verse": 16, "text": "Car Dieu a tant aimé le monde"},
        {"book": "1 Cor", "chapter": 13, "verse": 4, "text": "La charité est patiente"},
    ]
    path = tmp_path / f"verses{suffix}"
    if suffix == ".csv":
        lines = ["livre,chapitre,verset,texte"] + [
            f"{r['book']},{r['chapter']},{r['verse']},{r['text']}" for r in rows
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
    elif suffix == ".jsonl":
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    else:
        path.write_text(json.dumps({"verses": rows}), encoding="utf-8")
    verses = list(parse_tabular(path))
    assert [v.key for v in verses] == [("John", 3, 16), ("1Cor", 13, 4)]


def test_tabular_unknown_book(tmp_path):
    path = tmp_path / "v.jsonl"
    path.write_text(json.dumps({"book": "Hezekiah", "chapter": 1, "verse": 1, "text": "x"}))
    with pytest.raises(ValueError):
        list(parse_tabular(path))
