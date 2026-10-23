import json

import pytest

from corpusstudy.config import get_settings
from corpusstudy.ingest.commentary import (
    COMMENTARY_SOURCES,
    CommentaryReport,
    excerpt_rows,
)
from corpusstudy.ingest.thml import Excerpt, normalise_osis, parse_ccel_thml
from corpusstudy.schemas import PassageRef

THML = """<html><head><title>Calvin: Commentaries</title></head>
<body>
<p class="div1">Calvin: Commentaries</p>
<h1 id="i-p0.1">CALVIN: COMMENTARIES</h1>
<h2 id="vi-p0.1">II The Knowledge of God</h2>
<p class="normal" id="vi-p1">An editor's introduction that must be skipped.</p>
<h3 id="vi-p0.2">THE TEXT</h3>
<p class="normal" id="vi-p90"><i>In the
beginning God created the heaven and the earth</i>. <scripRef passage="Genesis 1:1" id="vi-p90.1"
parsed="|Gen|1|1|0|0" osisRef="Bible:Gen.1.1">Gen. 1:1</scripRef>.</p>
<p class="normal" id="vi-p91"><i>In the beginning</i>. To interpret <i>the beginning</i> as meaning
Christ is too frivolous<a style="mso-footnote-id:ftn3" href="#_ftn3" name="_ftnref3"><span
class="MsoFootnoteReference">[1]</span></a>. Compare <scripRef osisRef="Bible:John.1.1-John.1.3">John
1:1-3</scripRef>.</p>
<p class="normal" id="vi-p92">By the word <i>create</i>, he shows that something was
<span class="pb">140</span>made which did not before exist.</p>
<h3 id="vi-p50.1">2. PROVIDENCE AND GOD</h3>
<p class="normal" id="vi-p93"><i>The foreign born you shall not oppress</i>.
<scripRef osisRef="Bible:Exod.22.21-Exod.22.22 Bible:Lev.19.33-Lev.19.34">Ex. 22:21-22,
Lev. 19:33-34</scripRef>. (Calvin's wording.)</p>
<p class="normal" id="vi-p94">Comment with a bad tag <scripRef osisRef="Bible:Acts.6.22">Acts
6:22</scripRef> and an apocryphal one <scripRef osisRef="Bible:Tob.1.1">Tob. 1:1</scripRef>.</p>
<h2 id="vii-p0.1">III Jesus Christ</h2>
<p class="normal" id="vii-p1">Another introduction, skipped.</p>
<p class="div1">Indexes</p>
<p class="normal" id="xv-p1">Index entries, skipped.</p>
</body></html>"""


@pytest.fixture
def excerpts(tmp_path):
    path = tmp_path / "calcom.doc"
    path.write_text(THML, encoding="utf-8")
    return parse_ccel_thml(path)


def test_excerpts_split_on_lemmas(excerpts):
    assert [e.id for e in excerpts] == ["vi-p90", "vi-p93"]
    assert excerpts[0].refs == ["Gen.1.1"]
    assert excerpts[1].refs == ["Exod.22.21-Exod.22.22", "Lev.19.33-Lev.19.34"]


def test_editor_text_and_indexes_skipped(excerpts):
    joined = " ".join(e.text for e in excerpts)
    for unwanted in ("editor's introduction", "Another introduction", "Index entries"):
        assert unwanted not in joined


def test_footnotes_and_page_numbers_removed(excerpts):
    text = excerpts[0].text
    assert "[1]" not in text and "140" not in text
    assert "too frivolous. Compare John 1:1-3." in text
    assert "something was made which did not" in text
    assert text.count("\n\n") == 2  # three paragraphs


def test_mentions_and_topics(excerpts):
    assert excerpts[0].mentions == ["John.1.1-John.1.3"]
    assert excerpts[0].topic == ("II The Knowledge of God",)
    assert excerpts[1].topic == ("II The Knowledge of God", "2. Providence And God")
    assert "Tob.1.1" not in excerpts[1].mentions  # outside the canon


def test_normalise_osis():
    assert normalise_osis("Bible:Gen.1.1 Bible:Rom.8.28-Rom.8.30") == [
        "Gen.1.1",
        "Rom.8.28-Rom.8.30",
    ]
    assert normalise_osis("Bible:Tob.1.1 Bible:Gen") == []


@pytest.mark.parametrize(
    "osis,display",
    [("Gen.1.1", "Genesis 1:1"), ("Rom.8.28-Rom.9.2", "Romans 8:28-9:2"),
     ("Ps.23", "Psalms 23"), ("Gen.1-Gen.3", "Genesis 1-3")],
)  # fmt: skip
def test_passage_from_osis(osis, display):
    assert PassageRef.from_osis(osis).display() == display


@pytest.mark.parametrize("osis", ["Gen.1.1-Exod.1.1", "Gen.1.1-Gen.2", "Gen"])
def test_passage_from_osis_rejects(osis):
    with pytest.raises(ValueError):
        PassageRef.from_osis(osis)


def test_rows_apply_corrections_and_drop_invalid():
    source = COMMENTARY_SOURCES["calvin-lcc"]
    report = CommentaryReport(source.id)
    ex = Excerpt(id="p1", topic=("T",), refs=["Acts.6.22"], mentions=["Rom.17.1", "John.3.16"],
                 paragraphs=["text"])  # fmt: skip
    (row,) = excerpt_rows(source, [ex], report)
    assert row["id"] == "calvin-lcc:p1" and row["work"] == "calvin"
    assert row["refs"] == ["Acts.16.22"]
    assert row["mentions"] == ["John.3.16"]
    assert report.corrected_refs == ["Acts.6.22 -> Acts.16.22"]
    assert report.dropped_refs == ["Rom.17.1"]


PROCESSED = get_settings().data_dir / "processed" / "commentary" / "calvin-lcc.jsonl"


@pytest.mark.skipif(not PROCESSED.exists(), reason="Calvin not imported")
def test_imported_calvin():
    rows = [json.loads(line) for line in PROCESSED.read_text(encoding="utf-8").splitlines()]
    assert len(rows) >= 240
    assert all(r["work"] == "calvin" and r["text"] for r in rows)
    refs = {ref for r in rows for ref in r["refs"]}
    assert "Gen.1.1" in refs and "Acts.16.22" in refs
    for r in rows:
        for osis in r["refs"] + r["mentions"]:
            PassageRef.from_osis(osis)


@pytest.mark.skipif(not PROCESSED.exists(), reason="Calvin not imported")
def test_commentary_store_lookup():
    from corpusstudy.commentary import CommentaryStore

    store = CommentaryStore.load("calvin-lcc")
    excerpt = store.get("vi-p90")
    assert excerpt is not None and excerpt.refs == ("Gen.1.1",)
    assert excerpt.local_id == "vi-p90" and excerpt.work == "calvin"
    assert store.get("no-such-paragraph") is None
