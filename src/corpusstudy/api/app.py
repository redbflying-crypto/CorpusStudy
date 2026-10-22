"""The HTTP API and the chat page.

    GET  /          the chat page
    POST /chat      ChatRequest -> ChatResponse (answer, verified citations, sources)
    GET  /passage   ?ref=John 3:16&lang=en -> the passage text in that language's translation
    GET  /health    index state, whether an API key is set, model, drafts setting

Lookups and the pastoral safety message work without an API key; other questions return
503 until ANTHROPIC_API_KEY is set. The service is for one person on their own machine, so
searches share one SQLite connection behind a lock.
"""

from __future__ import annotations

import threading
from importlib.resources import files
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from corpusstudy.config import Language, get_settings
from corpusstudy.schemas import ChatRequest, ChatResponse, PassageRef, parse_reference


class LockedSearch:
    """A Search whose calls are serialised (one SQLite connection, many request threads)."""

    def __init__(self, search: Any):
        self._search = search
        self._lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._search, name)
        if not callable(attr):
            return attr

        def locked(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                return attr(*args, **kwargs)

        return locked


def create_app(answerer: Any = None) -> FastAPI:
    """The app. `answerer` (a generation.Answerer) is built on first use when not given."""
    app = FastAPI(title="CorpusStudy", version="0.1.0")
    state: dict[str, Any] = {"answerer": answerer}
    build_lock = threading.Lock()

    def get_answerer() -> Any:
        with build_lock:
            if state["answerer"] is None:
                from corpusstudy.generation import Answerer
                from corpusstudy.retrieval.search import IndexNotBuilt, Search

                try:
                    state["answerer"] = Answerer(LockedSearch(Search()))
                except IndexNotBuilt as exc:
                    raise HTTPException(503, str(exc)) from exc
            return state["answerer"]

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return files("corpusstudy.api").joinpath("static/index.html").read_text("utf-8")

    @app.post("/chat")
    def chat(request: ChatRequest) -> ChatResponse:
        from corpusstudy.generation import LLMUnavailable

        try:
            return get_answerer().answer(request)
        except LLMUnavailable as exc:
            raise HTTPException(503, f"{exc}. Verse lookups still work.") from exc

    @app.get("/passage")
    def passage(ref: str = Query(min_length=1, max_length=100),
                lang: Language = Language.EN) -> dict[str, Any]:  # fmt: skip
        from corpusstudy.corpus import CorpusNotBuilt, bible
        from corpusstudy.versification import versification

        try:
            p: PassageRef = parse_reference(ref)
        except ValueError as exc:
            raise HTTPException(422, f"not a Bible reference: {ref!r}") from exc
        tr = get_settings().translation_for(lang)
        if problems := versification(tr).check_passage(p):
            raise HTTPException(404, problems[0])
        try:
            verses = bible(tr).passage(p)[:60]
        except CorpusNotBuilt as exc:
            raise HTTPException(503, str(exc)) from exc
        return {
            "ref": p.display(lang),
            "translation": str(tr),
            "verses": [{"verse": v.ref.verse, "text": v.text} for v in verses],
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        from corpusstudy.indexing.build import index_status
        from corpusstudy.indexing.store import index_path

        settings = get_settings()
        return {
            "index": index_status(index_path()),
            "api_key": settings.has_api_key,
            "model": settings.llm_model,
            "use_drafts": settings.use_drafts,
        }

    return app
