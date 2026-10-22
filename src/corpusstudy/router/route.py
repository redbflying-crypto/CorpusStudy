"""Route a question before anything is retrieved or generated.

    pastoral    words of self-harm, abuse or despair: a safety response comes first, then a
                gentle answer (checked before every other route)
    lookup      the question only asks for a passage ("John 3:16", "Que dit Jean 3.16 ?"):
                the text is read from the Bible, no model call
    knowledge   the question names a usable knowledge concept by title or alias
    hybrid      it names a concept and cites a passage
    rag         anything else: retrieval over Scripture, commentary and knowledge

The rules are deterministic so the routing can be tested offline and explained.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from corpusstudy.config import Language
from corpusstudy.indexing.analysis import detect_language
from corpusstudy.retrieval.references import extract_references, strip_references
from corpusstudy.schemas import PassageRef, Route

# Matched on lower-cased text without accents. Questions *about* these subjects ("what does
# the Bible say about suicide?") are routed here too: the safety note costs little.
_CONCERNS = {
    "self-harm": [
        r"suicid",
        r"kill (?:my ?self|me)",
        r"end (?:my|it all)\b",
        r"take my (?:own )?life",
        r"(?:want|wanna|going) to die",
        r"don'?t want to (?:live|be alive|wake up)",
        r"(?:hurt|harm|cut(?:ting)?) my ?self",
        r"self[- ]?harm",
        r"better off dead",
        r"no reason to live",
        r"me tuer",
        r"mettre fin a (?:mes jours|ma vie)",
        r"en finir",
        r"envie de mourir",
        r"veux mourir",
        r"me faire du mal",
        r"plus envie de vivre",
        r"automutil",
        r"me scarifier",
    ],
    "abuse": [
        r"\babused\b",
        r"\babusive\b",
        r"sexual abuse",
        r"domestic violence",
        r"(?:hits|beats|hurts|chokes) me\b",
        r"\braped?\b",
        r"molest",
        r"afraid of my (?:husband|wife|partner|father|mother|parents?)",
        r"\babuse(?:s|d)? (?:me|my)\b",
        r"violence (?:conjugale|domestique)",
        r"\bviolee?s?\b",
        r"me (?:frappe|bat)\b",
        r"attouchements",
        r"maltrait",
        r"abus sexuel",
    ],
    "despair": [
        r"can'?t go on",
        r"cannot go on",
        r"no way out",
        r"hopeless",
        r"je n'?en peux plus",
        r"desespere",
        r"plus aucun espoir",
    ],
}
_CONCERN_RE = {k: re.compile("|".join(v)) for k, v in _CONCERNS.items()}

# Words that ask for a passage without asking anything about it.
_LOOKUP_WORDS = set(
    """what does do did say says said read text verse verses quote show me give please the
    a kjv king james version bible passage chapter is in of and to us
    que qu quoi dit disent lire lis lisez texte verset versets citer cite cites montre
    montrez moi donne donnez passage chapitre le la les l de du des et s il te vous plait
    lsg segond louis bible est ce dans""".split()
)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def pastoral_concern(text: str) -> str | None:
    """The first concern the text raises ("self-harm", "abuse", "despair"), if any."""
    plain = _plain(text)
    return next((name for name, rx in _CONCERN_RE.items() if rx.search(plain)), None)


@dataclass
class Routing:
    route: Route
    language: Language
    refs: list[PassageRef] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)  # usable concept ids it names
    concern: str | None = None  # pastoral concern
    reason: str = ""


def _only_asks_for_text(text: str) -> bool:
    words = re.findall(r"[a-z]+", _plain(strip_references(text)))
    return all(w in _LOOKUP_WORDS for w in words)


def route(text: str, language: Language | None = None, search=None) -> Routing:
    """Route a question. `search` (a retrieval.Search) finds the concepts it names."""
    language = language or detect_language(text)
    refs = extract_references(text)
    if concern := pastoral_concern(text):
        return Routing(Route.PASTORAL, language, refs, concern=concern,
                       reason=f"pastoral concern: {concern}")  # fmt: skip
    if refs and _only_asks_for_text(text):
        return Routing(Route.LOOKUP, language, refs, reason="asks only for the passage text")
    concepts = search.named_concepts(text, language) if search is not None else []
    if concepts and refs:
        return Routing(Route.HYBRID, language, refs, concepts,
                       reason="names a concept and cites a passage")  # fmt: skip
    if concepts:
        return Routing(Route.KNOWLEDGE, language, refs, concepts, reason="names a concept")
    return Routing(Route.RAG, language, refs, reason="open question")
