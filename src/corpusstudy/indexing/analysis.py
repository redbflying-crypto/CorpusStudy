"""Text analysis for keyword search: tokens -> normalised stems, per language.

The same analysis runs at indexing and at query time, so the index stores stems and the
FTS5 tokenizer only splits on spaces. English folds KJV archaisms onto modern forms first
("loveth" -> "loves", "thou" -> "you", "thou knowest" -> "knows") so a modern query finds
the 1769 wording; French drops elided articles ("l'Éternel" -> "éternel").
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

import snowballstemmer

from corpusstudy.config import Language

_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?|\d+", re.UNICODE)

_ARCHAIC = {
    "thou": "you", "thee": "you", "ye": "you", "thy": "your", "thine": "your",
    "hath": "has", "hast": "has", "doth": "does", "dost": "does", "saith": "says",
    "shalt": "shall", "wilt": "will", "hadst": "had", "didst": "did", "wast": "was",
    "wert": "were", "canst": "can", "couldest": "could", "shouldest": "should",
    "wouldest": "would", "mayest": "may", "spake": "spoke", "unto": "to",
}  # fmt: skip
# Words ending in -eth that are not 3rd-person verbs.
_ETH_KEEP = {"teeth", "seth", "beth", "heth", "japheth", "nazareth", "elisabeth", "elizabeth",
             "twentieth", "thirtieth", "fortieth", "fiftieth", "sixtieth", "seventieth",
             "eightieth", "ninetieth", "hundredth", "meth"}  # fmt: skip

STOPWORDS: dict[str, frozenset[str]] = {
    Language.EN: frozenset(
        """a about above after again against all am an and any are as at be because been
        before being below between both but by can could did do does doing down during each
        few for from further had has have having he her here hers herself him himself his how
        i if in into is it its itself just me more most my myself no nor not now of off on
        once only or other our ours ourselves out over own same she should so some such than
        that the their theirs them themselves then there these they this those through to too
        under until up very was we were what when where which while who whom why will with
        would you your yours yourself yourselves tell explain mean meaning say says said
        bible scripture verse verses passage chapter book teach teaches teaching according
        shall unto thee thou thy ye hath""".split()
    ),
    Language.FR: frozenset(
        """a au aux avec ce ces cet cette dans de des du elle elles en et eux il ils je la le
        les leur leurs lui ma mais me même mes moi mon ne nos notre nous on ou où par pas pour
        qu que qui sa se ses son sont sur ta te tes toi ton tu un une vos votre vous y est
        été être avoir ai as avons avez ont sont fait faire comme dit dire quoi quel quelle
        quels quelles comment pourquoi est-ce bible écriture écritures verset versets passage
        chapitre livre enseigne enseignement selon cela ça ceci""".split()
    ),
}

_ELISION = re.compile(r"^(?:[ldjmnstc]|qu|jusqu|lorsqu|puisqu|quoiqu)['’]", re.IGNORECASE)


def fold(text: str) -> str:
    """Lowercase and strip accents (the FTS index and queries both use folded stems)."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


@lru_cache(maxsize=4)
def _stemmer(language: str):
    return snowballstemmer.stemmer("english" if language == Language.EN else "french")


@lru_cache(maxsize=200_000)
def _stem(word: str, language: str) -> str:
    return fold(_stemmer(language).stemWord(word))


def _english(words: list[str], originals: list[str]) -> list[str]:
    out = []
    prev = ""
    for w, orig in zip(words, originals, strict=True):
        surface = w
        if w in _ARCHAIC:
            w = _ARCHAIC[w]
        elif orig.islower() and len(w) >= 6 and w.endswith("eth") and w not in _ETH_KEEP:
            w = w[:-3] + "es"  # loveth -> loves, believeth -> believes
        elif prev == "thou" and orig.islower() and len(w) >= 6 and w.endswith("est"):
            w = w[:-3] + "s"  # thou knowest -> knows
        prev = surface
        out.append(w)
    return out


def tokens(text: str, language: Language) -> list[str]:
    """Lowercased surface tokens after language-specific normalisation."""
    originals = _WORD.findall(text)
    words = [o.lower().replace("’", "'") for o in originals]
    if language == Language.FR:
        words = [_ELISION.sub("", w) for w in words]
    else:
        words = _english(words, originals)
    return [w for w in words if w]


def stems(text: str, language: Language) -> list[str]:
    return [_stem(w, language) for w in tokens(text, language)]


def query_terms(text: str, language: Language) -> list[str]:
    """Distinct stems of a query, without stopwords or one-letter tokens."""
    stop = STOPWORDS[language]
    seen: dict[str, None] = {}
    for w in tokens(text, language):
        if w in stop or len(w) < 2:
            continue
        seen.setdefault(_stem(w, language), None)
    return list(seen)


_FR_HINTS = frozenset("le la les des du est et que qui une dans pour pas sur avec au aux "
                      "selon dieu jésus christ seigneur éternel je tu nous vous ils elles mon "
                      "ma mes ton ta tes notre nos votre vos leur leurs ne n'en pourquoi "
                      "comment quel quelle quels quelles cette ces suis sommes ai avons sont "
                      "veux peux faut aussi".split())  # fmt: skip
_EN_HINTS = frozenset("the and of to is that what does who which for with about god jesus "
                      "christ lord i my you your how why when should can are was were he she "
                      "they we our this his her".split())  # fmt: skip


@lru_cache(maxsize=1)
def _book_hints() -> tuple[frozenset[str], frozenset[str]]:
    """Folded book-name words that occur only in French or only in English names."""
    from corpusstudy.canon import BOOKS

    fr = {w for b in BOOKS for w in fold(b.name_fr).split() if not w.isdigit()}
    en = {w for b in BOOKS for w in fold(b.name_en).split() if not w.isdigit()}
    fr |= {"psaume"}
    en |= {"psalm"}
    return frozenset(fr - en), frozenset(en - fr)


def detect_language(text: str) -> Language:
    """A cheap guess for search: French function words, accents, French book names and
    "3.16"-style references against their English counterparts."""
    words = [fold(w) for w in _WORD.findall(text)]
    fr_books, en_books = _book_hints()
    fr = sum(w in _FR_HINTS for w in words) + 2 * len(re.findall(r"[éèêàùçôî]", text.lower()))
    fr += 2 * sum(w in fr_books for w in words) + 2 * len(re.findall(r"\d+\.\d+", text))
    en = sum(w in _EN_HINTS for w in words) + 2 * sum(w in en_books for w in words)
    en += 2 * len(re.findall(r"\d+:\d+", text))
    return Language.FR if fr > en else Language.EN
