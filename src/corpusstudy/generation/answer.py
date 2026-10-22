"""Answer a question: route, retrieve, generate, validate, repair once, or refuse.

    refuse     a request to demean or harass people: declined with an offer of respectful
               discussion (fixed text, no model call)
    pastoral   a safety message with crisis contacts comes first, always (written here, not
               by the model); a gentle answer follows when the model is available
    lookup     the passage is read from the Bible in the answer's translation (or the loaded
               translation asked for); no model call
    compare    evidence searched book by book; crossref: the passage and the passages the
               commentaries and knowledge link to it; others: hybrid retrieval
    then       evidence -> model -> citation validator. An answer that fails validation gets
               one repair round; if it still fails, it is not shown: it says it cannot verify
               the passage and gives the retrieved passages instead.

Notes written here, not by the model, come before the answer: the safety message, a
medical/legal/financial note, and a note when a translation asked for is not loaded.
Commentary and knowledge markers ([E3]) are renumbered [1], [2], ... in order of use and
each becomes a SourceNote (with the OKF trust tier for knowledge); every reference gets its
translation tag ("John 3:16 (KJV)"); a Sources section is appended. Each answer is logged
(route, outcome, citations, tokens, cost, time; never the question) and carries its usage.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from corpusstudy.canon import BOOKS_BY_OSIS, find_book
from corpusstudy.config import Language, Translation, get_settings
from corpusstudy.generation.evidence import Evidence, pack
from corpusstudy.generation.llm import LLM, LLMUnavailable, Reply
from corpusstudy.generation.prompts import (
    ADVICE,
    CANNOT_VERIFY,
    ROUTE_NOTES,
    TRANSLATION,
    repair_message,
    user_message,
)
from corpusstudy.indexing.analysis import detect_language
from corpusstudy.okf.profile import check_concept
from corpusstudy.retrieval.references import all_references
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
    Usage,
)
from corpusstudy.validation import Report, validate
from corpusstudy.validation.citations import MARKER
from corpusstudy.versification import map_passage, versification

log = logging.getLogger("corpusstudy.answers")

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
        "refused": "The answer I drafted cited passages or quotations I could not check "
        "against the corpus, so I am not showing it. Here are the passages I found:",
        "related": "Related commentary and notes",
        "declined": "I'm not able to help with that request.",
        "refuse": "I can't help with that: CorpusStudy is not for demeaning or harassing "
        "people. If you like, we can look respectfully at what Scripture says on the subject, "
        "for example on loving one's neighbour or on speech that builds others up.",
        "truncated": "(first {n} verses shown)",
        "title": "title",
        "unavailable": "The answer service is not available right now, so I can only show "
        "the message above.",
        "translation": "The {name} is not available here: CorpusStudy has only public-domain "
        "translations, the King James Version (English) and the Louis Segond 1910 (French). "
        "This answer quotes the {loaded}.",
        "advice": {
            "medical": "**A note first:** I can share what Scripture says, but not medical "
            "advice. Please talk with a doctor or another qualified health professional "
            "before changing any treatment.",
            "legal": "**A note first:** I can share what Scripture says, but not legal "
            "advice. For your situation, please consult a lawyer or a legal aid service.",
            "financial": "**A note first:** I can share what Scripture says, but not "
            "financial advice. For decisions about money, please consult a qualified "
            "financial adviser.",
        },
        "sources": "Sources",
        "bible": "Bible",
        "commentary": "Commentary",
        "knowledge": "Knowledge",
        "public_domain": "public domain",
        "draft": "draft, not yet reviewed",
        "reviewed": "reviewed",
    },
    Language.FR: {
        "refused": "La réponse que j'ai rédigée citait des passages ou des citations que je "
        "n'ai pas pu vérifier dans le corpus, je ne l'affiche donc pas. Voici les passages "
        "trouvés :",
        "related": "Commentaires et notes associés",
        "declined": "Je ne peux pas répondre à cette demande.",
        "refuse": "Je ne peux pas vous aider pour cela : CorpusStudy ne sert pas à rabaisser "
        "ou à harceler des personnes. Si vous le souhaitez, nous pouvons regarder avec "
        "respect ce que dit l'Écriture sur ce sujet, par exemple sur l'amour du prochain ou "
        "sur les paroles qui édifient.",
        "truncated": "({n} premiers versets)",
        "title": "titre",
        "unavailable": "Le service de réponse n'est pas disponible pour le moment ; je ne peux "
        "afficher que le message ci-dessus.",
        "translation": "La version « {name} » n'est pas disponible ici : CorpusStudy n'a que "
        "des traductions du domaine public, la King James Version (anglais) et la Louis "
        "Segond 1910 (français). Cette réponse cite la {loaded}.",
        "advice": {
            "medical": "**Une remarque d'abord :** je peux dire ce que dit l'Écriture, mais "
            "pas donner d'avis médical. Parlez-en à un médecin ou à un professionnel de "
            "santé avant de modifier un traitement.",
            "legal": "**Une remarque d'abord :** je peux dire ce que dit l'Écriture, mais pas "
            "donner d'avis juridique. Pour votre situation, consultez un avocat ou un "
            "service d'aide juridique.",
            "financial": "**Une remarque d'abord :** je peux dire ce que dit l'Écriture, mais "
            "pas donner de conseil financier. Pour vos décisions d'argent, consultez un "
            "conseiller financier qualifié.",
        },
        "sources": "Sources",
        "bible": "Bible",
        "commentary": "Commentaire",
        "knowledge": "Connaissance",
        "public_domain": "domaine public",
        "draft": "brouillon, non relu",
        "reviewed": "relu",
    },
}

TAGS = {Translation.KJV: "KJV", Translation.LSG1910: "LSG"}
COMPARE_PER_BOOK = 3


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
        started = time.monotonic()
        routing: Routing | None = None
        try:
            language = request.language or detect_language(request.message)
            routing = route(request.message, language, self.search)
            response = self._answer(request, routing)
        finally:
            usage = self._llm.take_usage() if self._llm is not None else Usage()
        usage.seconds = round(time.monotonic() - started, 2)
        response = response.model_copy(update={"usage": usage})
        _log(routing, response)
        return response

    def _answer(self, request: ChatRequest, routing: Routing) -> ChatResponse:
        language = routing.language
        text = _TEXT[language]
        if routing.route == Route.REFUSE:
            answer = Answer(text=text["refuse"], language=language, route=Route.REFUSE,
                            outcome=Outcome.DECLINED)  # fmt: skip
            return ChatResponse(answer=answer)
        self._refine(request.message, routing)
        if routing.route == Route.LOOKUP:
            return ChatResponse(answer=self._lookup(routing))

        notes = self._notes(routing)
        evidence = self._gather(request.message, routing)
        try:
            llm = self.llm
        except LLMUnavailable as exc:
            if not routing.concern:
                raise
            answer = Answer(text=_join(*notes, text["unavailable"]), language=language,
                            route=routing.route, outcome=Outcome.SAFETY)  # fmt: skip
            return ChatResponse(answer=answer, warnings=[str(exc)])

        messages = _history(request)
        messages.append({"role": "user", "content": user_message(
            request.message, language, evidence, concern=routing.concern,
            notes=self._model_notes(routing))})  # fmt: skip
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
            answer = Answer(text=_join(*notes, text["declined"]), language=language,
                            route=routing.route, outcome=Outcome.DECLINED)  # fmt: skip
            return ChatResponse(answer=answer, warnings=warnings)
        if report is None or not report.ok or reply.answer is None:
            if report:
                warnings += [p.message for p in report.problems]
            answer = self._refused(routing, evidence, notes)
            return ChatResponse(answer=answer, warnings=warnings)

        body, sources = _number_sources(reply.answer, report, evidence)
        body = tag_translations(body, language)
        answer = Answer(
            text=_join(*notes, body, _sources_section(report.citations, sources, language)),
            language=language,
            route=routing.route,
            citations=report.citations,
            sources=sources,
            interpretive=reply.interpretive,
            outcome=outcome,
        )
        return ChatResponse(answer=answer, warnings=warnings if outcome == Outcome.REPAIRED else [])

    # ------------------------------------------------------------------ routing and evidence

    def _refine(self, question: str, routing: Routing) -> None:
        """The optional model-assisted step: refine an open question's route."""
        if routing.route != Route.RAG or not get_settings().llm_router:
            return
        try:
            data = self.llm.classify(question)
        except LLMUnavailable:
            return
        books = [b.osis for name in data.get("books", []) if (b := find_book(str(name)))]
        kind = data.get("route")
        if kind == "compare" and len(set(books)) >= 2:
            routing.route, routing.books = Route.COMPARE, list(dict.fromkeys(books))
        elif kind == "interpretive":
            routing.route = Route.INTERPRETIVE
        elif kind == "crossref" and routing.refs:
            routing.route = Route.CROSSREF
        else:
            return
        routing.reason = f"model-assisted: {kind}"

    def _notes(self, routing: Routing) -> list[str]:
        """Notes shown before the answer, in the answer's language (fixed text)."""
        text = _TEXT[routing.language]
        notes = []
        if routing.concern:
            notes.append(safety_message(routing.language, routing.concern))
        if routing.advice:
            notes.append(text["advice"][routing.advice])
        if routing.unavailable_translation:
            loaded = get_settings().translation_for(routing.language).full_name
            notes.append(text["translation"].format(name=routing.unavailable_translation,
                                                    loaded=loaded))  # fmt: skip
        return notes

    def _model_notes(self, routing: Routing) -> list[str]:
        """Instructions for the model about this route."""
        notes = []
        if routing.route == Route.COMPARE:
            names = ", ".join(BOOKS_BY_OSIS[b].name(routing.language) for b in routing.books)
            notes.append(ROUTE_NOTES["compare"].format(books=names))
        elif routing.route == Route.CROSSREF:
            refs = ", ".join(r.display(routing.language) for r in routing.refs)
            key = "crossref" if routing.links else "crossref_none"
            notes.append(ROUTE_NOTES[key].format(refs=refs))
        elif routing.route == Route.INTERPRETIVE:
            notes.append(ROUTE_NOTES["interpretive"])
        if routing.advice:
            notes.append(ADVICE.format(domain=routing.advice))
        if routing.unavailable_translation:
            notes.append(TRANSLATION.format(name=routing.unavailable_translation))
        return notes

    def _gather(self, question: str, routing: Routing) -> list[Evidence]:
        language = routing.language
        if routing.route == Route.COMPARE:
            results: list[Any] = []
            per_book = [self.search.search(question, language, k=COMPARE_PER_BOOK,
                                           kinds=("bible", "commentary"), books=[book])
                        for book in routing.books]  # fmt: skip
            for i in range(COMPARE_PER_BOOK):  # interleave the books
                results += [hits[i] for hits in per_book if i < len(hits)]
            results += self.search.search(question, language, k=3, kinds=("knowledge",))
            return pack(results)
        if routing.route == Route.CROSSREF:
            return pack(self._cross_references(question, routing))
        return pack(self.search.search(question, language, k=self.k))

    def _cross_references(self, question: str, routing: Routing) -> list[Any]:
        language = routing.language
        tr = get_settings().translation_for(language)
        ref = routing.refs[0]
        kjv = ref if tr == Translation.KJV else map_passage(ref, tr, Translation.KJV)
        linked = self.search.cross_references(kjv, limit=8)
        routing.links = len(linked)
        targets = [t if tr == Translation.KJV else map_passage(t, Translation.KJV, tr)
                   for t, _ in linked]  # fmt: skip
        out: list[Any] = self.search.passage_chunks([ref, *targets], language)
        vias = list(dict.fromkeys(v for _, vs in linked for v in vs if ":" in v))[:4]
        for via in vias:
            out += self.search.excerpt_chunks(via)
        out += self.search.search(question, language, k=4)
        return out

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

        tr = routing.translation or get_settings().translation_for(routing.language)
        language = tr.language  # "John 3:16 in Louis Segond" is answered in French
        text = _TEXT[language]
        blocks, citations = self._notes(routing), []
        for bad in routing.invalid_refs:
            ref_text, _, reason = bad.partition(" (")
            blocks.append(f"**{ref_text}**: {CANNOT_VERIFY[language]} ({reason}")
        for ref in routing.refs[:MAX_LOOKUP_REFS]:
            if routing.language != language:  # the reference was written in the other numbering
                ref = map_passage(ref, get_settings().translation_for(routing.language), tr)
            heading = f"**{ref.display(language)} ({TAGS[tr]})** · {tr.full_name}"
            problems = versification(tr).check_passage(ref)
            if problems:
                blocks.append(f"**{ref.display(language)}**: {CANNOT_VERIFY[language]} "
                              f"({problems[0]})")  # fmt: skip
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

    def _refused(self, routing: Routing, evidence: list[Evidence], notes: list[str]) -> Answer:
        language = routing.language
        tr = get_settings().translation_for(language)
        scripture = [e for e in evidence if e.kind == SourceType.SCRIPTURE
                     and e.chunk.translation == tr][:3]  # fmt: skip
        others = [e for e in evidence if e.kind != SourceType.SCRIPTURE][:3]
        parts = [CANNOT_VERIFY[language], _TEXT[language]["refused"]]
        parts += [f"**{e.label}**\n> {e.text}" for e in scripture]
        if others:
            parts.append(f"{_TEXT[language]['related']}: " + "; ".join(e.label for e in others))
        citations = [Citation(ref=e.chunk.refs[0], translation=tr, quote=e.text, verified=True)
                     for e in scripture if e.chunk.refs]  # fmt: skip
        sources = [_source(e) for e in others]
        return Answer(text=_join(*notes, *parts), language=language,
                      route=routing.route, citations=citations, sources=sources,
                      outcome=Outcome.REFUSED)  # fmt: skip


