"""Text normalisation shared by every parser."""

from __future__ import annotations

import re
import unicodedata

_SPACE_RE = re.compile(r"\s+")
# No space before closing punctuation, except the French high punctuation (; : ! ?),
# which takes a (narrow) space in Louis Segond and is left as the source prints it.
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.)\]])")


def clean_text(text: str) -> str:
    """NFC-normalise, collapse whitespace and trim. Punctuation and quotes are kept as printed."""
    text = unicodedata.normalize("NFC", text)
    text = _SPACE_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    return text.strip()
