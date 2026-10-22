"""Quotation check for knowledge concepts: every quotation must be found, word for word, in
the source its footnote cites.

A claim segment runs from the previous footnote marker (or the paragraph start) to a marker
group such as `...[^henry-rom3]` or `...[^kjv-rom5][^henry-rom5]`. Each "quoted" or
“quoted” string in the segment must occur in the text of one of the group's sources; an
ellipsis ("..." or "…") splits a quotation into fragments that must each occur. Comparison
ignores case, curly versus straight quotes and apostrophes, and spacing.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from corpusstudy.config import Translation
from corpusstudy.okf import document
from corpusstudy.okf.bundle import Concept, Issue
from corpusstudy.okf.references import SOURCES_DIR

_MARKERS = re.compile(r"((?:\[\^[^\]\s]+\])+)")
_QUOTE = re.compile(r"“([^”]+)”|\"([^\"]+)\"")
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")
_BIBLES = {"kjv": Translation.KJV, "lsg1910": Translation.LSG1910}


def normalise(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def source_text(resource: str, concept_id: str) -> str | None:
    """The text a source points at, or None when it cannot be resolved here."""
    from corpusstudy.commentary import commentary
    from corpusstudy.corpus import CorpusNotBuilt, bible
    from corpusstudy.schemas import PassageRef

    path = document.resolve_path(resource, concept_id)
    if not path.startswith(SOURCES_DIR + "/"):
        return None
    source = path.removeprefix(SOURCES_DIR + "/").removesuffix(".md")
    fragment = resource.partition("#")[2]
    if not fragment:
        return None
    if source in _BIBLES:
        try:
            verses = bible(_BIBLES[source]).passage(PassageRef.from_osis(fragment))
        except (CorpusNotBuilt, ValueError):
            return None
        return " ".join(v.text for v in verses) or None
    store = commentary(source)
    excerpt = store.get(fragment) if store else None
    return excerpt.text if excerpt else None


def check_quotes(
    c: Concept, resolve: Callable[[str, str], str | None] = source_text
) -> list[Issue]:
    issues: list[Issue] = []
    sources = {s.id: s.resource for s in c.meta.sources if s.id}
    body = re.sub(r"^\[\^[^\]]+\]:.*$", "", c.body, flags=re.M)  # footnote definitions
    unresolved: set[str] = set()
    for paragraph in re.split(r"\n\s*\n", body):
        start = 0
        for m in _MARKERS.finditer(paragraph):
            segment = paragraph[start : m.start()]
            start = m.end()
            ids = re.findall(r"\[\^([^\]\s]+)\]", m.group(1))
            texts = []
            for sid in ids:
                if sid not in sources:
                    continue
                text = resolve(sources[sid], c.id)
                if text is None:
                    unresolved.add(sid)
                else:
                    texts.append(normalise(text))
            if not texts:
                continue
            for qm in _QUOTE.finditer(segment):
                quote = qm.group(1) or qm.group(2)
                for frag in _ELLIPSIS.split(quote):
                    frag = normalise(frag).strip(" ,.;:!?")
                    if len(frag) < 3:
                        continue
                    if not any(frag in t for t in texts):
                        issues.append(
                            Issue(
                                f"{c.id}.md",
                                "error",
                                f'quotation "{frag}" not found in {", ".join(ids)}',
                            )  # fmt: skip
                        )
    for sid in sorted(unresolved):
        issues.append(
            Issue(f"{c.id}.md", "warning", f"source `{sid}` text not available; quotes unchecked")
        )
    return issues
