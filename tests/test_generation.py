"""Phase 6: router, citation validator and answer pipeline (offline, with a fake API client)."""

import json
from types import SimpleNamespace

import pytest

from corpusstudy.config import Language, Translation
from corpusstudy.generation import Answerer, LLMUnavailable, safety_message
from corpusstudy.generation.evidence import Evidence, pack
from corpusstudy.generation.llm import FALLBACK_BETA, LLM
from corpusstudy.generation.prompts import SYSTEM, user_message
from corpusstudy.router import pastoral_concern, route
from corpusstudy.schemas import (
    ChatRequest,
    ChatTurn,
    Chunk,
    Outcome,
    PassageRef,
    RetrievalResult,
    Retriever,
    Route,
    SourceType,
)
from corpusstudy.validation import validate

ROM5 = "Therefore being justified by faith, we have peace with God through our Lord Jesus Christ:"
ROM5_FR = ("Étant donc justifiés par la foi, nous avons la paix avec Dieu par notre Seigneur "
           "Jésus-Christ,")  # fmt: skip
HENRY = ("It is free grace, mere mercy; there is nothing in us to deserve such favours. It "
         "comes freely unto us, but Christ bought it, and paid the price.")  # fmt: skip
NOTE = "Justification is God's verdict declaring the ungodly righteous for Christ's sake."


def chunk(kind, text, **kw):
    defaults = {
        "scripture": dict(source_type=SourceType.SCRIPTURE, translation=Translation.KJV,
                          language=Language.EN, refs=[PassageRef.parse("Rom 5:1")]),
        "scripture_fr": dict(source_type=SourceType.SCRIPTURE, translation=Translation.LSG1910,
                             language=Language.FR, refs=[PassageRef.parse("Rom 5:1")]),
        "commentary": dict(source_type=SourceType.COMMENTARY, work="henry", language=Language.EN,
                           refs=[PassageRef.parse("Rom 3:21-26")]),
        "knowledge": dict(source_type=SourceType.KNOWLEDGE, language=Language.EN,
                          concept_id="doctrines/justification",
                          metadata={"title": "Justification by Faith", "section": "Definition",
                                    "trust_tier": "human-reviewed"}),
    }[kind]  # fmt: skip
    return Chunk(id=kw.pop("id", f"{kind}:1"), text=text, **{**defaults, **kw})


def evidence(language=Language.EN):
    scripture = chunk("scripture" if language == Language.EN else "scripture_fr",
                      ROM5 if language == Language.EN else ROM5_FR)  # fmt: skip
    results = [scripture, chunk("commentary", HENRY), chunk("knowledge", NOTE)]
    return pack([RetrievalResult(chunk=c, score=1.0, retriever=Retriever.KEYWORD, rank=i)
                 for i, c in enumerate(results, 1)])  # fmt: skip


def no_bible(ref, language):
    return None


def problems(text, language=Language.EN, **kw):
    report = validate(text, language, evidence(language), passage_text=no_bible, **kw)
    return [p.message for p in report.problems]


# --------------------------------------------------------------------------- router


class Concepts:
    def __init__(self, names):
        self.names = names

    def named_concepts(self, text, language=None):
        return [cid for word, cid in self.names.items() if word in text.lower()]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("John 3:16", Route.LOOKUP),
        ("What does John 3:16 say?", Route.LOOKUP),
        ("Que dit Jean 3.16 ?", Route.LOOKUP),
        ("Psalm 23", Route.LOOKUP),
        ("Explain John 3:16", Route.RAG),
        ("How should I pray?", Route.RAG),
        ("What is justification?", Route.KNOWLEDGE),
        ("What does Romans 4 say about justification?", Route.HYBRID),
        ("I want to die", Route.PASTORAL),
        ("Je n'en peux plus, je veux mourir", Route.PASTORAL),
        ("My husband hits me, what does God say?", Route.PASTORAL),
    ],
)
def test_routes(text, expected):
    assert (
        route(text, search=Concepts({"justification": "doctrines/justification"})).route == expected
    )