def tag_translations(text: str, language: Language) -> str:
    """Add the translation after every Bible reference that lacks it: "John 3:16 (KJV)";
    inside parentheses, "(John 3:16, KJV)"."""
    tag = TAGS[get_settings().translation_for(language)]
    out, pos = [], 0
    for _, start, end in all_references(text):
        out.append(text[pos:end])
        after = text[end:]
        if re.match(rf"\s*(?:\(|,\s*)?{tag}\b", after):
            pass  # already tagged
        elif after.startswith(")") and "(" in text[max(0, start - 2) : start]:
            out.append(f", {tag}")
        else:
            out.append(f" ({tag})")
        pos = end
    out.append(text[pos:])
    return "".join(out)


def _sources_section(citations: list[Citation], sources: list[SourceNote],
                     language: Language) -> str:  # fmt: skip
    text = _TEXT[language]
    lines = [f"**{text['sources']}**", ""]
    verified = [c for c in citations if c.verified]
    if verified:
        refs = "; ".join(dict.fromkeys(c.display for c in verified))
        lines.append(f"- {text['bible']}: {refs} ({verified[0].translation.full_name})")
    for s in sources:
        if s.source_type == SourceType.KNOWLEDGE:
            concept = (s.chunk_id or "").split("#", 1)[0]
            status = text["reviewed"] if s.trust_tier == "human-reviewed" else text["draft"]
            lines.append(f"- [{s.marker}] {text['knowledge']}: `{concept}.md`, {s.label} "
                         f"({status})")  # fmt: skip
        else:
            lines.append(f"- [{s.marker}] {text['commentary']}: {s.label} "
                         f"({text['public_domain']})")  # fmt: skip
    return "\n".join(lines) if len(lines) > 2 else ""


def _log(routing: Routing | None, response: ChatResponse) -> None:
    """One line per answer, without the question or the answer text."""
    a, u = response.answer, response.usage
    log.info(json.dumps({
        "route": str(a.route), "outcome": str(a.outcome), "language": str(a.language),
        "reason": routing.reason if routing else None, "citations": len(a.citations),
        "verified": a.all_verified, "sources": len(a.sources),
        "warnings": len(response.warnings), "calls": u.calls,
        "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
        "cost_usd": u.cost_usd, "seconds": u.seconds,
    }))  # fmt: skip


def _join(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p)


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
