"""Route a question before anything is retrieved or generated (first match wins).

    refuse        a request to demean, harass or incite against people: declined, with an
                  offer of respectful discussion
    pastoral      words of self-harm, abuse or despair: a safety response comes first, then a
                  gentle answer
    lookup        the question only asks for a passage ("John 3:16", "Que dit Jean 3.16 ?",
                  "Romans 8:28 in the ESV"): the text is read from the Bible, no model call
    crossref      asks for passages related to a cited passage: the cross-reference index
    compare       compares books or accounts ("the four Gospels on the resurrection"):
                  retrieval filtered book by book
    interpretive  asks about interpretations or traditions ("main interpretations of
                  Romans 9", "Catholics and Protestants on justification")
    knowledge     names a usable knowledge concept by title or alias
    hybrid        names a concept and cites a passage
    rag           anything else: retrieval over Scripture, commentary and knowledge

Two modifiers ride along with any route: `advice` (a medical, legal or financial question:
general biblical context only, and a referral to a qualified professional) and a requested
translation (`translation` when it is loaded, `unavailable_translation` when it is not).
Concepts the question names are kept for every route (`secondary` lists knowledge or hybrid
when they apply). The rules are deterministic so routing can be tested offline and
explained; an optional model-assisted step can refine an open (rag) question.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from corpusstudy.canon import find_book
from corpusstudy.config import Language, Translation
from corpusstudy.indexing.analysis import detect_language
from corpusstudy.retrieval.references import (
    extract_references,
    find_invalid_references,
    strip_references,
)
from corpusstudy.schemas import PassageRef, Route

# Matched on lower-cased text without accents. Questions *about* these subjects ("what does
# the Bible say about suicide?") are routed here too: the safety note costs little.
_CONCERNS = {
    "self-harm": [
        r"suicid",
        r"kill(?:ing)? (?:my ?self|me)\b",
        r"end(?:ing)? (?:my life|it all)\b",
        r"take my (?:own )?life",
        r"wish i(?:'?d| was| were| had) (?:dead|never been born)",
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


# Requests to demean or harass people, or to call groups of people inferior. Theological
# questions about such subjects ("Does the Bible justify slavery?") are not matched.
_GROUPS = (r"(?:jews?|jewish people|muslims?|arabs?|blacks?|black people|africans?|whites?|"
           r"asians?|gays?|homosexuals?|lesbians?|trans(?:gender)? people|women|immigrants?|"
           r"catholics?|protestants?|mormons?|hindus?|atheists?|juifs?|musulmans?|arabes?|"
           r"noirs?|homosexuels?|femmes|immigres?|catholiques?|athees?)")  # fmt: skip
_HOSTILE = re.compile(
    r"\b(?:write|give me|help me|make|compose|draft|ecris|aide-moi a|donne-moi)\b.{0,40}"
    r"\b(?:insult|mock|humiliate|harass|bully|threaten|demean|ridicule|shame|insulter|"
    r"humilier|harceler|ridiculiser|menacer|se moquer)"
    r"|\b(?:how (?:can|do) i|help me) (?:insult|humiliate|harass|bully|threaten|shame)\b"
    r"|\buse (?:the bible|scripture|bible verses|verses) to (?:attack|shame|humiliate|insult|"
    r"threaten|hurt)\b"
    rf"|\b{_GROUPS} (?:are|is) (?:inferior|subhuman|vermin|animals|less than human|cursed)\b"
    rf"|\b{_GROUPS} (?:deserve to die|should (?:be killed|die)|must be (?:killed|exterminated))"
    rf"|\b{_GROUPS} (?:sont|est) (?:inferieur|maudit|des animaux|sous-homme)"
)

# Medical, legal or financial advice: a domain word with an advice-seeking phrase.
_ASKS = re.compile(r"\b(?:should i|can i|do i (?:need|have) to|is it (?:ok|okay|wrong|safe) "
                   r"(?:to|if)|what should i|how much should i|must i|dois-je|puis-je|"
                   r"faut-il que je|est-ce que je (?:dois|peux))\b")  # fmt: skip
_ADVICE = {
    "medical": re.compile(r"\b(?:medication|medicine|meds|doctor|diagnos\w*|symptoms?|"
                          r"treatment|therapy|chemo\w*|surgery|vaccin\w*|pills?|"
                          r"antidepressants?|medicament\w*|medecin|traitement|chirurgie)\b"),
    "legal": re.compile(r"\b(?:lawyer|attorney|lawsuit|sue|court|custody|legal\w*|contract|"
                        r"immigration|avocat|proces|tribunal|garde des enfants|juridique)\b"),
    "financial": re.compile(r"\b(?:invest\w*|stocks?|shares|crypto\w*|bitcoin|loan|mortgage|"
                            r"bankrupt\w*|retirement|savings|investir|placements?|credit|"
                            r"emprunt|faillite|bourse)\b"),
}  # fmt: skip

# Translations people ask for. Abbreviations must be in capitals ("ESV"), names any case.
_LOADED = {
    Translation.KJV: re.compile(r"\bKJV\b|\bking james\b|\bauthori[sz]ed version\b", re.I),
    Translation.LSG1910: re.compile(r"\bLSG\b|\b(?:louis )?segond(?: 1910)?\b(?! 21)", re.I),
}
_OTHER_ABBREV = re.compile(r"\b(ESV|NIV|NASB|NKJV|RSV|NRSV|NLT|CSB|HCSB|AMP|ASV|YLT|NAB|NJB|"
                           r"TOB|BDS|NBS|NEG|S21)\b")  # fmt: skip
_OTHER_NAMES = re.compile(
    r"\b(english standard version|new international version|new american standard|"
    r"new king james|revised standard version|new living translation|christian standard "
    r"bible|amplified bible|the message|american standard version|young'?s literal|darby|"
    r"geneva bible|douay|jerusalem bible|bible de jerusalem|segond 21|colombe|nouvelle bible "
    r"segond|semeur|parole de vie|francais courant|ostervald)\b"
)

# Comparisons: book groups, or two books with a comparing word.
_GROUP_BOOKS = [  # the synoptics first: "les évangiles synoptiques" are three
    (re.compile(r"\bsynoptics?\b|\bsynoptiques?\b"), ["Matt", "Mark", "Luke"]),
    (re.compile(r"\b(?:four|4) gospels\b|\bthe gospels\b|\bgospel accounts\b|"
                r"\b(?:quatre|4) evangiles\b|\bles evangiles\b"), ["Matt", "Mark", "Luke", "John"]),
]  # fmt: skip
_COMPARE = re.compile(r"\b(?:compar\w*|contrast\w*|differ\w*|versus|vs\.?|parallel accounts?|"
                      r"harmon\w*|agree\w*|accounts?|recits?|s'accordent)\b")  # fmt: skip
_BOOK_WORD = re.compile(r"\b(?:[1-3] )?[A-Z][a-zà-ÿ]+(?: of [A-Z][a-z]+| des [a-z]+)?")

# Interpretive questions and cross-references.
_INTERPRETIVE = re.compile(
    r"\binterpret\w*|\bviews? (?:on|of)\b|\breadings? of\b|\bperspectives?\b|"
    r"\btraditions?\b|\bdenominations?\b|\bcalvinists?\b|\barminians?\b|\bcatholics?\b|"
    r"\borthodox\b|\blutherans?\b|\bbaptists?\b|\bpentecostals?\b|\breformed\b|"
    r"\bpoints? de vue\b|\blectures? de\b|\bcatholiques?\b|\borthodoxes?\b|"
    r"\blutheriens?\b|\breformes?\b|\barminiens?\b|\bcalvinistes?\b|\bprotestants?\b"
)
_CROSSREF = re.compile(
    r"\bcross[- ]?ref\w*|\b(?:related|parallel|similar|other|connected|linked) "
    r"(?:passages?|verses?|texts?)\b|\b(?:passages?|verses?|texts?) (?:parallel|related|similar|"
    r"linked|connected) (?:to|with)\b|\bparallels? (?:to|of|for)\b|\bwhere else\b|"
    r"\breferences? croisees?\b|\brenvois?\b|\b(?:passages?|versets?|textes?) "
    r"(?:paralleles?|similaires?|lies)\b|\bautres (?:passages|versets)\b|\bparalleles? (?:a|de)\b"
)


def hostile(text: str) -> bool:
    """A request to demean, harass or incite against people."""
    return bool(_HOSTILE.search(_plain(text)))


def advice_domain(text: str) -> str | None:
    """'medical', 'legal' or 'financial' when the question asks for such advice."""
    plain = _plain(text)
    if not _ASKS.search(plain):
        return None
    return next((name for name, rx in _ADVICE.items() if rx.search(plain)), None)


def requested_translation(text: str) -> tuple[Translation | None, str | None]:
    """(a loaded translation the question asks for, the name of one that is not loaded)."""
    if m := _OTHER_ABBREV.search(text):
        return None, m.group(1)
    if m := _OTHER_NAMES.search(_plain(text)):
        return None, m.group(1).title()
    for tr, rx in _LOADED.items():
        if rx.search(text):
            return tr, None
    return None, None


def named_books(text: str) -> list[str]:
    """Books named in the text (outside references), in order: "Matthew and Luke"."""
    text = strip_references(text)
    found: dict[str, None] = {}
    for m in _BOOK_WORD.finditer(text):
        book = find_book(m.group(0))
        if book is not None and len(m.group(0)) > 2:
            found.setdefault(book.osis, None)
    return list(found)


def compared_books(text: str, refs: list[PassageRef]) -> list[str]:
    plain = _plain(text)
    for rx, books in _GROUP_BOOKS:
        if rx.search(plain):
            return books
    books = list(dict.fromkeys([*named_books(text), *(r.book for r in refs)]))
    return books if len(books) >= 2 and _COMPARE.search(plain) else []


@dataclass
class Routing:
    route: Route
    language: Language
    refs: list[PassageRef] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)  # usable concept ids it names
    concern: str | None = None  # pastoral concern
    invalid_refs: list[str] = field(default_factory=list)  # "Matthieu 29.1 (...)"
    books: list[str] = field(default_factory=list)  # compare: the books to search one by one
    secondary: list[Route] = field(default_factory=list)  # knowledge / hybrid when they apply
    advice: str | None = None  # medical | legal | financial
    translation: Translation | None = None  # a loaded translation the question asks for
    unavailable_translation: str | None = None  # "ESV"
    links: int | None = None  # crossref: how many linked passages the index holds
    reason: str = ""


def _only_asks_for_text(text: str, invalid: list[str]) -> bool:
    text = strip_references(text)
    for bad in invalid:  # "Matthieu 29.1 (Matthew has 28 chapters)": drop the reference
        text = text.replace(bad.split(" (", 1)[0], " ")
    text = _OTHER_ABBREV.sub(" ", text)
    plain = _OTHER_NAMES.sub(" ", _plain(text))
    for rx in _LOADED.values():
        plain = rx.sub(" ", plain)
    words = re.findall(r"[a-z]+", plain)
    return all(w in _LOOKUP_WORDS for w in words)


def route(text: str, language: Language | None = None, search=None) -> Routing:
    """Route a question. `search` (a retrieval.Search) finds the concepts it names."""
    language = language or detect_language(text)
    refs = extract_references(text)
    translation, unavailable = requested_translation(text)
    base = dict(advice=advice_domain(text), translation=translation,
                unavailable_translation=unavailable)  # fmt: skip
    if hostile(text):
        return Routing(Route.REFUSE, language, refs, reason="request to demean or harass")
    if concern := pastoral_concern(text):
        return Routing(Route.PASTORAL, language, refs, concern=concern, **base,
                       reason=f"pastoral concern: {concern}")  # fmt: skip
    invalid = find_invalid_references(text, language)
    if (refs or invalid) and _only_asks_for_text(text, invalid):
        return Routing(Route.LOOKUP, language, refs, invalid_refs=invalid, **base,
                       reason="asks only for the passage text")  # fmt: skip
    concepts = search.named_concepts(text, language) if search is not None else []
    secondary = [Route.HYBRID if refs else Route.KNOWLEDGE] if concepts else []
    plain = _plain(text)
    if refs and _CROSSREF.search(plain):
        return Routing(Route.CROSSREF, language, refs, concepts, secondary=secondary, **base,
                       reason="asks for related passages")  # fmt: skip
    if books := compared_books(text, refs):
        return Routing(Route.COMPARE, language, refs, concepts, books=books,
                       secondary=secondary, **base,
                       reason="compares books or accounts")  # fmt: skip
    if _INTERPRETIVE.search(plain):
        return Routing(Route.INTERPRETIVE, language, refs, concepts, secondary=secondary,
                       **base, reason="asks about interpretations or traditions")  # fmt: skip
    if concepts and refs:
        return Routing(Route.HYBRID, language, refs, concepts, **base,
                       reason="names a concept and cites a passage")  # fmt: skip
    if concepts:
        return Routing(Route.KNOWLEDGE, language, refs, concepts, **base,
                       reason="names a concept")  # fmt: skip
    return Routing(Route.RAG, language, refs, **base, reason="open question")
