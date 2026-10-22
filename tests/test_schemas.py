from datetime import date

import pytest
from pydantic import ValidationError

from corpusstudy.config import Language, Translation
from corpusstudy.schemas import (
    Answer,
    ChatRequest,
    Chunk,
    Citation,
    KnowledgeFrontmatter,
    KnowledgeType,
    RetrievalResult,
    Retriever,
    ReviewStatus,
    Route,
    SourceNote,
    SourceType,
    Verse,
    VerseRef,
    parse_reference,
)

JOHN_3_16 = parse_reference("John 3:16")


def scripture_chunk(**overrides):
    fields = dict(
        id="KJV:John.3.16",
        source_type=SourceType.SCRIPTURE,
        text="For God so loved the world...",
        language=Language.EN,
        refs=[JOHN_3_16],
        translation=Translation.KJV,
    )
    return Chunk(**(fields | overrides))


def test_verse_id():
    v = Verse(ref=VerseRef(book="John", chapter=3, verse=16), translation=Translation.KJV, text="x")
    assert v.id == "KJV:John.3.16"


def test_scripture_chunk_ok():
    assert scripture_chunk().translation == Translation.KJV


def test_scripture_needs_translation():
    with pytest.raises(ValidationError):
        scripture_chunk(translation=None)


def test_scripture_needs_refs():
    with pytest.raises(ValidationError):
        scripture_chunk(refs=[])


def test_scripture_translation_matches_language():
    with pytest.raises(ValidationError):
        scripture_chunk(language=Language.FR)


def test_commentary_cannot_be_labelled_scripture():
    # A commentary work on a Scripture chunk is refused...
    with pytest.raises(ValidationError):
        scripture_chunk(work="henry")
    # ...and a commentary chunk can never carry a Bible translation.
    with pytest.raises(ValidationError):
        Chunk(
            id="henry:John.3.16",
            source_type=SourceType.COMMENTARY,
            text="Here is ...",
            language=Language.EN,
            refs=[JOHN_3_16],
            work="henry",
            translation=Translation.KJV,
        )


def test_commentary_must_be_public_domain():
    ok = Chunk(
        id="calvin:Rom.9",
        source_type=SourceType.COMMENTARY,
        text="...",
        language=Language.EN,
        work="calvin",
    )
    assert ok.work == "calvin"
    with pytest.raises(ValidationError):
        Chunk(
            id="x",
            source_type=SourceType.COMMENTARY,
            text="...",
            language=Language.EN,
            work="modern-study-bible",
        )


def test_knowledge_chunk_names_its_file():
    with pytest.raises(ValidationError):
        Chunk(id="k1", source_type=SourceType.KNOWLEDGE, text="...", language=Language.EN)


def test_models_are_frozen_and_strict():
    c = scripture_chunk()
    with pytest.raises(ValidationError):
        c.text = "changed"
    with pytest.raises(ValidationError):
        scripture_chunk(unexpected="field")


FRONTMATTER = dict(
    id="justification",
    type="doctrine",
    title="Justification by Faith",
    title_fr="La justification par la foi",
    key_passages=["Rom 3:21-26", "Romains 5.1", "Gal 2:16"],
    related=["faith", "grace"],
    sources=["calvin", "henry"],
)


def test_frontmatter_parses_passages():
    fm = KnowledgeFrontmatter(**FRONTMATTER)
    assert fm.type == KnowledgeType.DOCTRINE
    assert [p.osis for p in fm.key_passages] == [
        "Rom.3.21-Rom.3.26",
        "Rom.5.1",
        "Gal.2.16",
    ]
    assert fm.review_status == ReviewStatus.DRAFT


def test_frontmatter_rejects_bad_passage():
    with pytest.raises(ValidationError):
        KnowledgeFrontmatter(**FRONTMATTER | {"key_passages": ["Rom 30:1"]})


def test_frontmatter_rejects_bad_slug_and_source():
    with pytest.raises(ValidationError):
        KnowledgeFrontmatter(**FRONTMATTER | {"id": "Justification By Faith"})
    with pytest.raises(ValidationError):
        KnowledgeFrontmatter(**FRONTMATTER | {"related": ["Grace"]})
    with pytest.raises(ValidationError):
        KnowledgeFrontmatter(**FRONTMATTER | {"sources": ["wikipedia"]})


def test_reviewed_file_must_name_reviewer():
    with pytest.raises(ValidationError):
        KnowledgeFrontmatter(**FRONTMATTER | {"review_status": "reviewed"})
    fm = KnowledgeFrontmatter(
        **FRONTMATTER
        | {"review_status": "reviewed", "reviewed_by": "D. Nguyen", "reviewed_on": "2026-10-22"}
    )
    assert fm.reviewed_on == date(2026, 10, 22)


def test_retrieval_result():
    r = RetrievalResult(chunk=scripture_chunk(), score=0.42, retriever=Retriever.FUSED, rank=1)
    assert r.retriever == "fused"


def test_source_note_cannot_be_scripture():
    assert SourceNote(source_type=SourceType.COMMENTARY, label="Matthew Henry on John 3")
    with pytest.raises(ValidationError):
        SourceNote(source_type=SourceType.SCRIPTURE, label="John 3:16")


def test_answer_quotes_translation_of_its_language():
    kjv = Citation(ref=JOHN_3_16, translation=Translation.KJV, verified=True)
    lsg = Citation(ref=JOHN_3_16, translation=Translation.LSG1910, verified=False)
    a = Answer(text="...", language=Language.EN, route=Route.LOOKUP, citations=[kjv])
    assert a.all_verified
    with pytest.raises(ValidationError):
        Answer(text="...", language=Language.EN, route=Route.LOOKUP, citations=[lsg])
    fr = Answer(text="...", language=Language.FR, route=Route.LOOKUP, citations=[lsg])
    assert not fr.all_verified


def test_chat_request_limits():
    assert ChatRequest(message="Qui était Abraham ?").language is None
    with pytest.raises(ValidationError):
        ChatRequest(message="")
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", history=[{"role": "system", "content": "x"}])
