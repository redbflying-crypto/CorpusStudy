"""Answer a question: route, retrieve, generate, validate, repair once, or refuse.

    pastoral   a safety message with crisis contacts comes first, always (written here, not
               by the model); a gentle answer follows when the model is available
    lookup     the passage is read from the Bible in the answer's translation; no model call
    others     retrieval -> evidence -> model -> citation validator. An answer that fails
               validation gets one repair round; if it still fails, it is not shown and the
               retrieved passages are given instead.

Commentary and knowledge markers ([E3]) are renumbered [1], [2], ... in order of use, and each
becomes a SourceNote (with the OKF trust tier for knowledge).
"""

from __future__ import annotations

import re
from typing import Any

from corpusstudy.config import Language, get_settings
from corpusstudy.generation.evidence import Evidence, pack
from corpusstudy.generation.llm import LLM, LLMUnavailable, Reply
from corpusstudy.generation.prompts import repair_message, user_message
from corpusstudy.indexing.analysis import detect_language
from corpusstudy.okf.profile import check_concept
from corpusstudy.router import Routing, route
from corpusstudy.schemas import (
    Answer,
    ChatRequest,
    ChatResponse,
    Citation,
    Outcome,
    Route,
    SourceNote,
    SourceType,
)
from corpusstudy.validation import Report, validate
from corpusstudy.validation.citations import MARKER
from corpusstudy.versification import versification

MAX_LOOKUP_REFS = 3
MAX_LOOKUP_VERSES = 40

SAFETY = {
    Language.EN: {
        "self-harm": (
            "**If you are thinking about ending your life or hurting yourself, please reach "
            "out now.** Call your local emergency number (911 in the US and Canada, 112 in "
            "Europe) or a crisis line: 988 in the US and Canada, 3114 in France (suicide "
            "prevention, 24/7), 116 123 (Samaritans) in the UK and Ireland. Please also tell "
            "someone you trust, such as a friend, a family member or your pastor. You are not "
            "alone."
        ),
        "abuse": (
            "**If someone is hurting you, your safety comes first.** If you are in danger, "
            "call your local emergency number (911 in the US and Canada, 112 in Europe). In "
            "the US the National Domestic Violence Hotline is 1-800-799-7233 and Childhelp "
            "is 1-800-422-4453; in France call 3919 (violence against women) or 119 "
            "(children at risk). Please also tell someone you trust, such as a friend, a "
            "family member or your pastor. What is happening to you is not your fault."
        ),
        "despair": (
            "**I'm sorry you are carrying this.** If you feel you cannot go on, please reach "
            "out now: call your local emergency number, or a crisis line such as 988 in the "
            "US and Canada, 3114 in France, or 116 123 (Samaritans) in the UK and Ireland. "
            "Please also tell someone you trust, such as a friend, a family member or your "
            "pastor."
        ),
    },
    Language.FR: {
        "self-harm": (
            "**Si vous pensez à mettre fin à vos jours ou à vous faire du mal, demandez de "
            "l'aide maintenant.** Appelez le 15 ou le 112 (urgences), ou le 3114, numéro "
            "national de prévention du suicide (24h/24, 7j/7). En Belgique, appelez le 0800 "
            "32 123 ; en Suisse, le 143 ; au Canada, le 988. Parlez-en aussi à une personne "
            "de confiance : un proche, un ami ou votre pasteur. Vous n'êtes pas seul."
        ),
        "abuse": (
            "**Si quelqu'un vous fait du mal, votre sécurité passe d'abord.** En cas de "
            "danger, appelez le 17 (police) ou le 112. En France, le 3919 (Violences Femmes "
            "Info) et le 119 (enfance en danger) sont gratuits et anonymes. Parlez-en aussi à "
            "une personne de confiance : un proche, un ami ou votre pasteur. Ce qui vous "
            "arrive n'est pas de votre faute."
        ),
        "despair": (
            "**Je suis désolé que vous portiez cela.** Si vous sentez que vous n'en pouvez "
            "plus, demandez de l'aide maintenant : appelez le 15 ou le 112, ou le 3114 "
            "(prévention du suicide, 24h/24). Parlez-en aussi à une personne de confiance : "
            "un proche, un ami ou votre pasteur."
        ),
    },
}

