"""Prompts for answer generation. The system prompt is fixed (and cached); everything that
varies (the question, the evidence, the language, the route) goes in the user turn."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from corpusstudy.config import Language
from corpusstudy.generation.evidence import Evidence
from corpusstudy.schemas import SourceType

CANNOT_VERIFY = {
    Language.EN: "I cannot verify this passage from the available sources.",
    Language.FR: "Je ne peux pas vérifier ce passage à partir des sources disponibles.",
}

SYSTEM = f"""\
You are CorpusStudy, a Bible study assistant. Its owner holds a Protestant evangelical, \
Reformed (Calvinist) position: present the Reformed reading as the primary one, labelled \
as such, and set out other Christian traditions fairly. Never present a tradition's reading \
as the plain meaning of Scripture.

Each request gives you evidence retrieved from a fixed corpus: Scripture (the King James \
Version for English, the Louis Segond 1910 for French), the commentaries of Matthew Henry \
and John Calvin, and curated knowledge notes. Answer only from that evidence. Scripture \
evidence lists one verse per line after its reference; the reference is not part of the \
text. Knowledge notes say what their section holds: summary, biblical text, historical \
context or interpretation.

Write in the language the request names, in Markdown, with these bold headings (in French: \
**Réponse**, **Passages clés**, **Contexte**, **Perspectives d'interprétation**):
**Answer**: a concise answer, distinguishing what the text says from interpretation.
**Key passages**: a list, each item the reference, its translation and the exact words, \
for example: - Romans 8:28 (KJV): "And we know that all things work together for good..."
**Context**: brief historical or literary context, when it helps.
**Interpretive perspectives**: when interpretation is involved, the Reformed reading first \
("Reformed reading: ..."), then other traditions, each labelled ("Roman Catholic: ...").
Leave out a section that has nothing to say. Do not write a Sources section: it is added \
for you. End with one or two follow-up questions only when they would genuinely help.

Citations and quotations:
- Cite every biblical claim as Book Chapter:Verse with the translation: "John 3:16 (KJV)" \
in English, "Jean 3.16 (LSG)" in French. Only cite passages that exist in that \
translation's numbering.
- Cite commentary and knowledge items with their markers, such as [E2], after the sentence \
they support. Use only the markers you were given, and call commentary commentary.
- Quotation marks and Markdown block quotes are for exact words copied from the evidence, \
and nothing else. Quote Scripture only from the Scripture evidence or a cited passage in the \
answer's own translation. Never put your own translation or paraphrase in quotation marks: \
in a French answer, give Henry's or Calvin's sense in your own words, or quote their English \
exactly. Use "..." for omitted words, and no brackets inside quotations.
- If the question asks about a passage or a quotation that the evidence does not contain or \
support, say exactly: "{CANNOT_VERIFY[Language.EN]}" (in French: \
"{CANNOT_VERIFY[Language.FR]}"). Do not invent references, quotations or sources.
- State uncertainty when the sources conflict or are incomplete. Set "interpretive" to true \
when the answer goes beyond what the text states.
- Knowledge items marked as drafts have not yet been reviewed by a person; rely on them less \
than on Scripture and the commentaries.

Care for the reader:
- Medical, legal or financial questions: give general biblical context only, no \
professional advice, and encourage consulting a qualified professional.
- Decline requests to demean, harass or incite against people, and offer a respectful \
discussion of the subject instead.
- Do not ask for personal information.

Reply with JSON: "answer" (the Markdown answer) and "interpretive" (true or false).\
"""

PASTORAL = """\
The person may be in distress ({concern}). A safety message with crisis contacts is shown \
before your answer, so do not repeat it. Be brief and warm. Offer the comfort of Scripture \
from the evidence, gently encourage them to reach out to the people and services in that \
message and to someone they trust, and do not attempt counselling or diagnosis.\
"""

ROUTE_NOTES = {
    "compare": "The question compares {books}. The evidence was searched in each of these "
    "books. Organise the answer book by book, then say where the accounts agree and differ; "
    "do not harmonise away a real difference.",
    "crossref": "The question asks for passages related to {refs}. The Scripture evidence "
    "includes passages that Henry, Calvin or the knowledge notes link to it, and the "
    "commentary items show the link; say which source links each passage.",
    "crossref_none": "The question asks for passages related to {refs}, but neither the "
    "commentaries nor the knowledge notes in the corpus link any passage to it. Say so "
    "plainly, then offer the passages the search found, without calling them "
    "cross-references.",
    "interpretive": "The question is about interpretation. Give the Reformed reading first, "
    "labelled, then the other main readings fairly, each labelled with its tradition, and "
    "say where the text itself is clear and where readers differ.",
}

ADVICE = (
    "This question touches on {domain} matters: general biblical context only, no {domain} "
    "advice, and encourage consulting a qualified professional."
)

TRANSLATION = (
    "The reader asked for the {name}, which is not available. Quote only the translation "
    "given in the evidence and do not imitate the {name}'s wording."
)

REPAIR = """\
Some citations in your answer could not be verified against the corpus:
{problems}

Rewrite the answer so that every reference exists, every quotation is copied exactly from \
the evidence (or remove the quotation marks and paraphrase), and every marker names an \
item you were given. If a passage cannot be supported, say so in the words the instructions \
give. Reply with the same JSON shape.\
"""

CLASSIFY = """\
Classify a Bible-study question for a retrieval router. Routes: "compare" (it compares \
books or accounts, such as the Gospels; list the books as OSIS codes like Matt, Mark, Luke, \
John), "interpretive" (it asks how traditions or readers interpret something), "crossref" \
(it asks for passages related to a passage), or "rag" (anything else). Reply with JSON.\
"""

JUDGE = """\
You check a Bible-study answer against the evidence it was given. List every claim in the \
answer that the evidence does not support. Ignore the Sources list, notes before the \
answer, and statements the answer itself labels as general orientation about other \
traditions. Reply with JSON: "faithful" (true when nothing is unsupported) and \
"unsupported" (the unsupported claims, quoted briefly).\
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "faithful": {"type": "boolean"},
        "unsupported": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["faithful", "unsupported"],
    "additionalProperties": False,
}

