# CorpusStudy

A personal Bible study chatbot. It combines a curated Markdown knowledge layer (books, themes, people, doctrines, events) with hybrid retrieval over public-domain Bible texts and commentaries. A citation validator checks every verse reference before an answer is returned.

## Decisions (Phase 0)

| Topic | Decision |
|---|---|
| LLM provider | Anthropic (`AI_PROVIDER=anthropic`). The key lives only in the git-ignored `.env` file. |
| Translations | **KJV** for English and **Louis Segond 1910** for French. Both are public domain. |
| Language | Answers come in the user's language and quote the matching translation. |
| Commentaries | Public domain only: Calvin's Commentaries and Matthew Henry. The Treasury of Scripture Knowledge is skipped, because the only reachable edition is CC BY 4.0, not public domain. |
| Stance | Protestant Evangelical, Reformed (Calvinist). Interpretive answers are labelled as the Reformed view. Where it matters, the answer briefly notes how other traditions differ, so tradition is never presented as Scripture. |
| Audience | Personal use. |
| Hosting | Local, with Docker. |
| Knowledge format | Google Open Knowledge Format (OKF) v0.2: a curated, version-controlled bundle of Markdown concepts with YAML frontmatter, provenance and human review. See [`docs/KNOWLEDGE_FORMAT.md`](docs/KNOWLEDGE_FORMAT.md). |
| Reviewer | `human:dhnguyen` (`CORPUS_REVIEWER`). Only a `human:` actor can sign off a concept. |
| Review channel | GitHub pull requests, which CI checks; approval in chat as a fallback. |
| Drafts in answers | No. Answers use human-reviewed, stable concepts only (`CORPUS_USE_DRAFTS=false`). |
| Embeddings | Keyword search (SQLite FTS5) first (`CORPUS_EMBEDDINGS=none`); dense vectors can be added later. |

## Architecture

```
question ─► router ─┬─► lookup     (only asks for a passage → verse text, no model call)
                    ├─► knowledge  (names a reviewed OKF concept)
                    ├─► rag        (hybrid search: SQLite FTS5, rank fusion; vectors later)
                    ├─► hybrid     (a concept and a passage)
                    └─► pastoral   (self-harm / abuse / despair: safety message first)
                          │
                          ▼
              generation (Anthropic API)
                          │
                          ▼
  citation validator (every reference checked against the corpus,
                      every knowledge claim traced to its sources)
                          │
                          ▼
                 answer + verified citations
```

```
src/corpusstudy/
  config.py        settings from env / .env (provider, translations, stance, paths)
  canon.py         66 books: EN + LSG names, OSIS/USFM codes, chapter counts, alias lookup
  schemas.py       Pydantic models: references, verses, chunks, retrieval results,
                   answers, chat API; plus the reference parser
  versification.py verse counts per translation, KJV <-> LSG 1910 reference mapping
  corpus.py        read access to the ingested Bible texts (verse, chapter, passage)
  commentary.py    read access to the ingested commentaries (excerpt lookup by id)
  okf/             Google Open Knowledge Format v0.2: documents, tolerant models,
                   bundle conformance, CorpusStudy producer profile, index.md/log.md,
                   source references; CLI `python -m corpusstudy.okf`
  resources/       committed tables: versification, KJV-LSG mapping, source checksums
  ingest/          Phase 2  OSIS / USFM / CSV-JSON / CCEL parsers, alignment, pipeline
  indexing/        Phase 4  analysis (stemming, KJV archaisms), chunking, SQLite FTS5 index
  retrieval/       Phase 4  reference extraction, hybrid OKF-aware search, evaluation
  router/          Phase 6  question routing (deterministic rules)
  generation/      Phase 6  evidence, prompts, Anthropic API call, answer pipeline;
                   CLI `python -m corpusstudy.generation`
  validation/      Phase 6  citation and provenance validator
  api/             Phase 7  FastAPI /chat and the chat page
eval/              retrieval evaluation sets (tuned and held-out)
knowledge/         OKF v0.2 bundle (index.md, log.md, books, themes, people,
                   doctrines, events, references/sources); see docs/KNOWLEDGE_FORMAT.md
data/              downloaded texts and processed corpus (git-ignored)
docs/              BUILD_PLAN.md, KNOWLEDGE_FORMAT.md
tests/
```

