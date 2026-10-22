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
question ─► router ─┬─► refuse        (demeaning or harassing requests: declined)
                    ├─► pastoral      (self-harm / abuse / despair: safety message first)
                    ├─► lookup        (only asks for a passage → verse text, no model call)
                    ├─► crossref      (passages Henry, Calvin or the concepts link to it)
                    ├─► compare       (books or accounts: search filtered book by book)
                    ├─► interpretive  (interpretations and traditions: Reformed first)
                    ├─► knowledge     (names a reviewed OKF concept)
                    ├─► hybrid        (a concept and a passage)
                    └─► rag           (hybrid search: SQLite FTS5, rank fusion; vectors later)
   modifiers: medical / legal / financial note · translation asked for (loaded or not)
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
  api/             Phase 7  FastAPI app (/chat, /passage, /health) and the chat page;
                   `python -m corpusstudy.api`
eval/              retrieval and answer evaluation sets (tuned and held-out)
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
.venv/bin/python -m corpusstudy.generation eval    # answer evaluation (offline; --live)
.venv/bin/python -m corpusstudy.api                # chat page at http://127.0.0.1:8000
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
- **Bible:** passage windows for each translation, in that translation's own numbering. Each window starts at the beginning of a sentence and runs to the end of one (3 to 10 verses), so no sentence is split, and it records its book number and testament. Single-verse chunks were tried and made ranking worse (MRR 0.86 against 0.89), so exact verse requests go to the lookup route, which reads single verses from the Bible.
- **Commentaries:** excerpts split on sentence boundaries, at most about 1,200 characters each.
- **Knowledge:** one chunk per section of each OKF concept, carrying its trust metadata and what the section holds (summary, biblical text, historical context or interpretation).
- **Cross-references:** 2,135 links between passages, all public domain. They come from the passages Henry and Calvin cite in their comments, and from key passages listed together in theme, doctrine, event and parable concepts. About a quarter of all verses have at least one.

Text is stemmed with Snowball (English and French). In English, KJV archaisms are first folded onto modern forms ("loveth" → "loves", "thou knowest" → "knows"), and in French elided articles are dropped ("l'Éternel" → "éternel").

For each source a question produces several ranked lists:
- the cited passage itself, read from the Bible;
- knowledge concepts the question names by title or alias ("election", "la grâce");
- commentary and knowledge about a cited passage (French references are converted to KJV numbering first);
- commentary and knowledge about the verses its words matched best;
- exact phrase, all-words and any-word BM25 matches (terms of 4 or more letters match as prefixes);
- neighbours in the OKF link graph.

The lists are combined with weighted reciprocal rank fusion, and Scripture, commentary and knowledge are interleaved in the results. Naming "Matthew Henry" or "Calvin" restricts commentary to that work. Searches can be filtered by book or testament. Concept names are matched as whole words, not stems (otherwise "holy" would name Holiness), and a one-word file name that differs from the title is not used as a name. Knowledge concepts appear only when they are human-reviewed and stable (`CORPUS_USE_DRAFTS=false`). Dense vectors can be added later as one more list (`CORPUS_EMBEDDINGS`).

**Evaluation** (`eval/retrieval.yaml`: 35 questions in English and French, each expected passage checked against the corpus):

| Category | n | Recall@1 | Recall@5 | Recall@10 | MRR |
|---|---|---|---|---|---|
| Quotes, English / French | 7 / 4 | 0.86 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 0.93 / 1.00 |
| References, English / French | 4 / 3 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| Topics, English / French | 6 / 3 | 0.33 / 0.67 | 1.00 / 1.00 | 1.00 / 1.00 | 0.67 / 0.78 |
| Commentary | 5 | 0.80 | 1.00 | 1.00 | 0.90 |
| **All gated** | **32** | **0.78** | **1.00** | **1.00** | **0.89** |
| Paraphrase (not gated) | 3 | 0.00 | 0.00 | 0.00 | 0.00 |

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

## Chat page, API and evaluation (Phase 7)

`python -m corpusstudy.api` serves the chat page and the API on `CORPUS_API_HOST:CORPUS_API_PORT` (default `127.0.0.1:8000`).

| Endpoint | What it does |
|---|---|
| `GET /` | The chat page: English or French, answers with verified Scripture chips (tap one to read the passage), numbered sources, and a **Reviewed** or **Draft** badge on knowledge |
| `POST /chat` | `{"message": ..., "language": "en" or "fr" (optional), "history": [...]}` returns the answer, its citations, sources, route and outcome |
| `GET /passage?ref=Jean 3.16&lang=fr` | A passage in that language's translation and numbering |
| `GET /health` | Index state, whether an API key is set, the model, and the drafts setting |

Without an API key, lookups and the safety messages still work, and other questions return 503.

**Answer evaluation.**
- **The sets:**
  - `eval/answers.yaml` has 61 questions: lookups, doctrine, people, history, books, questions where traditions differ, open questions, 8 safety cases, and 3 that must not trigger a safety message. Each has a `gist`, the expected answer, for your review.
  - `eval/answers_holdout.yaml` has 16 questions written afterwards and never used to tune the router.
- **Offline checks** (`python -m corpusstudy.generation eval`, also run by the tests):
  - routing, language detection and the concept named;
  - the safety message comes first in pastoral cases, and no other case raises one;
  - lookups return the right passage, or report a reference that doesn't exist.