_TEXT = {
    Language.EN: {
        "refused": "I could not verify every citation in the answer I drafted, so I am not "
        "showing it. Here are the passages I found for your question:",
        "related": "Related commentary and notes",
        "declined": "I'm not able to help with that request.",
        "truncated": "(first {n} verses shown)",
        "title": "title",
        "invalid": "not found in the",
        "unavailable": "The answer service is not available right now, so I can only show "
        "the message above.",
    },
    Language.FR: {
        "refused": "Je n'ai pas pu vérifier toutes les citations de la réponse que j'ai "
        "rédigée, je ne l'affiche donc pas. Voici les passages trouvés pour votre question :",
        "related": "Commentaires et notes associés",
        "declined": "Je ne peux pas répondre à cette demande.",
        "truncated": "({n} premiers versets)",
        "title": "titre",
        "invalid": "introuvable dans la",
        "unavailable": "Le service de réponse n'est pas disponible pour le moment ; je ne peux "
        "afficher que le message ci-dessus.",
    },
}


def safety_message(language: Language, concern: str) -> str:
    return SAFETY[language].get(concern, SAFETY[language]["despair"])


class Answerer:
    def __init__(self, search: Any = None, llm: LLM | None = None, *, k: int = 12,
                 use_drafts: bool | None = None):  # fmt: skip
        if search is None:
            from corpusstudy.retrieval.search import Search

            search = Search(use_drafts=use_drafts)
        self.search = search
        self._llm = llm
        self.k = k
        self._concept_errors: dict[str, list[str]] = {}

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = LLM()
        return self._llm

    # ------------------------------------------------------------------ entry point

    def answer(self, request: ChatRequest) -> ChatResponse:
        language = request.language or detect_language(request.message)
        routing = route(request.message, language, self.search)
        if routing.route == Route.LOOKUP:
            return ChatResponse(answer=self._lookup(routing))

        preface = safety_message(language, routing.concern) if routing.concern else ""
        evidence = pack(self.search.search(request.message, language, k=self.k))
        try:
            llm = self.llm
        except LLMUnavailable as exc:
            if not preface:
                raise
            text = f"{preface}\n\n{_TEXT[language]['unavailable']}"
            answer = Answer(text=text, language=language, route=routing.route,
                            outcome=Outcome.SAFETY)  # fmt: skip
            return ChatResponse(answer=answer, warnings=[str(exc)])

        messages = _history(request)
        messages.append({"role": "user", "content": user_message(
            request.message, language, evidence, concern=routing.concern)})  # fmt: skip
        reply = llm.ask(messages)
        outcome, warnings = Outcome.ANSWERED, []
        report = self._check(reply, language, evidence)
        if not reply.declined and (report is None or not report.ok):
            problems = [p.message for p in report.problems] if report else [
                "the reply was empty or not the JSON shape requested"]  # fmt: skip
            warnings += problems
            messages.append({"role": "assistant", "content": _replayable(reply.content)})
            messages.append({"role": "user", "content": repair_message(problems)})
            reply = llm.ask(messages)
            report = self._check(reply, language, evidence)
            outcome = Outcome.REPAIRED

        if reply.declined:
            text = _join(preface, _TEXT[language]["declined"])
            answer = Answer(text=text, language=language, route=routing.route,
                            outcome=Outcome.DECLINED)  # fmt: skip
            return ChatResponse(answer=answer, warnings=warnings)
        if report is None or not report.ok or reply.answer is None:
            if report:
                warnings += [p.message for p in report.problems]
            answer = self._refused(routing, evidence, preface)
            return ChatResponse(answer=answer, warnings=warnings)

        text, sources = _number_sources(reply.answer, report, evidence)
        answer = Answer(
            text=_join(preface, text),
            language=language,
            route=routing.route,
            citations=report.citations,
            sources=sources,
            interpretive=reply.interpretive,
            outcome=outcome,
        )
        return ChatResponse(answer=answer, warnings=warnings if outcome == Outcome.REPAIRED else [])

    # ------------------------------------------------------------------ helpers

    def _check(self, reply: Reply, language: Language, evidence: list[Evidence]) -> Report | None:
        if reply.answer is None:
            return None
        return validate(reply.answer, language, evidence, knowledge_check=self._concept_check)

    def _concept_check(self, concept_id: str) -> list[str]:
        if concept_id not in self._concept_errors:
            bundle = self.search.bundle
            concept = bundle.concepts.get(concept_id)
            if concept is None:
                errors = ["not in the knowledge bundle"]
            else:
                errors = [i.message for i in check_concept(concept, bundle) if i.level == "error"]
            self._concept_errors[concept_id] = errors
        return self._concept_errors[concept_id]

    def _lookup(self, routing: Routing) -> Answer:
        from corpusstudy.corpus import bible

        language = routing.language
        tr = get_settings().translation_for(language)
        text = _TEXT[language]
        blocks, citations = [], []
        for bad in routing.invalid_refs:
            ref_text, _, reason = bad.partition(" (")
            blocks.append(f"**{ref_text}**: {text['invalid']} {tr.full_name} ({reason}")
        for ref in routing.refs[:MAX_LOOKUP_REFS]:
            heading = f"**{ref.display(language)}** · {tr.full_name}"
            problems = versification(tr).check_passage(ref)
            if problems:
                blocks.append(f"{heading}: {problems[0]}")
                citations.append(Citation(ref=ref, translation=tr, verified=False))
                continue
            verses = bible(tr).passage(ref)
            shown = verses[:MAX_LOOKUP_VERSES]
            lines = [f"**{v.ref.verse if v.ref.verse else text['title']}** {v.text}"
                     for v in shown]  # fmt: skip
            body = "\n".join(f"> {line}" for line in lines)
            if len(verses) > len(shown):
                body += "\n\n" + text["truncated"].format(n=MAX_LOOKUP_VERSES)
            blocks.append(f"{heading}\n\n{body}")
            quote = " ".join(v.text for v in shown)
            citations.append(Citation(ref=ref, translation=tr, quote=quote, verified=True))
        return Answer(text="\n\n".join(blocks), language=language, route=Route.LOOKUP,
                      citations=citations, outcome=Outcome.LOOKUP)  # fmt: skip

    def _refused(self, routing: Routing, evidence: list[Evidence], preface: str) -> Answer:
        language = routing.language
        tr = get_settings().translation_for(language)
        scripture = [e for e in evidence if e.kind == SourceType.SCRIPTURE
                     and e.chunk.translation == tr][:3]  # fmt: skip
        others = [e for e in evidence if e.kind != SourceType.SCRIPTURE][:3]
        parts = [_TEXT[language]["refused"]]
        parts += [f"**{e.label}**\n> {e.text}" for e in scripture]
        if others:
            parts.append(f"{_TEXT[language]['related']}: " + "; ".join(e.label for e in others))
        citations = [Citation(ref=e.chunk.refs[0], translation=tr, quote=e.text, verified=True)
                     for e in scripture if e.chunk.refs]  # fmt: skip
        sources = [_source(e) for e in others]
        return Answer(text=_join(preface, "\n\n".join(parts)), language=language,
                      route=routing.route, citations=citations, sources=sources,
                      outcome=Outcome.REFUSED)  # fmt: skip