The schemas enforce these rules from the start:
- References must name a real book and chapter. They parse from English or French forms such as `Rom 8:28-30`, `Romains 8.28-30`, `Ps 23` or `Jude 3`.
- Scripture chunks must carry a public-domain translation in their own language. Commentary chunks can never carry a translation, so commentary cannot be passed off as Scripture.
- An answer only quotes the translation for its own language (KJV in English, LSG 1910 in French).
- The knowledge bundle conforms to OKF v0.2. Our own concepts also pass a stricter producer profile:
  - Every claim is footnoted to a `sources` entry that resolves to a real Bible passage or commentary excerpt.
  - Only concepts verified by a `human:` actor can be `stable`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env        # then set ANTHROPIC_API_KEY
.venv/bin/pytest
.venv/bin/python -m corpusstudy.okf check   # knowledge bundle: OKF conformance + profile
.venv/bin/python -m corpusstudy.indexing    # build the search index (indexes/, git-ignored)
.venv/bin/python -m corpusstudy.retrieval search "Que dit Jean 3.16 ?"
.venv/bin/python -m corpusstudy.retrieval eval
.venv/bin/python -m corpusstudy.generation ask "What does Romans 5:1 teach?"
.venv/bin/python -m corpusstudy.generation smoke   # live API check (needs the key)
```

CI (`.github/workflows/ci.yml`) runs lint, rebuilds the Bibles, fails if any committed table or bundle file changes, then runs the tests and `okf check` on every push and pull request.

## Bible texts (Phase 2)

```bash
.venv/bin/python -m corpusstudy.ingest      # downloads, verifies and writes data/processed/
```

| Translation | Source (pinned commit, SHA-256 checked) | Verses |
|---|---|---|
| KJV (1769) | eBible.org OSIS, via [seven1m/open-bibles](https://github.com/seven1m/open-bibles) | 31,102 + 116 psalm titles |
| Louis Segond 1910 | UBS USFM, via [BibleCorps/FRA-B-LSG1910-PD-UBS](https://github.com/BibleCorps/FRA-B-LSG1910-PD-UBS) | 31,170 |

Each text is stored in its own verse numbering, so a French citation such as `Psaume 51.3` is checked against Louis Segond and an English one against the KJV. The two numberings differ in about 150 chapters:
- Louis Segond numbers most psalm titles as verses, so KJV Ps 51:1 is LSG Ps 51:3.
- It follows the Hebrew chapter breaks in places: KJV Exod 8:1 is LSG Exod 7:26, and KJV Job 38:39 is LSG Job 39:1.
- It splits or joins a few verses, for example KJV Mark 9:50 is LSG Mark 9:50-51.

`versification.map_passage` converts references between the two numberings. Its table, `resources/versification/KJV-LSG1910.json`, comes from aligning the two texts by verse length plus shared names and numbers (Gale-Church style). Only the chapters whose numbering differs are aligned.

The Louis Segond source contains one typo, at Ps 119:115 (typed `\v 11 5`). The pipeline repairs it and logs the repair. The KJV is public domain everywhere except the United Kingdom, where it is under perpetual Crown copyright. Personal use in France is unaffected.

## Commentaries (Phase 2b)

CCEL (ccel.org) is not reachable from the build workspace, so commentary files are downloaded by hand and imported once. The import records the file's SHA-256 checksum in `resources/sources.lock.json`:

```bash
.venv/bin/python -m corpusstudy.ingest --import calvin-lcc path/to/calcom.doc
.venv/bin/python -m corpusstudy.ingest --import henry-concise path/to/Matthew_Henry_Commentaries.txt
```

| Source | Edition | Status |
|---|---|---|
| `calvin-lcc` | *Calvin: Commentaries*, selections arranged by subject, tr. J. Haroutunian, Library of Christian Classics XXIII (1958), CCEL ThML. CCEL lists it as public domain. | ✅ 250 excerpts (245 keyed to the passage they comment on), about 870k characters |
| `henry-concise` | *Matthew Henry's Concise Commentary*, the one-volume abridgement, from CCEL's PDF saved as plain text. The text is public domain; CCEL's PDF edition is free for non-commercial use. | ✅ 5,164 excerpts: 64 book introductions, 1,189 chapters, and section comments covering every KJV verse |
| Treasury of Scripture Knowledge | — | Skipped (only a CC BY 4.0 edition is reachable) |

**Matthew Henry.** The plain text keeps the PDF's page furniture. The parser removes page numbers, running headers and end-of-line hyphenation, then follows the book, chapter and section headings. It handles both layouts used in the book:
- `Verses 3–5` headings after a "Chapter Outline".
- Sections that open with their reference, such as `Ezek. 1:1-14 ...`.

Each chapter outline becomes its own excerpt. In the source, 2 Kings and 2 Chronicles have no "Chapter 1" line, and the parser handles both cases. In 7 places a section heading is missing from the source, for example in John 3, where the 1–8 comment runs to verse 21. There the preceding section's range is extended, and each extension is logged. Two typos in the outline references are corrected.

Cross-references use the old Online Bible abbreviations. "Jud" means Judges and "So" means Song of Solomon, which is the opposite of how the shared book table reads them, so each source can override abbreviations.

**Calvin.** Each excerpt starts with the verse Calvin comments on, quoted in italics with its reference. The excerpt is keyed to that passage (`refs`), and other passages it cites are kept as `mentions`. Only Calvin's own text is kept: the editor's introductions, footnotes and page numbers are dropped. The source has three wrong references; they are corrected in a reviewed table (`COMMENTARY_SOURCES[...].corrections`), and each correction is logged.

## Search (Phase 4)

`python -m corpusstudy.indexing` builds one SQLite file, `indexes/corpus.sqlite` (about 70 MB, 8 seconds). It contains:
- **Bible:** 3-verse windows for each translation, in that translation's own numbering.
- **Commentaries:** excerpts split on sentence boundaries, at most about 1,200 characters each.
- **Knowledge:** one chunk per section of each OKF concept, carrying its trust metadata.

Text is stemmed with Snowball (English and French). In English, KJV archaisms are first folded onto modern forms ("loveth" → "loves", "thou knowest" → "knows"), and in French elided articles are dropped ("l'Éternel" → "éternel").

For each source a question produces several ranked lists:
- the cited passage itself, read from the Bible;
- knowledge concepts the question names by title or alias ("election", "la grâce");
- commentary and knowledge about a cited passage (French references are converted to KJV numbering first);
- commentary and knowledge about the verses its words matched best;
- exact phrase, all-words and any-word BM25 matches (terms of 4 or more letters match as prefixes);
- neighbours in the OKF link graph.

The lists are combined with weighted reciprocal rank fusion, and Scripture, commentary and knowledge are interleaved in the results. Naming "Matthew Henry" or "Calvin" restricts commentary to that work. Knowledge concepts appear only when they are human-reviewed and stable (`CORPUS_USE_DRAFTS=false`). Dense vectors can be added later as one more list (`CORPUS_EMBEDDINGS`).

**Evaluation** (`eval/retrieval.yaml`: 35 questions in English and French, each expected passage checked against the corpus):

| Category | n | Recall@1 | Recall@5 | Recall@10 | MRR |
|---|---|---|---|---|---|
| Quotes, English / French | 7 / 4 | 0.86 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 0.93 / 1.00 |
| References, English / French | 4 / 3 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| Topics, English / French | 6 / 3 | 0.33 / 0.33 | 1.00 / 1.00 | 1.00 / 1.00 | 0.62 / 0.56 |
| Commentary | 5 | 0.80 | 1.00 | 1.00 | 0.90 |
| **All gated** | **32** | **0.75** | **1.00** | **1.00** | **0.86** |
| Paraphrase (not gated) | 3 | 0.00 | 0.00 | 0.33 | 0.04 |

A held-out set (`eval/retrieval_holdout.yaml`, 12 questions written after tuning and never used to tune) scores gated recall@5 = 1.00 and MRR 0.85, so the tuning did not overfit. Modern paraphrases of KJV wording ("love is patient") are the known gap of keyword-only search; dense vectors would address them. Median latency is about 20 ms per question.

## Answers (Phase 6)

`python -m corpusstudy.generation ask "..."` runs the whole pipeline:

1. **Router** (rules, testable offline):
   - **Pastoral** is checked first. Words of self-harm, abuse or despair (English or French) put a safety message with crisis contacts at the top of the answer. The message is fixed text written in code, not generated, and it is shown even when the model is unavailable.
   - **Lookup:** a question that only asks for a passage ("John 3:16", "Que dit Jean 3.16 ?") is answered from the Bible in the answer's translation, with no model call.
   - **Knowledge, hybrid and RAG** depend on whether the question names a reviewed concept (by title, alias or file name) and whether it cites a passage.
2. **Evidence:**
   - The top 12 search results are passed to the model as evidence.
   - Scripture is cited by reference. Commentary and knowledge items get markers such as `[E2]`.
   - Knowledge items carry their review status.
3. **Generation:**
   - **Model and effort:** one Anthropic API call to `claude-opus-5-5`, with adaptive thinking at `CORPUS_LLM_EFFORT` (default `medium`).
   - **Output and caching:** structured JSON output, and a cached system prompt that sets the Reformed framing. The prompt asks the model to label interpretation, note where traditions differ, and not invent sources.
   - **Fallback:** server-side fallback (`fallbacks: "default"`) re-runs a request the model declines on safety grounds on Anthropic's recommended fallback model.
4. **Citation validator:**
   - Every reference must exist in the answer language's translation and numbering.
   - Every quotation of three words or more must occur word for word in a source cited in its paragraph.
   - Every `[E#]` marker must name a provided source.
   - Every cited knowledge concept must still pass the OKF profile, so its footnotes resolve.
   - A failing answer gets one repair round. If it still fails, it is not shown, and the retrieved passages are given instead.
5. **Answer:**
   - Markers are renumbered `[1]`, `[2]` in order of use.
   - Each source is listed with its label and, for knowledge, its OKF trust tier.
   - Scripture citations come back verified, and the answer records its route and outcome (answered, repaired, refused, lookup, safety or declined).

## Phases

The full plan, including how the knowledge layer follows Google's Open Knowledge Format (OKF) v0.2, is in [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md).

| Phase | Scope | Who | Status |
|---|---|---|---|
| 0 | Decisions | You | ✅ Done, including the OKF review decisions |
| 1 | Skeleton, config, canon, schemas | Claude | ✅ Done, OKF-aligned |
| 2 | Corpus: KJV, LSG 1910, Calvin, Henry | Claude (+ your files) | ✅ Done, published as OKF source references (TSK skipped) |
| 3 | OKF bundle foundation: loader, validator, lifecycle (new, touch, verify, deprecate), graph, index/log, CLI, CI | Claude | ✅ Done |
| 4 | Chunking, FTS5 keyword search (vectors later), OKF-aware retrieval, evaluation | Claude | ✅ Done |
| 5 | Knowledge concepts (about 36) in OKF | Claude drafts, **you review** | 🟡 37 concepts drafted; awaiting your review |
| 6 | Router, generation, citation and provenance validator | Claude | 🟡 Built and tested offline; live smoke test needs your API key |
| 7 | API, chat page, evaluation set | Claude, **you validate** | — |
| 8 | Docker, local run | Shared | — |
| 9 | Knowledge-base maintenance (optional) | Claude proposes, you review | — |

## License

Apache 2.0, see [LICENSE](LICENSE). The Bible texts and commentaries used are in the public domain.