- **Live checks** (`--live`, needs the key): every question is answered through the API, measuring citation accuracy, the share answered, and whether the expected passages are cited.

| Set | Routing | Language | Concept | Safety first | No false alarm | Lookups |
|---|---|---|---|---|---|---|
| Tuned (61) | 61/61 | 61/61 | 33/33 | 8/8 | 53/53 | 10/10 |
| Held-out (16) | 16/16 (13/16 on first run) | 16/16 | 7/7 | 2/2 | 14/14 | 3/3 |

On its first run the held-out set exposed one real defect: "Saul" was an alias of Paul, so a question about King Saul went to Paul. Ambiguous and generic aliases were then removed from 12 concepts. Two of my held-out expectations were wrong and are corrected in the file, with a note.

## Spec alignment (Phase 7b)

These changes close the gaps found when comparing the build with the original system prompt. Where the two conflict, your Phase 0 decisions win: the Reformed stance stays primary (other traditions are presented fairly and labelled), sources stay public domain, and search stays keyword-only for now.

- **Answer layout:**
  - **Answer**, **Key passages**, **Context** and **Interpretive perspectives** (Reformed first), with French headings for French answers.
  - A **Sources** section is added by the code: Bible references with the translation, numbered commentary (public domain), and knowledge files with their review status.
  - Every reference carries its translation ("John 3:16 (KJV)", "Jean 3.16 (LSG)").
  - Follow-up questions only when useful.
- **"I cannot verify this passage from the available sources."** (French: "Je ne peux pas vérifier ce passage à partir des sources disponibles.") This exact sentence is used for references that don't exist, for answers whose citations fail validation, and, per the model's instructions, for passages the evidence doesn't support.
- **Translations:**
  - Asking for the ESV, NIV, Semeur and so on gets a note that only the KJV and Louis Segond 1910 are loaded, then the KJV or Louis Segond text.
  - Asking for the KJV or Louis Segond explicitly is honoured: "John 3:16 in Louis Segond" is answered in French.
- **Care rules:**
  - Medical, legal and financial questions get a fixed note first and general biblical context only.
  - Requests to demean or harass people are declined without a model call.
  - The model is told not to ask for personal information.
- **Logging:** one log line per answer (route, outcome, citations, tokens, estimated cost, time), never the question or the answer. Each response also carries its usage.
- **New routes:**
  - **crossref** (public-domain cross-references);
  - **compare** (evidence searched book by book: "the four Gospels", "the synoptics", or two named books with a comparing word);
  - **interpretive** (traditions and interpretations).
  - With `CORPUS_LLM_ROUTER=true`, a model call can refine open questions into these routes (off by default; it costs one extra call).
- **Knowledge:**
  - 17 more concepts: Revelation; faith, forgiveness, justice, love, salvation; Jesus Christ, Isaiah the prophet; the Trinity, the Incarnation; the Exile, the Life of Jesus, Pentecost; and the new **Parable** type with the Prodigal Son, the Good Samaritan and the Sower.
  - Aliases that named the wrong concept or matched too many questions were removed.
- **Evaluation:**
  - The tuned set has 83 cases; the new categories are cross-references, comparisons, translations, unsupported questions, advice, refusals and interpretive questions.
  - The held-out set has 22 cases, 6 of them written for the new categories.
  - All offline targets are met. On their first run the six new held-out cases routed 5 of 6: "passages parallel to" was missed, and is now fixed.
  - The live run adds "cannot verify" correctness, cost per question and an optional faithfulness judge (`--judge`).

## Phases

The full plan, including how the knowledge layer follows Google's Open Knowledge Format (OKF) v0.2, is in [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md).

| Phase | Scope | Who | Status |
|---|---|---|---|
| 0 | Decisions | You | ✅ Done, including the OKF review decisions |
| 1 | Skeleton, config, canon, schemas | Claude | ✅ Done, OKF-aligned |
| 2 | Corpus: KJV, LSG 1910, Calvin, Henry | Claude (+ your files) | ✅ Done, published as OKF source references (TSK skipped) |
| 3 | OKF bundle foundation: loader, validator, lifecycle (new, touch, verify, deprecate), graph, index/log, CLI, CI | Claude | ✅ Done |
| 4 | Chunking, FTS5 keyword search (vectors later), OKF-aware retrieval, evaluation | Claude | ✅ Done |
| 5 | Knowledge concepts (about 36) in OKF | Claude drafts, **you review** | 🟡 37 concepts drafted (54 with Phase 7b); awaiting your review |
| 6 | Router, generation, citation and provenance validator | Claude | 🟡 Built and tested offline; live smoke test needs your API key |
| 7 | API, chat page, evaluation set | Claude, **you validate** | 🟡 Built; offline targets met; awaiting your review of the expected answers and a live run |
| 7b | Spec alignment: answer layout, translations, care rules, logging, new routes, 17 more concepts, evaluation | Claude, **you review** | 🟡 Built; offline targets met; 54 concepts await review |
| 8 | Docker, local run | Shared | — |
| 9 | Knowledge-base maintenance (optional) | Claude proposes, you review | — |

## License

Apache 2.0, see [LICENSE](LICENSE). The Bible texts and commentaries used are in the public domain.
