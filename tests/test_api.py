"""Phase 7: the HTTP API and the chat page (FastAPI TestClient; no API key needed)."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from corpusstudy.api.app import LockedSearch, create_app
from corpusstudy.config import Language, Translation
from corpusstudy.generation import LLMUnavailable
from corpusstudy.schemas import (
    Answer,
    ChatResponse,
    Citation,
    Outcome,
    PassageRef,
    Route,
    SourceNote,
    SourceType,
)


class StubAnswerer:
    def __init__(self, fail=False):
        self.requests = []
        self.fail = fail

    def answer(self, request):
        self.requests.append(request)
        if self.fail:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        language = request.language or Language.EN
        answer = Answer(
            text="Peace with God [1].",
            language=language,
            route=Route.RAG,
            citations=[Citation(ref=PassageRef.parse("Rom 5:1"),
                                translation=Translation.KJV if language == Language.EN
                                else Translation.LSG1910, verified=True)],
            sources=[SourceNote(source_type=SourceType.KNOWLEDGE, label="Justification",
                                marker=1, trust_tier="human-reviewed")],
            outcome=Outcome.ANSWERED,
        )  # fmt: skip
        return ChatResponse(answer=answer)


def client(answerer=None):
    return TestClient(create_app(answerer or StubAnswerer()))


def test_chat_returns_answer_citations_and_sources():
    stub = StubAnswerer()
    r = client(stub).post("/chat", json={"message": "Romains 5.1 ?", "language": "fr",
                                         "history": [{"role": "user", "content": "Bonjour"}]})  # fmt: skip
    assert r.status_code == 200
    a = r.json()["answer"]
    assert a["outcome"] == "answered" and a["citations"][0]["display"] == "Romains 5.1"
    assert a["sources"][0] == {"source_type": "knowledge", "label": "Justification",
                               "chunk_id": None, "marker": 1, "trust_tier": "human-reviewed"}  # fmt: skip
    assert stub.requests[0].history[0].content == "Bonjour"


def test_chat_validates_requests_and_reports_a_missing_key():
    c = client()
    assert c.post("/chat", json={"message": ""}).status_code == 422
    assert c.post("/chat", json={"message": "x" * 4001}).status_code == 422
    assert c.post("/chat", json={"message": "hi", "language": "de"}).status_code == 422
    r = client(StubAnswerer(fail=True)).post("/chat", json={"message": "What is grace?"})
    assert r.status_code == 503 and "lookups still work" in r.json()["detail"]


def test_page_and_health():
    c = client()
    page = c.get("/")
    assert page.status_code == 200 and "CorpusStudy" in page.text and "/chat" in page.text
    health = c.get("/health").json()
    assert set(health) == {"index", "api_key", "model", "use_drafts"}


def test_locked_search_passes_calls_and_attributes_through():
    inner = SimpleNamespace(bundle="b", search=lambda text, k=1: [text, k])
    s = LockedSearch(inner)
    assert s.bundle == "b" and s.search("q", k=3) == ["q", 3]


def _bible_built():
    from corpusstudy.corpus import CorpusNotBuilt, bible

    try:
        bible(Translation.LSG1910)
    except CorpusNotBuilt:
        return False
    return True


@pytest.mark.skipif(not _bible_built(), reason="Bible corpus not built")
def test_passage_endpoint_uses_the_language_translation():
    c = client()
    fr = c.get("/passage", params={"ref": "Psaume 51.3", "lang": "fr"}).json()
    assert fr["translation"] == "LSG1910" and fr["verses"][0]["text"].startswith("O Dieu!")
    en = c.get("/passage", params={"ref": "Psalm 51:1", "lang": "en"}).json()
    assert en["verses"][0]["text"].startswith("Have mercy upon me")
    assert c.get("/passage", params={"ref": "John 3:99"}).status_code == 404
    assert c.get("/passage", params={"ref": "not a ref"}).status_code == 422
