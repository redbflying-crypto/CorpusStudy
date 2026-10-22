# CorpusStudy

A personal Bible study chatbot. It combines a curated Markdown knowledge layer (books, themes, people, doctrines, events) with hybrid retrieval over public-domain Bible texts and commentaries. A citation validator checks every verse reference before an answer is returned.

## Decisions (Phase 0)

| Topic | Decision |
|---|---|
| LLM provider | Anthropic (`AI_PROVIDER=anthropic`). The key lives only in the git-ignored `.env` file. |
| Translations | **KJV** for English and **Louis Segond 1910** for French. Both are public domain. |
| Language | Answers come in the user's language and quote the matching translation. |
| Commentaries | Public domain only: Calvin's Commentaries, Matthew Henry, and the Treasury of Scripture Knowledge for cross-references. |
| Stance | Protestant Evangelical, Reformed (Calvinist). Interpretive answers are labelled as the Reformed view. Where it matters, the answer briefly notes how other traditions differ, so tradition is never presented as Scripture. |
| Audience | Personal use. |
| Hosting | Local, with Docker. |
| Knowledge format | Obsidian-style Markdown: YAML frontmatter plus `[[wikilinks]]`. See [`knowledge/README.md`](knowledge/README.md). |

## Architecture

```
question ─► router ─┬─► lookup     (explicit reference → verse text)
                    ├─► knowledge  (curated Markdown files)
                    ├─► rag        (hybrid search: SQLite FTS5 + vectors, rank fusion)
                    ├─► hybrid     (knowledge + rag)
                    └─► pastoral   (crisis / abuse: safety response first)
                          │
                          ▼
              generation (Anthropic API)
                          │
                          ▼
         citation validator (every reference checked against the corpus)
                          │
                          ▼
                 answer + verified citations
```

```
src/corpusstudy/
  config.py        settings from env / .env (provider, translations, stance, paths)
  canon.py         66 books: EN + LSG names, OSIS/USFM codes, chapter counts, alias lookup
  schemas.py       Pydantic models: references, verses, chunks, knowledge files,
                   retrieval results, answers, chat API; plus the reference parser
  ingest/          Phase 2  text parsers → Verse records
  indexing/        Phase 3  chunking, keyword + vector indexes
  retrieval/       Phase 3  hybrid retrieval and rank fusion
  knowledge/       Phase 4  knowledge file loader and linker
  router/          Phase 5  question routing
  generation/      Phase 5  prompt assembly and LLM calls
  validation/      Phase 5  citation validator
  api/             Phase 6  FastAPI /chat and the chat page
knowledge/         curated Markdown files (books, themes, people, doctrines, events)
data/              downloaded texts (git-ignored)
tests/
```

The schemas enforce these rules from the start:
- References must name a real book and chapter. They parse from English or French forms such as `Rom 8:28-30`, `Romains 8.28-30`, `Ps 23` or `Jude 3`.
- Scripture chunks must carry a public-domain translation in their own language. Commentary chunks can never carry a translation, so commentary cannot be passed off as Scripture.
- An answer only quotes the translation for its own language (KJV in English, LSG 1910 in French).
- A knowledge file marked `reviewed` must name its reviewer and the review date.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env        # then set ANTHROPIC_API_KEY
.venv/bin/pytest
```

## Phases

| Phase | Scope | Who | Status |
|---|---|---|---|
| 0 | Decisions, API key, repo | You | ✅ Done |
| 1 | Skeleton, config, canon, schemas, tests | Claude | ✅ Done |
| 2 | Ingest KJV and Louis Segond 1910, plus commentaries | Claude | ⏳ Next |
| 3 | Chunking, keyword and vector indexes, hybrid retrieval | Claude | — |
| 4 | Knowledge files (about 36) | Claude drafts, you review | — |
| 5 | Router, generation, citation validator | Claude | — |
| 6 | API, chat page, tests, evaluation set (50+ questions) | Claude drafts, you validate | — |
| 7 | Docker and running it locally | Shared | — |

## License

Apache 2.0, see [LICENSE](LICENSE). The Bible texts and commentaries used are in the public domain.