def test_pastoral_concerns_and_language():
    assert pastoral_concern("I keep thinking about suicide") == "self-harm"
    assert pastoral_concern("j'ai été violée") == "abuse"
    assert pastoral_concern("I feel hopeless") == "despair"
    assert pastoral_concern("What does grace mean?") is None
    assert route("Je n'en peux plus, je veux mourir").language == Language.FR


# --------------------------------------------------------------------------- validator


def test_valid_answer_passes_and_lists_citations():
    text = (f'Paul writes that "being justified by faith, we have peace with God" (Romans 5:1). '
            f'Henry: "Christ bought it, and paid the price" [E1]. In short, {NOTE} [E2]')  # fmt: skip
    report = validate(text, Language.EN, evidence(), passage_text=no_bible)
    assert report.ok, report.problems
    assert [c.ref.osis for c in report.citations] == ["Rom.5.1"]
    assert report.citations[0].verified and report.citations[0].translation == Translation.KJV
    assert report.markers == ["E1", "E2"]


def test_references_must_exist_in_the_answer_translation():
    assert problems("See John 3:99.") == ["John 3:99: John 3 has 36 verses, not 99"]
    assert problems("See Romans 99:1.") == [
        "Romans 99:1 is not a valid reference in KJV (Romans has 16 chapters)"]  # fmt: skip
    # "the numbers 3 and 4" is not Numbers 3.
    assert problems("Read the numbers 3 and 4 carefully.") == []


def test_quotations_must_be_exact_and_from_a_cited_source():
    assert problems('Henry: "Christ earned it, and paid the price" [E1].') == [
        'quotation "christ earned it, and paid the price" does not occur in the sources provided']  # fmt: skip
    # The right words without the marker of the source they come from.
    assert problems('He said "Christ bought it, and paid the price".') == [
        'quotation "christ bought it, and paid the price" is not in the sources cited in its '
        "paragraph"]  # fmt: skip
    # Ellipses split a quotation; each fragment must occur. Short quotes are not checked.
    assert problems('"It is free grace ... Christ bought it" [E1], by "grace" alone.') == []
    assert problems('"It is free grace ... Christ earned it" [E1]') == [
        'quotation "christ earned it" does not occur in the sources provided']  # fmt: skip


def test_scripture_quotes_follow_the_answer_language():
    fr_ok = "Paul écrit : « Étant donc justifiés par la foi, nous avons la paix » (Romains 5.1)."
    assert problems(fr_ok, Language.FR) == []
    # A French rendering of English commentary is not a quotation.
    fr_bad = "Henry écrit : « Christ l'a acheté et en a payé le prix » [E1]."
    assert len(problems(fr_bad, Language.FR)) == 1


def test_markers_and_knowledge_provenance():
    assert problems("As noted [E9].") == ["[E9] does not name a provided source"]
    assert problems("Grace [E1, E2].") == []
    bad = problems("Justified [E2].", knowledge_check=lambda cid: ["footnote [^x] has no source"])
    assert bad == ["doctrines/justification: footnote [^x] has no source"]


# --------------------------------------------------------------------------- pipeline


class FakeSearch:
    def __init__(self, language=Language.EN):
        self.language = language
        self.bundle = SimpleNamespace(concepts={})
        self.calls = []

    def named_concepts(self, text, language=None):
        return []

    def search(self, text, language=None, k=10):
        self.calls.append((text, language, k))
        return [RetrievalResult(chunk=e.chunk, score=1.0, retriever=Retriever.KEYWORD, rank=i)
                for i, e in enumerate(evidence(language or self.language), 1)]  # fmt: skip


def reply(answer=None, interpretive=False, stop="end_turn", raw=None):
    text = raw if raw is not None else json.dumps({"answer": answer, "interpretive": interpretive})
    block = SimpleNamespace(type="text", text=text, to_dict=lambda: {"type": "text", "text": text})
    return SimpleNamespace(stop_reason=stop, content=[block], model="claude-opus-5-5")


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0)


GOOD = ('Paul says "being justified by faith, we have peace with God" (Romans 5:1). '
        'Henry adds that "Christ bought it, and paid the price" [E1]. ' + NOTE + " [E2]")  # fmt: skip