def _join(preface: str, text: str) -> str:
    return f"{preface}\n\n{text}" if preface else text


def _history(request: ChatRequest) -> list[dict[str, Any]]:
    turns = [{"role": t.role, "content": t.content} for t in request.history]
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


def _replayable(content: list[Any]) -> list[Any]:
    """The assistant's blocks as sent back in the repair turn (thinking blocks unchanged)."""
    return [b.to_dict() if hasattr(b, "to_dict") else b for b in content]


def _source(e: Evidence, marker: int | None = None) -> SourceNote:
    return SourceNote(source_type=e.kind, label=e.label, chunk_id=e.chunk.id, marker=marker,
                      trust_tier=e.trust_tier)  # fmt: skip


def _number_sources(
    text: str, report: Report, evidence: list[Evidence]
) -> tuple[str, list[SourceNote]]:
    """Rewrite [E3] markers as [1], [2], ... in order of first use; one SourceNote each."""
    by_key = {e.key: e for e in evidence if e.key}
    numbers = {key: n for n, key in enumerate(report.markers, 1)}

    def renumber(m: re.Match[str]) -> str:
        keys = re.split(r"\s*,\s*", m.group(1))
        return "".join(f"[{numbers[k]}]" for k in keys)

    sources = [_source(by_key[k], numbers[k]) for k in report.markers]
    return MARKER.sub(renumber, text), sources
