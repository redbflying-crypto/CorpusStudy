"""The producer-profile quotation rule (okf.quotes)."""

from pathlib import Path

from corpusstudy.okf import document
from corpusstudy.okf.bundle import Concept
from corpusstudy.okf.models import OkfFrontmatter
from corpusstudy.okf.quotes import check_quotes, normalise

TEXTS = {
    "/references/sources/kjv.md#Rom.5.1": "Therefore being justified by faith, we have peace "
    "with God through our Lord Jesus Christ:",
    "/references/sources/henry-concise.md#Rom.3.21-Rom.3.26": "It is free grace, mere mercy; "
    "there is nothing in us to deserve such favours. It comes freely unto us, but Christ "
    "bought it, and paid the price.",
}


def resolve(resource: str, concept_id: str) -> str | None:
    return TEXTS.get(resource)


def concept(body: str, sources=None) -> Concept:
    sources = sources or [
        {"id": "kjv-rom5", "resource": "/references/sources/kjv.md#Rom.5.1"},
        {"id": "henry-rom3", "resource": "/references/sources/henry-concise.md#Rom.3.21-Rom.3.26"},
        {"id": "calvin", "resource": "/references/sources/calvin-lcc.md#xx-p1"},
    ]
    meta = OkfFrontmatter.model_validate({"type": "Doctrine", "sources": sources})
    return Concept("doctrines/test", Path("doctrines/test.md"), meta, {}, body,
                   document.links(body, "doctrines/test"))  # fmt: skip


def errors(body: str) -> list[str]:
    return [i.message for i in check_quotes(concept(body), resolve) if i.level == "error"]


def test_exact_quotes_pass():
    body = (
        '"Being justified by faith, we have peace with God".[^kjv-rom5] Henry: "It is free '
        'grace, mere mercy" and “Christ bought it, and paid the price”.[^henry-rom3]\n\n'
        "[^kjv-rom5]: Romans 5:1\n[^henry-rom3]: Henry\n"
    )
    assert errors(body) == []


def test_ellipsis_fragments_must_each_occur():
    ok = '"It is free grace ... Christ bought it".[^henry-rom3]'
    bad = '"It is free grace ... Christ earned it".[^henry-rom3]'
    assert errors(ok) == []
    assert errors(bad) == ['quotation "christ earned it" not found in henry-rom3']


def test_misquote_and_wrong_source_are_errors():
    assert errors('"justified by works".[^kjv-rom5]') == [
        'quotation "justified by works" not found in kjv-rom5']  # fmt: skip
    # The right words, attributed to the wrong source.
    assert errors('"paid the price".[^kjv-rom5]') == [
        'quotation "paid the price" not found in kjv-rom5']  # fmt: skip


def test_segment_is_text_since_the_previous_marker():
    # The Henry quotation sits before the KJV marker's segment begins: checked against Henry.
    body = '"mere mercy"[^henry-rom3] then "peace with God"[^kjv-rom5]'
    assert errors(body) == []
    swapped = '"peace with God"[^henry-rom3] then "mere mercy"[^kjv-rom5]'
    assert len(errors(swapped)) == 2


def test_grouped_markers_accept_either_source():
    assert errors('"peace with God" and "mere mercy".[^kjv-rom5][^henry-rom3]') == []


def test_unresolvable_source_is_a_warning_not_an_error():
    issues = check_quotes(concept('"anything at all"[^calvin]'), resolve)
    assert [i.level for i in issues] == ["warning"]
    assert "quotes unchecked" in issues[0].message


def test_footnote_definitions_are_not_checked_and_short_fragments_ignored():
    body = 'A "no" here.[^kjv-rom5]\n\n[^kjv-rom5]: "Not a real quote" in a definition\n'
    assert errors(body) == []


def test_normalise_quotes_and_apostrophes():
    assert normalise("God’s  “elect”") == normalise('god\'s "elect"')


def test_quotation_after_the_last_marker_is_a_warning():
    body = '"peace with God".[^kjv-rom5] Later "an unsourced saying".\n\n"Another one" here.'
    issues = check_quotes(concept(body), resolve)
    assert [(i.level, i.message) for i in issues] == [
        ("warning", 'quotation "an unsourced saying" has no footnote'),
        ("warning", 'quotation "another one" has no footnote'),
    ]