BAD = 'Henry: "Christ earned it" [E1] (Romans 5:1).'


def answerer(*replies, language=Language.EN):
    client = FakeClient(*replies)
    a = Answerer(FakeSearch(language), LLM(client))
    a._concept_errors["doctrines/justification"] = []
    return a, client


def ask(a, text, language=None, **kw):
    return a.answer(ChatRequest(message=text, language=language, **kw))


def test_answer_is_validated_and_sources_renumbered():
    # Knowledge (E2) is cited before commentary (E1): numbering follows first use.
    text = (f"{NOTE} [E2] Paul says \"being justified by faith, we have peace with God\" "
            f"(Romans 5:1). Henry adds that \"Christ bought it, and paid the price\" [E1].")  # fmt: skip
    a, client = answerer(reply(text, True))
    response = ask(a, "What does Romans 5:1 teach?")
    ans = response.answer
    assert ans.outcome == Outcome.ANSWERED and ans.route == Route.RAG and ans.interpretive
    assert "[1]" in ans.text and "[2]" in ans.text and "[E" not in ans.text
    assert [(s.marker, s.source_type, s.trust_tier) for s in ans.sources] == [
        (1, SourceType.KNOWLEDGE, "human-reviewed"), (2, SourceType.COMMENTARY, None)]  # fmt: skip
    assert ans.all_verified and [c.ref.osis for c in ans.citations] == ["Rom.5.1"]
    call = client.calls[0]
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["output_config"]["effort"] == "medium"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["system"][0]["text"] == SYSTEM and call["system"][0]["cache_control"]
    assert "temperature" not in call and "thinking" not in call


