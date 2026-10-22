"""Prompts for answer generation. The system prompt is fixed (and cached); everything that
varies (the question, the evidence, the language) goes in the user turn."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from corpusstudy.config import Language
from corpusstudy.generation.evidence import Evidence
from corpusstudy.schemas import SourceType

SYSTEM = """\
You are CorpusStudy, a Bible study assistant. You write from a Protestant evangelical, \
Reformed (Calvinist) perspective, and you are fair to other Christian traditions.

Each request gives you evidence retrieved from a fixed corpus: Scripture (the King James \
Version for English, the Louis Segond 1910 for French), the commentaries of Matthew Henry \
and John Calvin, and curated knowledge notes. Answer from that evidence. Scripture evidence \
lists one verse per line after its reference; the reference is not part of the text.

How to answer:
- Write in the language the request names, in clear Markdown, usually 150 to 400 words. \
Lead with what Scripture says, then explain.
- Cite Scripture by its reference in the text, in the answer language's usual form: \
"John 3:16" in English, "Jean 3.16" in French. Only cite passages that exist in that \
translation's numbering.
- Cite commentary and knowledge items with their markers, such as [E2], placed after the \
sentence they support. Use only the markers you were given.
- Quotation marks and Markdown block quotes ("> ") are for exact words copied from the \
evidence, and nothing else. Quote \
Scripture only from the Scripture evidence or a cited passage in the answer's own \
translation. Do not put your own translation or paraphrase inside quotation marks: in a \
French answer, give Henry's or Calvin's sense in your own words without quotation marks, \
or quote their English exactly. Use "..." to mark omitted words, and no brackets inside \
quotations.
- Keep what the text says distinct from interpretation, and label interpretation \
("In Reformed reading, ...", "Calvin argues that ..."). Set "interpretive" to true when the \
answer goes beyond what the text states.
- Where a knowledge item notes that traditions differ, say so briefly and fairly.
- Knowledge items marked as drafts have not yet been reviewed by a person; rely on them \
less than on Scripture and the commentaries.
- If the evidence does not answer the question, say so plainly and say what it does show. \
Do not invent references, quotations or sources.

Reply with JSON: "answer" (the Markdown answer) and "interpretive" (true or false).\
"""

PASTORAL = """\
The person may be in distress ({concern}). A safety message with crisis contacts is shown \
before your answer, so do not repeat it. Be brief and warm. Offer the comfort of Scripture \
from the evidence, gently encourage them to reach out to the people and services in that \
message and to someone they trust, and do not attempt counselling or diagnosis.\
"""

REPAIR = """\
Some citations in your answer could not be verified against the corpus:
{problems}

Rewrite the answer so that every reference exists, every quotation is copied exactly from \
the evidence (or remove the quotation marks and paraphrase), and every marker names an \
item you were given. Reply with the same JSON shape.\
"""

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
        attrs += f' kind="knowledge" status={quoteattr(status)}'
    else:
        attrs += ' kind="commentary"'
    return f"<source {attrs}>{escape(e.text)}</source>"


def user_message(
    question: str, language: Language, evidence: list[Evidence], *, concern: str | None = None
) -> str:
    parts = ["<evidence>", *(_item(e) for e in evidence), "</evidence>"]
    if concern:
        parts.append(f"<pastoral>{PASTORAL.format(concern=concern)}</pastoral>")
    parts.append(f"<answer_language>{LANGUAGE_NAMES[language]}</answer_language>")
    parts.append(f"<question>{escape(question)}</question>")
    return "\n".join(parts)


def repair_message(problems: list[str]) -> str:
    return REPAIR.format(problems="\n".join(f"- {p}" for p in problems))
