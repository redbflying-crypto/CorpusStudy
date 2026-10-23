"""Verse alignment between two translations with different versification (KJV <-> LSG 1910).

Most chapters number their verses identically and map one-to-one. Where the verse counts of
a run of chapters differ (psalm titles numbered as verses, Job 38-41, Mark 9:50/51...), the
verses of that run are aligned with a Gale-Church style dynamic programme. The cost combines
the character-length ratio, which is stable between English and French, with shared "anchor"
tokens (numbers and proper names, compared accent-free on their first letters).

The result is a list of `Link`s, each joining one or two verses on one side to one or two
verses on the other; a side may be empty when a verse has no counterpart.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from corpusstudy.ingest.raw import RawVerse

Key = tuple[str, int, int]

# English/French Bible text length ratio, measured on the chapters that map one-to-one.
_DEFAULT_RATIO = 1.12
_VARIANCE = 6.8  # Gale & Church's s^2

# (left count, right count) -> prior penalty
_MOVES: dict[tuple[int, int], float] = {
    (1, 1): 0.0,
    (1, 2): 2.3,
    (2, 1): 2.3,
    (1, 0): 4.5,
    (0, 1): 4.5,
    (2, 2): 4.6,
}

_WORD_RE = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)
_STOP = {
    # Capitalised function words at sentence starts carry no signal.
    "and", "the", "for", "but", "then", "now", "thou", "thy", "that", "this", "when", "who",
    "car", "et", "mais", "puis", "alors", "le", "la", "les", "il", "ils", "elle", "vous",
    "nous", "je", "tu", "quand", "que", "qui", "o", "oh",
}  # fmt: skip


@dataclass(frozen=True)
class Link:
    left: tuple[Key, ...]
    right: tuple[Key, ...]


def _fold(word: str) -> str:
    word = unicodedata.normalize("NFKD", word)
    return "".join(c for c in word if not unicodedata.combining(c)).lower()


def anchors(text: str) -> set[str]:
    """Numbers and capitalised words (likely names), folded to their first 4 letters."""
    out = set()
    for w in _WORD_RE.findall(text):
        if w.isdigit():
            out.add(w)
        elif w[0].isupper() and len(w) > 2 and _fold(w) not in _STOP:
            out.add(_fold(w)[:4])
    return out


def _length_cost(l1: int, l2: int, ratio: float) -> float:
    if l1 == 0 and l2 == 0:
        return 0.0
    mean = (l1 + l2 / ratio) / 2
    delta = (l2 - l1 * ratio) / math.sqrt(_VARIANCE * max(mean, 1.0))
    # -log of the two-sided normal tail, smooth and cheap.
    return delta * delta / 2


def _anchor_bonus(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return 3.0 * len(a & b) / len(a | b)


def align_sequences(
    left: Sequence[RawVerse],
    right: Sequence[RawVerse],
    ratio: float = _DEFAULT_RATIO,
    band: int = 40,
) -> list[Link]:
    """Monotone alignment of two verse sequences covering the same stretch of text."""
    n, m = len(left), len(right)
    la = [anchors(v.text) for v in left]
    ra = [anchors(v.text) for v in right]
    inf = float("inf")
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(n + 1):
        # Stay within a band around the diagonal: versification drifts by a few verses only.
        centre = i * m // n if n else 0
        for j in range(max(0, centre - band), min(m, centre + band) + 1):
            base = cost[i][j]
            if base == inf:
                continue
            for (di, dj), prior in _MOVES.items():
                ni, nj = i + di, j + dj
                if ni > n or nj > m:
                    continue
                l1 = sum(len(v.text) for v in left[i:ni])
                l2 = sum(len(v.text) for v in right[j:nj])
                a = set().union(*la[i:ni]) if di else set()
                b = set().union(*ra[j:nj]) if dj else set()
                c = base + prior + _length_cost(l1, l2, ratio) - _anchor_bonus(a, b)
                if c < cost[ni][nj]:
                    cost[ni][nj] = c
                    back[ni][nj] = (di, dj)
    links: list[Link] = []
    i, j = n, m
    while i or j:
        step = back[i][j]
        assert step is not None
        di, dj = step
        links.append(
            Link(tuple(v.key for v in left[i - di : i]), tuple(v.key for v in right[j - dj : j]))
        )
        i, j = i - di, j - dj
    links.reverse()
    return links


def _by_chapter(verses: Sequence[RawVerse]) -> dict[tuple[str, int], list[RawVerse]]:
    out: dict[tuple[str, int], list[RawVerse]] = defaultdict(list)
    for v in verses:
        out[(v.book, v.chapter)].append(v)
    for vs in out.values():
        vs.sort(key=lambda v: v.verse)
    return out


def length_ratio(left: Sequence[RawVerse], right: Sequence[RawVerse]) -> float:
    lc, rc = _by_chapter(left), _by_chapter(right)
    l_total = r_total = 0
    for ch, lv in lc.items():
        rv = rc.get(ch, [])
        if [v.verse for v in lv] == [v.verse for v in rv]:
            l_total += sum(len(v.text) for v in lv)
            r_total += sum(len(v.text) for v in rv)
    return r_total / l_total if l_total else _DEFAULT_RATIO


def align_translations(
    left: Sequence[RawVerse], right: Sequence[RawVerse]
) -> tuple[list[Link], float]:
    """Align two whole translations. Returns the links and the measured length ratio."""
    ratio = length_ratio(left, right)
    lc, rc = _by_chapter(left), _by_chapter(right)
    # Keep canonical order: book order comes from the order verses were given in.
    book_order: dict[str, int] = {}
    for v in list(left) + list(right):
        book_order.setdefault(v.book, len(book_order))
    chapters = sorted(set(lc) | set(rc), key=lambda c: (book_order[c[0]], c[1]))

    def same(ch: tuple[str, int]) -> bool:
        return [v.verse for v in lc.get(ch, [])] == [v.verse for v in rc.get(ch, [])]

    links: list[Link] = []
    run: list[tuple[str, int]] = []

    def flush_run() -> None:
        if not run:
            return
        lv = [v for ch in run for v in lc.get(ch, [])]
        rv = [v for ch in run for v in rc.get(ch, [])]
        links.extend(align_sequences(lv, rv, ratio))
        run.clear()

    for ch in chapters:
        if same(ch):
            flush_run()
            links.extend(Link((v.key,), (v.key,)) for v in lc[ch])
        else:
            if run and run[-1][0] != ch[0]:
                flush_run()
            run.append(ch)
    flush_run()
    return links, ratio