def test_failed_validation_gets_one_repair():
    a, client = answerer(reply(BAD), reply(GOOD))
    response = ask(a, "What does Romans 5:1 teach?")
    assert response.answer.outcome == Outcome.REPAIRED
    assert any("christ earned it" in w for w in response.warnings)
    second = client.calls[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert "could not be verified" in second[-1]["content"]


def test_an_answer_that_stays_invalid_is_refused():
    a, _ = answerer(reply(BAD), reply(BAD))
    ans = ask(a, "What does Romans 5:1 teach?").answer
    assert ans.outcome == Outcome.REFUSED
    assert "could not verify" in ans.text and ROM5 in ans.text and "earned" not in ans.text
    assert ans.all_verified and ans.sources


def test_unusable_json_is_repaired_and_declines_are_reported():
    a, _ = answerer(reply(raw="not json"), reply(GOOD))
    assert ask(a, "What does Romans 5:1 teach?").answer.outcome == Outcome.REPAIRED
    a, _ = answerer(reply(stop="refusal", raw=""))
    ans = ask(a, "What does Romans 5:1 teach?").answer
    assert ans.outcome == Outcome.DECLINED and ans.text


def test_pastoral_safety_message_comes_first():
    a, client = answerer(reply(GOOD))
    ans = ask(a, "I want to die. Is there any peace with God?").answer
    assert ans.route == Route.PASTORAL and ans.outcome == Outcome.ANSWERED
    assert ans.text.startswith(safety_message(Language.EN, "self-harm"))
    assert "<pastoral>" in client.calls[0]["messages"][-1]["content"]


def test_pastoral_without_api_key_still_gets_the_safety_message(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from corpusstudy import config

    monkeypatch.setattr(config, "get_settings",
                        lambda: config.Settings(_env_file=None))  # fmt: skip
    monkeypatch.setattr("corpusstudy.generation.llm.get_settings",
                        lambda: config.Settings(_env_file=None))  # fmt: skip
    a = Answerer(FakeSearch(Language.FR))
    ans = ask(a, "Je n'en peux plus, je veux mourir").answer
    assert ans.outcome == Outcome.SAFETY and ans.language == Language.FR
    assert "3114" in ans.text
    with pytest.raises(LLMUnavailable):
        ask(Answerer(FakeSearch()), "What is grace?")


def test_lookup_needs_no_model_call():
    a, client = answerer()
    ans = ask(a, "Que dit Jean 3.99 ?").answer
    assert ans.route == Route.LOOKUP and ans.outcome == Outcome.LOOKUP and not client.calls
    assert ans.citations[0].translation == Translation.LSG1910 and not ans.all_verified


def test_history_is_sent_and_prompt_carries_language_and_evidence():
    a, client = answerer(reply(GOOD))
    history = [ChatTurn(role="assistant", content="Hello"), ChatTurn(role="user", content="Hi"),
               ChatTurn(role="assistant", content="How can I help?")]  # fmt: skip
    ask(a, "What does Romans 5:1 teach?", history=history)
    messages = client.calls[0]["messages"]
    assert [m["role"] for m in messages] == [
        "user",
        "assistant",
        "user",
    ]  # leading assistant dropped
    prompt = user_message("Q?", Language.FR, evidence(Language.FR))
    assert "<answer_language>French</answer_language>" in prompt
    assert 'id="E1"' in prompt and 'status="reviewed"' in prompt and "<scripture" in prompt


def test_evidence_keys_skip_scripture():
    keys = [e.key for e in evidence()]
    assert keys == [None, "E1", "E2"]
    assert isinstance(evidence()[1], Evidence) and evidence()[1].label.startswith("Matthew Henry")


# --------------------------------------------------------------------------- integration


def _corpus_ready():
    from corpusstudy.corpus import CorpusNotBuilt, bible
    from corpusstudy.indexing.store import index_path

    try:
        bible(Translation.KJV)
        bible(Translation.LSG1910)
    except CorpusNotBuilt:
        return False
    return index_path().exists()


needs_corpus = pytest.mark.skipif(not _corpus_ready(), reason="corpus or index not built")


@needs_corpus
def test_quotes_are_checked_against_the_bible_itself():
    ok = 'Jesus said "God so loved the world, that he gave his only begotten Son" (John 3:16).'
    assert validate(ok, Language.EN, []).ok
    bad = 'Jesus said "God so loved the world that he sent his Son" (John 3:16).'
    assert not validate(bad, Language.EN, []).ok
    # In French the Louis Segond text and numbering apply.
    fr = "« Car Dieu a tant aimé le monde qu'il a donné son Fils unique » (Jean 3.16)."
    assert validate(fr, Language.FR, []).ok
    assert not validate(fr.replace("Jean 3.16", "Jean 3.99"), Language.FR, []).ok


class EchoClient(FakeClient):
    """Answers by quoting the first verse and the first commentary item it was given."""

    def _create(self, **kwargs):
        import re as _re
        from html import unescape

        self.calls.append(kwargs)
        prompt = kwargs["messages"][-1]["content"]
        # The first verse line: "<reference> <text>" (the reference is not part of the text).
        verse = _re.search(r'<scripture ref="[^"]+">\n(\S.*?\d+[:.]\d+) ([^\n<]+)', prompt)
        source = _re.search(r'<source id="(E\d+)"[^>]*>([^<]+)<', prompt)
        text = f'{verse.group(1)} reads: "{unescape(verse.group(2)).split(".")[0]}".'
        if source:  # commentary or knowledge was retrieved
            words = " ".join(unescape(source.group(2)).split()[:6])
            text += f' A commentator writes "{words}" [{source.group(1)}].'
        return reply(text)


@needs_corpus
@pytest.mark.parametrize("question,language", [
    ("What does Romans 5:1 teach about peace with God?", Language.EN),
    ("Que dit Romains 5.1 sur la paix avec Dieu ?", Language.FR),
])  # fmt: skip
def test_end_to_end_with_the_real_index(question, language):
    from corpusstudy.retrieval.search import Search

    client = EchoClient()
    a = Answerer(Search(), LLM(client))
    ans = a.answer(ChatRequest(message=question, language=language)).answer
    assert ans.outcome == Outcome.ANSWERED, ans.text
    assert ans.language == language and ans.all_verified and ans.citations
    from corpusstudy.commentary import commentary

    if commentary("henry-concise") is not None:  # not imported in CI
        assert ans.sources and ans.sources[0].marker == 1


def test_block_quotes_are_quotations():
    ok = f"Paul writes:\n\n> **1** {ROM5}\n> — Romans 5:1\n\nThat is the heart of it."
    assert problems(ok) == []
    bad = "Paul writes:\n\n> **1** Being justified by works, we have peace with God.\n"
    assert problems(bad) == [
        'quotation "being justified by works, we have peace with god" does not occur in the '
        "sources provided"]  # fmt: skip
    # Commentary in a block quote still needs its marker.
    assert problems(f"> {HENRY} [E1]") == []
    assert len(problems(f"> {HENRY}")) == 1


@needs_corpus
def test_lookup_and_refusal_texts_pass_the_validator():
    from corpusstudy.retrieval.search import Search

    a = Answerer(Search(), LLM(FakeClient()))
    for question, lang in [("Romans 8:28-30", Language.EN), ("Psaume 23", Language.FR)]:
        ans = a.answer(ChatRequest(message=question, language=lang)).answer
        assert ans.outcome == Outcome.LOOKUP
        assert validate(ans.text, lang, []).ok, validate(ans.text, lang, []).problems


def test_scripture_evidence_is_quotable_without_verse_markers():
    c = chunk("scripture", "[1] Therefore being justified by faith, [2] By whom also we have access",
              refs=[PassageRef.parse("Rom 5:1-2")])  # fmt: skip
    e = pack([RetrievalResult(chunk=c, score=1.0, retriever=Retriever.KEYWORD, rank=1)])[0]
    assert e.text == "Therefore being justified by faith, By whom also we have access"
    assert [r.osis for r, _ in e.verses()] == ["Rom.5.1", "Rom.5.2"]
    prompt = user_message("q", Language.EN, [e])
    assert "Romans 5:1 Therefore being justified by faith," in prompt and "[1]" not in prompt
    title = chunk("scripture", "To the chief Musician. [1] Have mercy upon me",
                  refs=[PassageRef.parse("Ps 51:1")])  # fmt: skip
    e = pack([RetrievalResult(chunk=title, score=1.0, retriever=Retriever.KEYWORD, rank=1)])[0]
    assert [r.verse for r, _ in e.verses()] == [0, 1]


def test_invalid_references_are_lookups_with_a_reason():
    r = route("Matthieu 29.1")
    assert r.route == Route.LOOKUP and r.invalid_refs == [
        "Matthieu 29.1 (Matthieu compte 28 chapitres)"
    ]
    assert route("Romans 99:1").invalid_refs == ["Romans 99:1 (Romans has 16 chapters)"]
    assert route("I've been thinking about killing myself").concern == "self-harm"
    assert route("I wish I were dead").concern == "self-harm"
    assert route("When will this study end? My notes are long.").concern is None


# --------------------------------------------------------------------------- answer evaluation


@needs_corpus
@pytest.mark.parametrize("name", ["answers.yaml", "answers_holdout.yaml"])
def test_answer_evaluation_offline_targets(name):
    from corpusstudy.config import REPO_ROOT
    from corpusstudy.generation.evaluation import (
        evaluate_offline,
        load_cases,
        summarise,
        targets_met,
    )
    from corpusstudy.retrieval.search import Search

    results = evaluate_offline(load_cases(REPO_ROOT / "eval" / name), Search(use_drafts=True))
    failures = [(r.case["q"], r.checks, r.notes) for r in results if not r.ok]
    assert targets_met(summarise(results)), failures


def test_answer_evaluation_set_is_well_formed():
    from corpusstudy.config import REPO_ROOT
    from corpusstudy.generation.evaluation import load_cases

    cases = load_cases(REPO_ROOT / "eval" / "answers.yaml")
    assert len(cases) >= 50
    assert {c["lang"] for c in cases} == {"en", "fr"}
    assert {c["route"] for c in cases} == {r.value for r in Route}
    for c in cases:
        assert c["gist"] and c["category"], c
        for ref in c.get("refs", []):
            PassageRef.from_osis(ref)
