"""Bible references inside free text ("What does Romans 9 say about election?")."""

from __future__ import annotations

import re

from corpusstudy.schemas import PassageRef, parse_reference

# Optional numeral, a book word (optionally "Song of Solomon" / "Cantique des cantiques"),
# then chapter[:verse[-verse | -chapter:verse]] with ":" or "." separators, or "," with no
# space ("Jean 3,16"; "Ps 1, 2" is two chapters, not Ps 1:2).
_CANDIDATE = re.compile(
    # A roman-numeral prefix needs a space ("I Kings"), or "Isaiah" would read as "I saiah".
    r"(?<![\w.])([1-3]\s?|I{1,3}\s)?([A-Za-zÀ-ÿ][a-zà-ÿ]+\.?(?:\s+(?:of|des)\s+[A-Za-zÀ-ÿ]+)?)"
    r"\s+("
    r"\d{1,3}\s*[-–]\s*\d{1,3}(?!\s*[:.]\s*\d|,\d)"  # chapter range: Gen 1-3
    r"|\d{1,3}(?:(?:\s*[:.]\s*|,)\d{1,3}(?:\s*[-–]\s*\d{1,3}(?:(?:\s*[:.]\s*|,)\d{1,3})?)?)?"
    r")(?![\d:])"
)


def extract_references(text: str) -> list[PassageRef]:
    """Passages cited in a question, in order, without duplicates.

    A chapter-only reference ("Romans 9") needs a capitalised book name, so phrases like
    "the numbers 3 and 4" are not read as Numbers 3.
    """
    return [ref for ref, _, _ in find_references(text)]


def strip_references(text: str) -> str:
    """The text with cited references removed (so "Romans" is not a search term)."""
    for _, start, end in reversed(find_references(text)):
        text = text[:start] + " " + text[end:]
    return text


def find_references(text: str) -> list[tuple[PassageRef, int, int]]:
    """(reference, start, end) for each citation, first occurrence of each passage."""
    found: dict[str, tuple[PassageRef, int, int]] = {}
    pos = 0
    while m := _CANDIDATE.search(text, pos):
        prefix, book, numbers = m.group(1) or "", m.group(2), m.group(3)
        has_verse = bool(re.search(r"\d(?:\s*[:.]\s*|,)\d", numbers))
        ref = None
        if has_verse or book[0].isupper():
            try:
                ref = parse_reference(f"{prefix}{book} {numbers}".replace(" ,", ","))
            except ValueError:
                ref = None
        if ref is None:
            # "In 1 John 4:8": retry just after this start, so the numeral can begin the
            # next candidate.
            pos = m.start() + 1
            continue
        found.setdefault(ref.osis, (ref, m.start(), m.end()))
        pos = m.end()
    return list(found.values())