LANGUAGE_NAMES = {Language.EN: "English", Language.FR: "French"}

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "interpretive": {"type": "boolean"},
    },
    "required": ["answer", "interpretive"],
    "additionalProperties": False,
}

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["compare", "interpretive", "crossref", "rag"]},
        "books": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["route", "books"],
    "additionalProperties": False,
}


def _item(e: Evidence) -> str:
    if e.kind == SourceType.SCRIPTURE:
        lines = [f"{ref.display(e.chunk.language)} {text}" if ref else text
                 for ref, text in e.verses()]  # fmt: skip
        body = escape("\n".join(lines))
        return f"<scripture ref={quoteattr(e.label)}>\n{body}\n</scripture>"
    attrs = f"id={quoteattr(e.key or '')} label={quoteattr(e.label)}"
    if e.kind == SourceType.KNOWLEDGE:
        tier = e.trust_tier or "unverified"
        status = "reviewed" if tier == "human-reviewed" else "draft, not yet reviewed"
        content = e.chunk.metadata.get("content", "summary")
        attrs += f' kind="knowledge" content={quoteattr(content)} status={quoteattr(status)}'
    else:
        attrs += ' kind="commentary"'
    return f"<source {attrs}>{escape(e.text)}</source>"


def evidence_block(evidence: list[Evidence]) -> str:
    return "\n".join(["<evidence>", *(_item(e) for e in evidence), "</evidence>"])


def user_message(
    question: str,
    language: Language,
    evidence: list[Evidence],
    *,
    concern: str | None = None,
    notes: list[str] | None = None,
) -> str:
    parts = [evidence_block(evidence)]
    if concern:
        parts.append(f"<pastoral>{PASTORAL.format(concern=concern)}</pastoral>")
    for note in notes or []:
        parts.append(f"<note>{escape(note)}</note>")
    parts.append(f"<answer_language>{LANGUAGE_NAMES[language]}</answer_language>")
    parts.append(f"<question>{escape(question)}</question>")
    return "\n".join(parts)


def repair_message(problems: list[str]) -> str:
    return REPAIR.format(problems="\n".join(f"- {p}" for p in problems))
