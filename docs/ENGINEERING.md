# CorpusStudy engineering guide

This guide is for the software engineer who maintains CorpusStudy. It explains how the application is built, what must not break, and how to make the common changes safely. Other documents cover the rest:

| Document | For |
|---|---|
| [`README.md`](../README.md) | What the project is, phase by phase |
| [`docs/RUNBOOK.md`](RUNBOOK.md) | Running it day to day with Docker (the owner's guide) |
| [`docs/KNOWLEDGE_FORMAT.md`](KNOWLEDGE_FORMAT.md) | The knowledge bundle: the OKF format, the producer profile, the review workflow |
| [`docs/BUILD_PLAN.md`](BUILD_PLAN.md) | How it was built, the decisions taken, and the status of each phase |

CorpusStudy is a personal Bible-study assistant. It runs for one person, on their own computer. It answers questions in English and French from four public-domain sources:

- the King James Version (KJV, 1769);
- the Louis Segond 1910 (LSG);
- Calvin's commentaries (Library of Christian Classics XXIII selections);
- Matthew Henry's Concise Commentary.

It also draws on a curated knowledge bundle that a human reviewer approves. Answers are framed from the Reformed (Protestant, Calvinist) position, and other views are labelled.

Appendix A covers extending the application: A.1 explains how to add new sources, A.2 how to add new knowledge concepts, A.3 how to add new routes, and A.4 how to tune retrieval.

---

## 1. Ground rules (invariants)

Every change must keep these true. Most are enforced by tests or by schema validation; a few are policy, and the review is the only guard.

| # | Rule | Enforced by |
|---|---|---|
| 1 | **Public domain only.** The corpus holds only the KJV, the LSG 1910, Calvin and Henry, and every knowledge source points into `/references/sources/`. | `config.PUBLIC_DOMAIN_TRANSLATIONS`, the `ALLOWED_COMMENTARIES` validator, the producer profile (`okf/profile.py`) |
| 2 | **Commentary can never pass as Scripture.** Scripture chunks carry a translation; commentary chunks never do. | `schemas.Chunk` validators |
| 3 | **No unverified citation reaches the user.** Every reference, quotation, `[E#]` marker and knowledge citation in a generated answer is checked against the corpus. An answer that fails gets one repair round; if it fails again, the user sees the retrieved passages instead. | `validation/citations.py`, `generation/answer.py` |
| 4 | **Only human-reviewed knowledge is used in answers**, unless `CORPUS_USE_DRAFTS=true`. Only `okf verify` with a `human:` actor makes a concept usable. | `okf/graph.usable`, `okf/lifecycle.verify`, the producer profile |
| 5 | **Safety text is written by code, not by the model.** This covers the crisis message, the medical, legal and financial note, the refusal and the translation note. | `generation/answer.py` (`SAFETY`, `_TEXT`), tests |
| 6 | **Questions are never logged.** The one exception is the opt-in gap log (`CORPUS_GAP_LOG`, off by default). | `answer._log`, `answer._record_gap` |
| 7 | **The API key never enters git or the image.** It lives only in the git-ignored `.env` or the environment. | `.gitignore`, `.dockerignore`, `tests/test_prepare.py` |
| 8 | **The page is reachable from the local machine only** (`127.0.0.1`). There is no authentication, so it must never be exposed. | `compose.yaml`, `tests/test_prepare.py` |
| 9 | **Sources are pinned.** Every download URL names a git commit, and every file is checked against `resources/sources.lock.json`. | `ingest/sources.py` |
| 10 | **The Reformed stance is primary**; other traditions are presented fairly and labelled. | `generation/prompts.SYSTEM`, the concept review |

The owner's decisions (Phase 0 and Phase 7b) override the original system-prompt spec where the two conflict: the Reformed stance stays primary, sources stay public domain only, and search stays keyword-only.

---

## 2. Architecture

### 2.1 Two pipelines

**Build time** turns the sources into a corpus and an index. It runs offline after the first download, and again whenever a source or the knowledge changes.

```
pinned downloads ──► ingest ──► data/processed/*.jsonl ──┐
(KJV OSIS, LSG USFM)  (parse, repair, validate, align)   │
hand-imported files ─► ingest --import ──► commentary/*.jsonl ─┤
(calcom.doc, mhcc.txt)                                    ├──► indexing ──► indexes/corpus.sqlite
knowledge/ (OKF bundle, git) ─────────────────────────────┘   (chunks, FTS5, refs, xrefs)
```

**Request time** answers one question:

```
POST /chat ─► router ─► retrieval ─► evidence pack ─► model (Anthropic API, structured JSON)
                │                                             │
                │ refuse / lookup / pastoral notes            ▼
                │ (no model call where possible)      citation validator ──► repair once ──► refuse
                ▼                                             │
          fixed texts ◄───────────────────────────── answer + Sources section + usage + log line
```

### 2.2 Package map (`src/corpusstudy/`)

| Module | Responsibility | Entry point |
|---|---|---|
| `config.py` | All settings (`CORPUS_*`, `ANTHROPIC_API_KEY`, `AI_PROVIDER`), with validators | `get_settings()` (cached) |
| `canon.py` | The 66 books: English and French names, OSIS and USFM codes, chapter counts, aliases | `find_book()` |
| `schemas.py` | Pydantic models for every stage: references, verses, chunks, answers, chat API, usage | |
| `versification.py` | Verse counts per translation; KJV ↔ LSG reference mapping | `versification(tr)`, `map_passage()` |
| `corpus.py`, `commentary.py` | Read access to the processed Bibles and commentaries | `bible(tr)`, `commentary(source)` |
| `ingest/` | Download, parse (OSIS, USFM, ThML, CCEL text, CSV/JSON), repair, validate, align and write the corpus | `python -m corpusstudy.ingest` |
| `indexing/` | Text analysis (stems, KJV archaisms, French elision), chunking, the SQLite FTS5 index | `python -m corpusstudy.indexing` |
| `retrieval/` | Reference extraction, hybrid OKF-aware search with rank fusion, evaluation | `python -m corpusstudy.retrieval` |
| `router/` | Deterministic routing and the advice and translation modifiers | `route()` |
| `generation/` | Prompts, the Anthropic call, the answer pipeline, answer evaluation | `python -m corpusstudy.generation` |
| `validation/` | The citation and provenance validator | `validate()` |
| `okf/` | The knowledge bundle: loader, producer profile, quotation check, lifecycle, graph, indexes, maintenance, proposals | `python -m corpusstudy.okf` |
| `api/` | FastAPI app and the static chat page | `python -m corpusstudy.api` |
| `prepare.py` | Brings the corpus and index up to date on start | `python -m corpusstudy.prepare` |
| `resources/` | Committed tables: versification, the KJV ↔ LSG mapping, source checksums | |

Dependencies point one way: `api → generation → (router, retrieval, validation) → (indexing, okf) → (corpus, commentary, versification, canon, schemas, config)`. Some imports sit inside functions to break cycles; keep it that way:
- `okf.maintenance` imports retrieval and the router lazily;
- `okf.propose` imports generation;
- the OKF CLI imports retrieval lazily.

---

## 3. Files and data

| Path | In git? | Written by | Notes |
|---|---|---|---|
| `src/corpusstudy/resources/sources.lock.json` | yes | `ingest` (`--update-lock`, `--import`) | SHA-256 of every source file |
| `src/corpusstudy/resources/versification/*.json` | yes | `ingest` | Verse counts and the KJV ↔ LSG links; used offline by the validator and the profile |
| `knowledge/` | yes | people, `okf` commands, `ingest` (`references/sources/*.md`) | The OKF v0.2 bundle; see `KNOWLEDGE_FORMAT.md` |
| `eval/*.yaml` | yes | people | Retrieval and answer evaluation sets, tuned and held-out |
| `data/raw/<source>/` | no | `ingest` | Downloaded and imported source files |
| `data/processed/{KJV,LSG1910}.jsonl` | no | `ingest` | One verse per line, in the translation's own numbering |
| `data/processed/commentary/<source>.jsonl` | no | `ingest` | One excerpt per line |
| `data/processed/manifest.json` | no | `ingest` | Sources, checksums and counts; feeds `okf references` |
| `indexes/corpus.sqlite` | no | `indexing` | The search index (schema version `store.SCHEMA_VERSION`) |
| `imports/` | no | the owner | `calcom.doc` and `mhcc.txt` for the Docker start-up import |
| `.env` | no | the owner | API key and settings |

CI fails if building the corpus changes any committed file (`git diff --exit-code`). If you change a parser in a way that changes the versification tables or the source references, commit the regenerated files in the same change.

---

## 4. Development setup

Python 3.11 or later (CI and Docker use 3.12).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m corpusstudy.ingest                       # downloads and builds the two Bibles
python -m corpusstudy.ingest --import calvin-lcc ~/calcom.doc --import henry-concise ~/mhcc.txt
python -m corpusstudy.indexing                     # builds indexes/corpus.sqlite
python -m corpusstudy.api                          # http://127.0.0.1:8000
```

The commentaries come from CCEL, which can't always be downloaded automatically, so they are imported by hand once. `imports/README.md` says where to get them. Without them, everything except commentary retrieval works, and the related tests skip.

Everyday commands:

```bash
ruff check src tests && ruff format --check src tests     # lint (line length 100)
pytest                                                    # full suite (about 30 s with the corpus)
python -m corpusstudy.okf check                           # knowledge bundle: 0 errors required
python -m corpusstudy.retrieval eval                      # recall@k / MRR per category
python -m corpusstudy.generation eval                     # offline answer evaluation
python -m corpusstudy.generation route "Who was Abraham?" # how a question is routed
python -m corpusstudy.retrieval search "grace through faith" -k 10   # fused results, with lists
python -m corpusstudy.retrieval show henry-concise:Rom.3.21-Rom.3.26 # a source in full
python -m corpusstudy.generation ask "What does Romans 5:1 teach?"  # live (API key)
python -m corpusstudy.okf maintain                        # knowledge maintenance report
```

Code style:
- ruff (rules E, F, I, B, UP), line length 100.
- A few deliberately aligned blocks carry `# fmt: skip`.
- Comments explain why, not what.
- User-facing text is in plain English and French.

---

## 5. Configuration

All settings come from environment variables or `.env`; see `config.Settings` and `.env.example`.

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Needed for generated answers; lookups and safety messages work without it |
| `AI_PROVIDER` | `anthropic` | The only provider implemented |
| `CORPUS_LLM_MODEL` | `config.DEFAULT_MODEL` | Model id; add its price to `llm.PRICES` |
| `CORPUS_LLM_EFFORT` | `medium` | `low` … `max`: thinking depth and cost |
| `CORPUS_LLM_MAX_TOKENS` | `16000` | Output cap per call |
| `CORPUS_LLM_ROUTER` | `false` | One extra call to refine the route of open questions |
| `CORPUS_DEFAULT_LANGUAGE` | `en` | When detection is unsure |
| `CORPUS_STANCE` | `reformed` | The only stance implemented |
| `CORPUS_SHOW_OTHER_VIEWS` | `true` | Label and present other traditions |
| `CORPUS_REVIEWER` | `human:dhnguyen` | The OKF actor `okf verify` stamps; must start with `human:` |
| `CORPUS_USE_DRAFTS` | `false` | Use unreviewed concepts in answers (labelled as drafts) |
| `CORPUS_GAP_LOG` | off | JSON-lines file of topical questions answered without a reviewed concept |
| `CORPUS_EMBEDDINGS` | `none` | `voyage` and `local` are accepted but not implemented (see §11) |
| `CORPUS_DATA_DIR`, `CORPUS_INDEX_DIR`, `CORPUS_KNOWLEDGE_DIR`, `CORPUS_IMPORTS_DIR` | repo paths | Relative paths resolve from the repository root |
| `CORPUS_API_HOST`, `CORPUS_API_PORT` | `127.0.0.1`, `8000` | Inside Docker, `0.0.0.0` with only `127.0.0.1` published |

`get_settings()` is cached. Tests build `Settings(_env_file=None, ...)` and monkeypatch the module's `get_settings`. Don't read `os.environ` directly.

---

## 6. Subsystems

### 6.1 Ingestion and versification (`ingest/`, `versification.py`)

- **Pipeline** (`pipeline.run`): fetch (pinned URL, checksum) → parse → repair → validate → write the processed JSONL. With `write_resources=True`, it also writes the committed versification tables and the source-reference concepts.
  - `prepare` passes `write_resources=False`: it never rewrites committed files.
- **Versification:** the two Bibles number about 150 chapters differently (psalm titles counted as verses, Hebrew chapter breaks, split verses). `align.py` aligns runs of differing chapters with a Gale–Church style dynamic programme. `versification.map_passage` converts references between the two.
  - Commentary and knowledge are keyed in **KJV numbering**. A French question's references are mapped before reference retrieval.
- **A checksum mismatch** means the source changed upstream. Don't pass `--update-lock` blindly:
  1. diff the texts;
  2. check what the change does to the versification tables and to the quotations in `knowledge/` (`okf check` re-verifies them);
  3. commit the new lock and tables together.
- **Commentaries** (`ingest/commentary.py`, `thml.py`, `ccel_text.py`): excerpt ids are `<source>:<local id>`, such as `henry-concise:Rom.3.21-Rom.3.26` or `calvin-lcc:vi-p90`.
  - Ids are stable for a given imported file, because its checksum is locked.
  - Knowledge concepts cite these ids, so a re-import that renumbers excerpts breaks citations. `okf check` reports this.

### 6.2 The search index (`indexing/`)

- **Chunks:**
  - Bible: windows of 3 verses with a stride of 2, never splitting a sentence, at most 10 verses (`chunking.bible_chunks`).
  - Commentary: one chunk per excerpt, with long ones split on sentences.
  - Knowledge: one chunk per `#` section, carrying trust metadata and `content` (summary, biblical_text, historical_context, interpretation).
  - Single-verse Bible chunks were tried and rejected: they rank worse.
- **Tables** (`store.py`): `chunks`, `refs` (passage ranges as `chapter*1000+verse`), `fts` (FTS5 over pre-computed stems; the tokenizer only splits on spaces), `xrefs` (cross-references) and `meta`.
- **Staleness:** `build.index_status` returns `stale` when any input file changed (a fingerprint of the corpus files and every knowledge `.md`), or when the index was built with another `SCHEMA_VERSION`. `prepare` rebuilds a stale index on start.
  - **Bump `store.SCHEMA_VERSION` whenever you change chunking, the analysis or the tables.** Otherwise a deployed index, kept in the Docker volume, is not rebuilt.
- **Analysis** (`analysis.py`): Snowball stems. English folds KJV archaisms ("loveth" → "loves", "thou" → "you"); French drops elisions. The same code runs at indexing time and at query time, so any change here needs a rebuild and a schema bump.

### 6.3 Retrieval (`retrieval/search.py`)

For each kind (bible, commentary, knowledge), a query produces ranked lists. They are fused with weighted reciprocal rank fusion (`RRF_K = 60`), and the kinds are then interleaved so the model sees all three:

| List | Weight | What it finds |
|---|---|---|
| `lookup` | 3.0 | The passage the question cites, read from the Bible |
| `phrase` | 1.5 | The question's words in sequence (a quoted verse) |
| `concept` | 1.2 | Knowledge concepts named by title or alias (whole words, not stems) |
| `reference` | 1.2 | Commentary and knowledge about a cited passage (KJV numbering) |
| `all` | 1.0 | Every content word present (BM25) |
| `hits` | 0.8 | Commentary and knowledge about the verses the words matched best |
| `keyword` | 0.7 | Any content word (BM25) |
| `graph` | 0.5 | One OKF link hop from the best concepts |

Other rules:
- Terms of four or more letters match as prefixes.
- Naming "Henry" or "Calvin" restricts commentary to that work.
- `books=` and `testament=` filter the results (the compare route uses `books=`).
- **Concept names:** titles, aliases and multi-word file names, matched as folded whole words.
  - A one-word file name is not a name.
  - Hyphenated words are joined first ("cross-references" does not name the Crucifixion).
  - Alias hygiene matters: see `KNOWLEDGE_FORMAT.md`, "Names".

**Tuning procedure** (in full in Appendix A.4):
1. Change the weights or lists.
2. Run `python -m corpusstudy.retrieval eval`, and compare the gated recall@5, recall@10 and MRR, and each category, before and after.
3. Run the held-out set **only to confirm**, never to tune: `--cases eval/retrieval_holdout.yaml`.
4. Keep the gates green. `tests/test_retrieval.py` enforces:
   - tuned set: recall@10 ≥ 0.95, recall@5 ≥ 0.9, MRR ≥ 0.75, and 100% recall@5 on quote and reference cases;
   - held-out set: recall@10 ≥ 0.9.

   The current figures are recall@5 1.00 and MRR 0.89 on the tuned set, and MRR 0.85 on the held-out set.

### 6.4 Routing (`router/route.py`)

Rules are checked in order, and the first match wins:

1. **refuse:** demeaning or harassing requests.
2. **pastoral:** self-harm, abuse or despair, in English and French.
3. **lookup:** the question only asks for a passage, possibly naming a translation.
4. **crossref**
5. **compare:** book groups such as "the synoptics" are checked before "the Gospels".
6. **interpretive**
7. **knowledge / hybrid:** the question names a usable concept; hybrid also cites a passage.
8. **rag:** anything else.

Two modifiers can apply to any route:
- `advice`: medical, legal or financial;
- `translation`: a loaded or unavailable translation was asked for.

`invalid_refs` records references that don't exist.

The router is deterministic regular expressions, so test every change against both evaluation sets:

```bash
python -m corpusstudy.generation eval                   # all offline targets must stay at 100%
python -m corpusstudy.generation eval --cases eval/answers_holdout.yaml
```

With `CORPUS_LLM_ROUTER=true`, open (`rag`) questions get one extra model call (`LLM.classify`) that may re-route them.

**Adding a route** (step by step in Appendix A.3):
1. add a value to `schemas.Route`;
2. add a rule in `route.py` at the right place in the order;
3. handle it in `answer._gather` or `_notes`, with a note in `prompts.ROUTE_NOTES` if needed;
4. update `okf.maintenance.TOPICAL` if questions on it should be covered by concepts;
5. add the label to `api/static/index.html`;
6. add evaluation cases to both sets and tests in `tests/test_generation.py`.

### 6.5 Generation (`generation/`)

- **`answer.Answerer.answer`** is the single entry point. In order, it:
  1. routes the question;
  2. writes the fixed notes (safety, advice, translation);
  3. gathers evidence;
  4. makes one model call;
  5. validates the answer;
  6. runs at most one repair, then shows the answer or refuses;
  7. appends the Sources section, the usage and one log line.
- **`prompts.py`:**
  - `SYSTEM` is fixed and cached: the stance, the layout (Answer / Key passages / Context / Interpretive perspectives), citation rules and care rules.
  - Everything variable goes in the user turn: the evidence block, the language and the route notes.
  - The reply is structured JSON (`ANSWER_SCHEMA`: `answer`, `interpretive`).
- **`llm.py`:**
  - `beta.messages.create` with `output_config` (effort, JSON schema), adaptive thinking and a cached system prompt.
  - Server-side fallback: `fallbacks: "default"`, with the beta `FALLBACK_BETA`.
  - Usage and an estimated cost come from `PRICES`. An unknown model gives `cost_usd = None`.
- **Fixed texts:**
  - `answer.SAFETY` holds the crisis lines in English and French;
  - `answer._TEXT` holds the advice, refusal, translation and unavailable texts;
  - `prompts.CANNOT_VERIFY` is the exact "I cannot verify this passage from the available sources." sentence.

  These are reviewed by the owner. Change them only with the owner, and keep the English and French texts in step.
- **Evidence** (`evidence.py`): Scripture is cited by reference in the text; commentary and knowledge by `[E#]`, renumbered `[1]`, `[2]`… in order of first use.

**Changing the model:**
1. set `CORPUS_LLM_MODEL` or `config.DEFAULT_MODEL`;
2. add its price to `llm.PRICES` (input, output, cache read, cache write, per million tokens);
3. check that the model supports structured outputs, `effort` and the fallback beta;
4. run `python -m corpusstudy.generation smoke`, then `eval --live --judge` and compare citation accuracy and cost.

### 6.6 Citation validator (`validation/citations.py`)

The validator checks four things:

1. **References** exist in the answer language's translation and numbering, and a book name followed by impossible numbers is flagged.
2. **Quotations of three or more words** (`"…"`, `“…”`, `« … »` or block quotes) occur word for word in:
   - the referenced passage;
   - the Scripture evidence;
   - or an `[E#]` item in the same paragraph.

   An ellipsis splits a quotation into fragments that must each occur.
3. **Every `[E#]`** names evidence that was provided.
4. **Every cited concept** still passes the producer profile.

Loosening any check weakens invariant 3; discuss it with the owner first.

### 6.7 Knowledge bundle (`okf/`)

See `KNOWLEDGE_FORMAT.md` for the format. For engineers:
- **Tolerant reader, strict producer:** `bundle.load_bundle` accepts any OKF v0.2 content, while `profile.check_concept` enforces our rules. Quotations are checked word for word against the cited source (`quotes.py`).
- **Lifecycle** (`lifecycle.py`):
  - `new` drafts a skeleton;
  - `touch` records an edit and sends a stable concept back to draft;
  - `verify` is the human gate: it refuses non-`human:` actors and concepts that fail the profile;
  - `deprecate` retires a concept.

  Each writes a `log.md` entry and regenerates `index.md`.
- **Adding a concept type:**
  1. add it to `profile.TYPES` (type → folder);
  2. add its sections to `lifecycle.TEMPLATES`;
  3. add the folder heading to `indexes.SECTIONS`;
  4. add its section headings to the content map in `indexing/chunking.py` (`SECTION_CONTENT`);
  5. add it to `store._LINKING_TYPES` if its key passages should create cross-references;
  6. update `KNOWLEDGE_FORMAT.md`.

  The Parable type (Phase 7b) is the worked example.
- **The `okf` CLI** writes `knowledge/` in place. In Docker the app mounts it read-only, and the `okf` tools service mounts it read-write.

### 6.8 Knowledge maintenance (`okf/maintenance.py`, `okf/propose.py`)

- **`okf maintain`** builds a report that needs no API key. It covers:
  - check errors;
  - the review queue, ranked by questions answered;
  - edits, staleness and regenerated sources since review;
  - links;
  - book coverage;
  - question coverage.

  It exits 1 on check errors.
- **Coverage rule:** a question is covered by a concept it names, or by one retrieved through the `concept`, `phrase`, `all` or `reference` lists (`maintenance.STRONG`). Questions that need no concept are skipped (`needs_concept`): lookups, cross-references, pastoral, refused, advice and invalid references.
- **Gap log:** `answer._record_gap` appends `{at, q, lang, route}` when `CORPUS_GAP_LOG` is set and no human-reviewed knowledge source was used. A failed write never fails an answer.
- **`okf propose`:**
  - Gathers evidence: KJV and commentary only, with excerpt parts merged.
  - Makes one model call with `propose.DRAFT_SYSTEM` and `DRAFT_SCHEMA`.
  - Assembles the concept in code: `sources` and footnote definitions come from the evidence, never from the model.
  - Runs the profile check, with one repair round, then refuses if the draft still fails.
  - Writes a draft and logs it. It never verifies.
- **The weekly workflow** (`.github/workflows/maintenance.yml`) builds the Bible corpus and the index, then publishes the report. Scheduled runs happen only on the default branch.

### 6.9 API and chat page (`api/`)

| Endpoint | Behaviour |
|---|---|
| `GET /` | The chat page (`api/static/index.html`: a single file, no build step, light and dark themes) |
| `POST /chat` | `ChatRequest {message ≤4000 chars, language?, history ≤20 turns}` → `ChatResponse {answer, warnings, usage}`; 503 when a model call is needed and no key is set |
| `GET /passage?ref=…&lang=en\|fr` | Passage text in that language's translation; 422 for a non-reference, 404 for a reference that doesn't exist, 503 if the corpus isn't built |
| `GET /health` | `{index: fresh\|stale\|missing, api_key, model, use_drafts}`; used by the Docker health check |

The answerer is built lazily on the first request. Searches share one SQLite connection behind a lock (`LockedSearch`), which is enough for one user. The page renders the answer's own source list and hides the generated Sources section.

### 6.10 Start-up and Docker (`prepare.py`, `Dockerfile`, `compose.yaml`)

- **`prepare()`**, run by `python -m corpusstudy.api --prepare`:
  1. imports new or changed files from `imports/`;
  2. builds the corpus when an output is missing or a commentary was imported;
  3. rebuilds a missing or stale index.

  It raises `PrepareError` with a readable message, for example when it is offline or a checksum is wrong.
- **Image:**
  - `python:3.12-slim-bookworm`, non-root user `app`, editable install under `/app`.
  - An optional `extra_ca` build secret for proxies that inspect TLS.
  - The health check bypasses proxies.
- **Compose:**
  - service `corpusstudy`: port `127.0.0.1:${CORPUS_HOST_PORT:-8000}`, `.env` optional, volumes `corpus-data` and `corpus-index`, `knowledge/` and `imports/` read-only;
  - service `okf` (profile `tools`): the same image with the `okf` CLI as entrypoint, `knowledge/` read-write, and the data and index volumes.
- **After a code change:** run `docker compose up -d --build`. The volumes keep the corpus. The index is rebuilt automatically only if its inputs or `SCHEMA_VERSION` changed (§6.2).

---

## 7. Testing and evaluation

| Layer | Where | Needs |
|---|---|---|
| Unit and integration tests | `tests/test_*.py`, one file per subsystem | Some tests need the corpus or index and skip without them (`needs_corpus`, `needs_index`); commentary tests skip without the imported files |
| Retrieval evaluation | `eval/retrieval.yaml` (tuned), `eval/retrieval_holdout.yaml` | The index; gated in `tests/test_retrieval.py` |
| Answer evaluation, offline | `eval/answers.yaml` (83 cases), `eval/answers_holdout.yaml` (22) | The index; all targets must stay at 100% (`evaluation.OFFLINE_TARGETS`) |
| Answer evaluation, live | `python -m corpusstudy.generation eval --live [--judge]` | The API key; it costs money. It reports citation accuracy, the all-verified rate, safety-first, the cannot-verify correctness, cost per question and, with `--judge`, faithfulness (`LIVE_TARGETS`) |
| Bundle | `python -m corpusstudy.okf check` | 0 errors |

Conventions:
- **Fakes, not mocks:** `FakeSearch` and `FakeClient` in `tests/test_generation.py`, and the `world` fixture in `tests/test_retrieval.py` builds a tiny real index. API calls are never made in tests.
- **Bundle tests** start from `conftest.copy_bundle_skeleton` (the structure and source references, without the drafted concepts).
- **Two corpus states must both pass:**
  - full, with the commentaries (local): 364 tests;
  - Bible-only, as in CI (`checks` job): commentary tests skip.
- **Held-out sets** confirm a change; never tune on them. When a held-out case fails, fix the general rule and add a tuned case for it.

---

## 8. CI/CD

| Workflow | Trigger | What it does |
|---|---|---|
| `.github/workflows/ci.yml`, job `checks` | push, pull request | Lint and format; build the Bible corpus; **fail if committed files drift**; build the index; pytest; `okf check` |
| `ci.yml`, job `docker` | push, pull request | Build the image, `docker compose up`, wait until healthy, query a lookup through the API |
| `.github/workflows/maintenance.yml` | Mondays 06:17 UTC, manual | The maintenance report in the job summary and as an artifact |

There is no deployment pipeline: the owner runs `docker compose up -d --build` on their own computer. Changes reach `main` through pull requests. The knowledge review happens in the same pull requests: the owner approves a concept, and `okf verify` is run as them.

---

## 9. Recipes

**Rotate the API key.** Revoke the old key in the Anthropic console, put the new one in `.env`, then run `docker compose up -d` (a restart doesn't re-read `.env`).

**Update prices or the model.** See §6.5. Prices are estimates for the log and the evaluation; nothing is billed from them.

**Upgrade dependencies.**
1. Bump the version ranges in `pyproject.toml`.
2. Reinstall, then run the full suite in both corpus states, the offline evaluation and one live smoke test.
3. The Anthropic SDK is pinned below 3. Check `beta.messages.create` arguments, `output_config` and fallbacks after any SDK upgrade.

**Update a pinned source.**
1. Change the commit in `ingest/sources.py`.
2. Run `python -m corpusstudy.ingest --update-lock`.
3. Review the diffs in `resources/` and `knowledge/references/`.
4. Run `okf check`: quotations are re-verified against the new text.
5. Commit everything together.

**Add a new source** (commentary, translation or cross-references): see Appendix A.1.

**Re-import a commentary.**
1. Run `python -m corpusstudy.ingest --import <source> <file>`.
2. Run `okf check`, which catches excerpt ids or texts that changed under existing citations.
3. Run the retrieval evaluation.
4. Rebuild the index.

**Add a third language or translation.** This is a design change, not a configuration change:
- `config.Translation`, `TRANSLATION_LANGUAGE` and `Settings.translations` map exactly one translation per language;
- references, quotations and the validator follow the answer language's translation;
- you need a parser, a pinned source, versification tables and the mapping to KJV, analysis rules for the language (stemmer, stopwords), concept `title_<lang>` and aliases, fixed texts in that language, and evaluation cases.

Plan it as a phase; Appendix A.1.4 lists every step.

**Add embeddings (vectors).** `CORPUS_EMBEDDINGS` is reserved but not implemented. Add a `vector` list per kind to `Search.search`, give it a weight, store the vectors alongside the index (and bump `SCHEMA_VERSION`), and tune on the `paraphrase` category, which keyword search handles worst. Keep the gates green.

**Change a fixed text** (safety, advice, refusal or translation). Edit `answer.SAFETY` or `answer._TEXT` in both languages. The safety tests and the offline evaluation check that they appear first. The owner reviews crisis numbers and wording.

**Add a new knowledge concept:** see Appendix A.2.

**Approve a concept.** Only on the owner's explicit approval: `python -m corpusstudy.okf verify <concept> --by human:<id>`, commit, then rebuild or restart.

---

## 10. Operations

- **Logs:** one JSON line per answer on the `corpusstudy.answers` logger:
  - `route`, `outcome`, `language`, `reason`;
  - `citations`, `verified`, `sources`, `warnings`;
  - `calls`, `input_tokens`, `output_tokens`, `cost_usd`, `seconds`.

  The question and the answer are never logged. Start-up preparation logs on `corpusstudy.prepare`. Read them with `docker compose logs -f`.
- **Outcomes** (`schemas.Outcome`):
  - `answered`, `repaired`, `lookup`: normal;
  - `refused`: citations failed twice, and the passages were shown instead;
  - `declined`: the model declined;
  - `safety`: the safety message only, because no key was set.

  A rising share of `repaired` or `refused` signals a prompt, retrieval or model problem. Run the live evaluation.
- **Cost:** each response carries `usage`, and the chat page shows it. The live evaluation reports cost per question.
- **Health:** `GET /health`. A `stale` index self-heals on the next start.
- **Backups:** only `knowledge/` (git) and `.env` matter. The volumes can be rebuilt.

---

## 11. Known limitations and future work

- **Keyword search only.** Paraphrased questions in modern wording retrieve less well; the `paraphrase` category is tracked but not gated. Embeddings are the planned remedy (§9).
- **Cross-references come from public-domain sources only:** Henry and Calvin mentions, plus key passages of concepts. They cover about 26% of verses. The Treasury of Scripture Knowledge was skipped by decision.
- **French questions reach commentary only by reference.** Both commentaries are in English, so French words don't search them; references and matched verses do.
- **The knowledge bundle awaits review.** All 53 concepts are drafts until the owner approves them, so answers use no knowledge until then. `okf maintain` ranks the queue.
- **Single user, no authentication.** One SQLite connection behind a lock, bound to `127.0.0.1`. Serving more than one person would need authentication, a connection per request or a pool, and rate limiting.
- **Live checks are pending:** the smoke test, the live evaluation and the first `okf propose` need the owner's API key.
- **Book coverage:** 11 of 66 books have a Book concept; `okf maintain` lists the rest.

---

## 12. Troubleshooting (engineering)

| Symptom | Cause and fix |
|---|---|
| `IndexNotBuilt` or a 503 on `/chat` | Build the index with `python -m corpusstudy.indexing` |
| "the search index is older than its inputs" warning | Knowledge or the corpus changed; rebuild. In Docker, restart. |
| CI `git diff --exit-code` fails | A parser or pipeline change regenerated committed tables or source references; commit them, or fix the change |
| `okf check`: "quotation … not found" | A concept misquotes its source, or the source changed; fix the concept, not the check |
| "checksum mismatch" during ingest | An upstream file changed; see §6.1. Never `--update-lock` without reviewing the diff. |
| A question routes wrongly | `python -m corpusstudy.generation route "<question>"` shows the route, books, concepts and modifiers; look for an alias that is too generic (`KNOWLEDGE_FORMAT.md`, "Names") |
| Retrieval misses an obvious passage | `python -m corpusstudy.retrieval search "<question>"` shows which lists found each result; check the stems with `analysis.query_terms` |
| An answer is shown as "not verified: passages shown" | The response `warnings` (and the log line's count) say which check failed twice |
| Docker build fails at `pip install` behind a proxy | Build with the proxy's CA: `docker build --secret id=extra_ca,src=ca.crt -t corpusstudy:local .` (see the runbook) |
| Tests pass locally but fail in CI | CI is Bible-only and runs Python 3.12; run with commentaries absent, or check the `needs_*` skip markers |

---

## Appendix A. Extending the application

### A.1 How to add new sources

A *source* is any text the bot may quote or cite: a Bible translation, a commentary or a similar reference work, or a cross-reference dataset. Every source enters through the same gates. The text is pinned and checksummed, parsed into the corpus, described by an OKF `Reference` concept in `knowledge/references/sources/`, indexed, and then cited by concepts and answers through an anchor that `okf check` and the citation validator can resolve. Skipping a gate breaks one of the invariants in §1.

#### A.1.1 Before you start: is the source eligible?

Go through this list with the owner before writing any code. Adding a source is an owner's decision, recorded with the Phase 0 decisions in `docs/BUILD_PLAN.md`.

- [ ] **Public domain, for this edition.** An old work can sit in a copyrighted modern translation, revision or edition (Segond 21, for example, is not the Louis Segond 1910).
  - Check the edition you will actually ingest.
  - Record its licence; it ends up in the source's `Reference` concept.
  - A licence that limits redistribution of the *file*, but not the text (CCEL's PDF of Henry is an example), is acceptable for a personal installation. Say so in `license`.
- [ ] **A stable, pinnable copy.**
  - Best: a file in a git repository at a fixed commit, downloaded automatically.
  - Otherwise: a file the owner downloads by hand once and imports; its checksum is then locked.
- [ ] **Structure you can key to passages.** Commentary excerpts must map to OSIS passages (KJV numbering), or at least to a topic. Bible text needs book, chapter and verse.
- [ ] **Fit with the stance.** Sources are evidence the model quotes, and the Reformed reading is presented as primary. A non-Reformed source is possible, but the prompts must then label it, so decide that with the owner.
- [ ] **Size.** Very large works make the index, and the first Docker start, slower. Measure the build time and the index size.

#### A.1.2 Which kind of source?

| Kind | Examples | Work involved | Section |
|---|---|---|---|
| Commentary or reference work, keyed to passages or arranged by topic | Matthew Henry's full Commentary, the Geneva Bible notes, Calvin's Institutes (by topic, like the LCC selections), a catechism with proof texts | Moderate: a parser and some registrations | A.1.3 |
| Bible text **replacing** the translation of an existing language | another public-domain French edition | Moderate to large | A.1.4 |
| A **second** translation in a language, or a **new language** | ASV beside the KJV; German Luther 1912 | Large: a design change, plan it as a phase | A.1.4 |
| Cross-reference dataset | Treasury of Scripture Knowledge (skipped by decision) | Small to moderate | A.1.5 |

#### A.1.3 Adding a commentary or reference work

The two existing commentaries are the worked examples. Calvin (`calvin-lcc`) is a CCEL ThML file of excerpts arranged by subject. Henry (`henry-concise`) is CCEL text converted from a PDF, chapter by chapter. Work through the steps in order; each names the file to change.

1. **Allow the work** in `config.py`: add a key and a full bibliographic description to `ALLOWED_COMMENTARIES`, such as `"henry-full": "Matthew Henry, Commentary on the Whole Bible (1706-1721)"`. The key is the *work*; one work may have several source files or editions.

2. **Parse it into excerpts.** A parser returns `list[ingest.raw.Excerpt]`, and each excerpt has:
   - `id`: the **local id**, unique within the source and **stable across re-imports**, because concepts cite it as an anchor. Use letters, digits, dots and hyphens only. Prefer passage-based ids (`Rom.3.21-Rom.3.26`); for subject-arranged works, use document-position ids (`vi-p90`).
   - `refs`: the OSIS passages the excerpt comments on, in **KJV numbering**. Leave it empty for a purely topical excerpt; it is then reachable by words only.
   - `mentions`: other passages cited in the comment. They become cross-references.
   - `topic`: headings, which are shown in labels.
   - `paragraphs`: the author's text only. Drop editors' introductions, footnotes, page numbers and indexes, as `thml.py` and `ccel_text.py` do.

   Reuse `parse_ccel_thml` (format `ccel-thml`) or `parse_ccel_text` (format `ccel-text`) if the file is a CCEL export. Otherwise write `ingest/<format>.py`, and add a branch for the new `format` in `ingest/commentary.process`.

3. **Register the source** in `ingest/commentary.COMMENTARY_SOURCES`:

   ```python
   "henry-full": CommentarySource(
       id="henry-full",                 # data/raw/<id>/, <id>.jsonl, references/sources/<id>.md
       work="henry",                    # a key of ALLOWED_COMMENTARIES (asserted at import)
       format="ccel-thml",              # the parser branch in process()
       filename="mhc.doc",              # the file name in imports/ and data/raw/<id>/
       title="Matthew Henry's Commentary on the Whole Bible",
       description="One sentence for the Reference concept.",
       edition="Which edition, translator, year",
       license="Public domain (…)",
       origin="https://… (how to download it by hand)",
       corrections={},                  # wrong references in the source, checked by hand
       abbreviations={},                # book abbreviations this source uses differently
   ),
   ```

   `prepare` (Docker start-up) and `okf references` pick up the new entry automatically. So do the anchor check in the producer profile (`commentary(source)`) and the quotation check.

4. **Give the work its names.** These lists are written out by hand:

   | File | What to add |
   |---|---|
   | `generation/evidence.WORKS` | The label shown in answers' Sources, such as `"Matthew Henry, Commentary"` |
   | `retrieval/search._WORKS` | A folded pattern for the author's name, so "What does Henry say…" filters to the work. Only needed for a new author; one pattern covers all of an author's works |
   | `okf/propose._SHORT` | Optional: a short prefix for footnote ids in proposals. Without an entry, the source id is used (`henry-full-rom3-21`); a prefix must not collide with another source's |
   | `okf/references._example_anchor` | One real anchor, shown in the Reference concept |
   | `generation/prompts.SYSTEM`, `ROUTE_NOTES` | The sentences that name the commentaries the evidence comes from |
   | `okf/propose.DRAFT_SYSTEM` | The sentence that names the commentators to cite |
   | `api/static/index.html` | The subtitle listing the sources |
   | `imports/README.md` | The file name and where to download it |

5. **Import and build.**

   ```bash
   python -m corpusstudy.ingest --import henry-full ~/Downloads/mhc.doc   # copies, locks the checksum, builds
   python -m corpusstudy.retrieval show henry-full:Rom.3.21-Rom.3.26        # read a few excerpts in full
   python -m corpusstudy.indexing                                           # rebuild the index
   ```

   Read the ingest report:
   - `excerpts` and `with_refs` (how many are keyed to a passage);
   - `corrected` (your `corrections` applied);
   - `dropped` (references that don't exist in the KJV).

   For each dropped reference, check the printed text. When the source is wrong, add a `corrections` entry with a comment saying what the text prints; then re-run until the drops are understood. The build also writes `knowledge/references/sources/henry-full.md` and a log entry.

6. **Tests.**
   - A parser unit test with a small inline fixture (`tests/test_ccel_text.py` is the pattern) that covers ids, refs, mentions and the text cleanup.
   - A test on the real file, skipped when it isn't imported (`@pytest.mark.skipif(not PROCESSED.exists(), …)` in `tests/test_commentary.py`). It checks the excerpt count, a few known excerpts and that every ref is valid.
   - Retrieval cases of kind `commentary` in `eval/retrieval.yaml` (and a couple in the held-out set) that name the work. Run `python -m corpusstudy.retrieval eval` and keep the gates in §6.3. More commentary dilutes the ranking, so check that the `quote_*` and `ref_*` categories stay at 100%.
   - `python -m corpusstudy.generation eval` must stay at 100%.
   - `python -m corpusstudy.okf check` must report 0 errors. Existing concepts are unaffected unless you changed an existing source.

7. **Commit together:**
   - the code;
   - `resources/sources.lock.json` (the new checksum);
   - `knowledge/references/sources/<id>.md`, `knowledge/references/sources/index.md` and `knowledge/log.md`;
   - the tests and evaluation cases;
   - the docs: the README source list, the provenance table in `KNOWLEDGE_FORMAT.md`, the Decisions in `BUILD_PLAN.md`, and §3 and §11 here.

   The raw file stays out of git (`data/` and `imports/` are ignored), so CI runs without it: the new tests must skip cleanly there.

8. **Tell the owner** to copy the file into `imports/` and restart the container. Start-up imports it, rebuilds the corpus and re-indexes. Concepts can then cite `/references/sources/<id>.md#<local id>`, and `okf propose` will draw on it.

#### A.1.4 Adding or replacing a Bible translation

The code assumes **exactly one translation per language**:
- `Settings.translations` maps each language to one translation;
- the validator checks references and quotations against the answer language's translation;
- commentary and knowledge are keyed in KJV numbering, with a mapping to the other translation.

Replacing a language's translation stays within that design. Adding a second translation to a language, or adding a new language, breaks it, so plan that as a phase.

Steps (replacement or addition):

1. **Declare it** in `config.py`:
   - add a value to `Translation`;
   - add its language to `TRANSLATION_LANGUAGE`;
   - add its full name to `TRANSLATION_NAMES`;
   - set it in the `Settings.translations` default.

   `PUBLIC_DOMAIN_TRANSLATIONS` is every `Translation`, so declaring one *is* the public-domain claim. Do it only after A.1.1.

2. **Pin and parse it** in `ingest/sources.SOURCES`:
   - a `Source` with a module-level commit constant;
   - one `SourceFile` per file, with a URL at that commit;
   - `format`, `license`, `homepage`, `description`.

   `parse_source` handles `osis` (`osis.py`) and `usfm` (`usfm.py`). For CSV or JSON, `tabular.py` exists but isn't wired in: add a branch. The parser must yield `RawVerse`s in the translation's **own** numbering, with psalm superscriptions as verse 0 where the text has them; drop notes, headings and cross-references.

3. **Ingest and validate.** Run `python -m corpusstudy.ingest -t <TR>`. The first run records checksums in the lock. The report lists:
   - the verse count;
   - superscriptions;
   - repairs (`pipeline.repair_numbering` fixes repeated or missing numbers);
   - validation errors against the canon (`canon.py` chapter counts).

   Fix the parser, not the data, until there are no errors.

4. **Versification.**
   - The pipeline writes `resources/versification/<TR>.json` (verse counts).
   - The mapping to KJV is the hard part: `pipeline.run` aligns only KJV with LSG 1910 (`align_translations` writes `KJV-LSG1910.json`), and `versification.map_passage` reads that file.
   - For a new translation, generalise both: align `<TR>` with the KJV and load `KJV-<TR>.json`.
   - Check the mapping on the known trouble spots: psalm titles, Joel 2–3, Malachi 3–4, Job 38–41 and Mark 9:50.
   - Add cases to `tests/test_versification.py`.

5. **Update the per-translation tables:**

   | File | Entry |
   |---|---|
   | `okf/profile._BIBLE_SOURCES`, `okf/quotes._BIBLES` | `"<source id>": Translation.<TR>` (anchors and quotations in concepts) |
   | `okf/references._BIBLE_TAGS`, `_LANGUAGE` | The tags and language name for its Reference concept |
   | `generation/answer.TAGS` | The short tag after every reference, such as `"LSG"` |
   | `router/route._LOADED` | How people name it; remove it from `_OTHER_ABBREV` and `_OTHER_NAMES` if it is listed there as "not loaded" |
   | `generation/evaluation.py`, `retrieval/evaluation.py` | They pick the translation by language (`KJV if en else LSG1910`); use `settings.translation_for(language)` instead |
   | `okf/propose._source` | Proposals draw on KJV Scripture only; widen this only for a new concept language |
   | `api/static/index.html` | The subtitle |

6. **For a new language** (on top of the above):
   - `config.Language`;
   - `indexing/analysis.py`: stemmer, stopwords, elision rules, `detect_language`;
   - the router's language-specific patterns (pastoral, advice and refusal words);
   - every fixed text: `answer.SAFETY` (with crisis lines for that language's country), `answer._TEXT`, `prompts.CANNOT_VERIFY` and the answer headings;
   - concept names (`title_<lang>`, `aliases_<lang>` in the profile and in `search._concept_names`);
   - the chat page strings;
   - evaluation cases in that language for every category.

   The owner reviews the crisis lines and fixed texts.

7. **Rebuild and test:**
   - Run `python -m corpusstudy.indexing`. The inputs changed, so Docker rebuilds the index on its own.
   - Add retrieval cases `quote_<lang>` and `ref_<lang>`, which are gated at 100% recall@5.
   - Add lookup cases in `eval/answers.yaml`, including references numbered differently from the KJV.
   - Run the full suite, both evaluations and `okf check`.

   CI downloads the new text on every run, and its drift check fails until you commit the generated tables and Reference concept.

8. **Commit together:** the code, the lock, `resources/versification/*`, `knowledge/references/sources/<id>.md`, the tests, the evaluation cases and the docs.

#### A.1.5 Adding a cross-reference dataset

Cross-references are pairs of passages (KJV numbering) stored in the index's `xrefs` table, each with the excerpt or concept that links them (`via`). Today they come from commentary `mentions` and from concept key passages (`store._links`). They cover about 26% of verses.

A dataset such as the Treasury of Scripture Knowledge (its key, `tsk`, is already reserved in `ALLOWED_COMMENTARIES`; it was skipped by decision) would plug in like this:
- pin or import it like a source (A.1.3 steps 3 and 5);
- parse it into `(from, to)` OSIS pairs in KJV numbering, dropping pairs that fail `versification(KJV).check_passage`;
- feed the pairs to `write_index`, next to the chunk-derived links, with `via` set to the source id (for example `tsk`), so the crossref route can say where each link comes from;
- add a `Reference` concept for it, and bump `store.SCHEMA_VERSION` if the table changes;
- measure: the share of verses with at least one link (`read_info(index_path()).meta["xrefs"]` gives the number of links) and the cross-reference cases in `eval/answers.yaml`.

#### A.1.6 Replacing or removing a source

Concepts cite sources by anchor, so a source can't simply disappear.

1. Find what cites it: `grep -rl "references/sources/<id>.md" knowledge/`.
2. For each concept, either re-source the claims (`okf touch` sends it back for review) or deprecate it (`okf deprecate`).
3. Remove the source's registrations (A.1.3 steps 1–4, or A.1.4 steps 1–5), its lock entries and its `Reference` concept.
4. Run `okf check` (0 errors), both evaluations and the full suite.
5. Re-importing a *new version* of the same source is a replacement too. Changed excerpt ids or wording show up as `okf check` errors on the concepts that quote them.

### A.2 How to add new concepts

A *concept* is one Markdown file in `knowledge/`: a book, theme, person, doctrine, event or parable. The bot uses it in answers once the owner has reviewed it. The format and the producer profile are defined in [`KNOWLEDGE_FORMAT.md`](KNOWLEDGE_FORMAT.md). This section is the procedure, from choosing a subject to getting the concept into answers.

Two rules hold throughout:

- **Only the owner approves.** Never run `okf verify` without the owner's explicit approval of that concept, and never stamp it with your own id to unblock something. An unreviewed concept is simply unused (`CORPUS_USE_DRAFTS=false`).
- **Every claim is sourced.** Every statement is footnoted to a public-domain passage or excerpt, and every quotation is word for word. `okf check` enforces both; don't weaken the check to make a concept pass.

#### A.2.1 Choose the subject

1. **Start from the gaps.** `python -m corpusstudy.okf maintain` lists the questions no concept covers, the words that recur among them, and the books without a Book concept. With `CORPUS_GAP_LOG` on, it also lists questions from real use.
2. **Check that no concept already covers it.** Look in `knowledge/*/index.md` and run `python -m corpusstudy.okf graph`. Run `python -m corpusstudy.generation route "<a question about it>"` and `python -m corpusstudy.retrieval search "<question>" --drafts`. Extending a concept (A.2.7) is often better than adding a neighbour that competes with it for the same questions.
3. **Keep to one subject per concept**: *Prayer*, not *Prayer and fasting*. Answers cite concepts section by section, and a sharp subject ranks better.
4. **Pick the type, the folder and the slug.** The slug is lowercase words joined by hyphens (`kingdom-of-god`). It becomes the concept's id (`themes/kingdom-of-god`) and is permanent: renaming means deprecating (A.2.7).

| Type | Folder | Sections, in this order |
|---|---|---|
| `Bible Book` | `books/` | Overview, Setting, Outline, Themes, Key passages |
| `Theme` | `themes/` | Overview, Development through Scripture, Reformed view, Key passages |
| `Person` | `people/` | Summary, Life, Significance, Key passages |
| `Doctrine` | `doctrines/` | Definition, Reformed view, Where other traditions differ, Key passages |
| `Event` | `events/` | Summary, Context, Significance, Key passages |
| `Parable` | `parables/` | Summary, Setting, Meaning, Interpretive notes, Key passages |

A subject that fits none of these needs a new type first (§6.7).

#### A.2.2 Path 1: let Claude draft it (`okf propose`)

This is the quickest path. It needs the API key and the built corpus and index.

```bash
git switch -c knowledge/prayer
python -m corpusstudy.okf propose Theme prayer "Prayer" \
    --question "How should I pray?" --question "How can I be sure God hears my prayers?" \
    --passage "Matt 6:5-15" --passage "Luke 11:1-13" --passage "Phil 4:6-7"
```

- **`--question`**: the gap questions the concept should answer. They steer both the evidence and the writing.
- **`--passage`**: the passages you know matter. Search alone can miss them: for prayer, keyword search does not find the Lord's Prayer from the word "Prayer". Their KJV text and the commentary on them come first in the evidence.

The command prints the file it wrote, its sources, any warnings, and the model calls and estimated cost. It writes nothing if the draft fails the checks twice; widen the evidence with `--passage` and run it again.

**Then read the draft as a reviewer would:**
- Is every claim supported by the footnoted source?
- Is the Reformed reading presented first and fairly?
- Are the other views labelled "not drawn from the corpus"?
- Are the aliases specific (A.2.3)?

Edit by hand where needed, then record your edit as the author of the current content: `python -m corpusstudy.okf touch themes/prayer --by human:<your-id>`. The concept stays a draft. Continue at A.2.4.

#### A.2.3 Path 2: write it by hand

```bash
git switch -c knowledge/prayer
python -m corpusstudy.okf new Theme prayer "Prayer" --by human:<your-id>
```

`okf new` writes `knowledge/themes/prayer.md`: the frontmatter fields with empty values, `status: draft`, `generated` with your actor and the time, the type's section headings, plus a log entry and the refreshed indexes. Then fill it in.

**Frontmatter:**

| Field | What to write |
|---|---|
| `title` | The name readers use ("Prayer", "The Parable of the Sower") |
| `description` | One sentence; shown in indexes and to the model |
| `tags` | A few lowercase words |
| `title_fr` | The French title (required: answers are bilingual) |
| `aliases`, `aliases_fr` | Other names a question would use. The router matches titles and aliases as whole words, so: prefer phrases ("the Lord's Prayer", "life of prayer"); no bare common words ("ask", "holy", "law"); no names shared by several people ("Saul", "Simon"); nothing that is another concept's title or alias |
| `key_passages` | 2 to 6 passages in KJV numbering, written `"Matt 6:5-15"`. Theme, Doctrine, Event and Parable concepts also turn them into cross-references between those passages |
| `sources` | One entry per cited source: `id` (the footnote label), `resource` (the anchor, below) and `title` |
| `tradition` | `reformed` |
| `other_views` | `true` when the concept summarises other traditions |
| `stale_after` | Optional: a date after which the concept stops being used until it is re-reviewed (for content that dates) |

**Source anchors:**

| Source | `resource` | Example | Find it with |
|---|---|---|---|
| KJV | `/references/sources/kjv.md#<OSIS>` in KJV numbering | `#Matt.6.5-Matt.6.15` | `python -m corpusstudy.retrieval show KJV:Matt.6.5-Matt.6.15` |
| Louis Segond 1910 | `/references/sources/lsg1910.md#<OSIS>` in **LSG** numbering | `#Ps.51.3` | `... show LSG1910:Ps.51.3` |
| Matthew Henry | `/references/sources/henry-concise.md#<excerpt id>` | `#Matt.6.9-Matt.6.15` | `... search "Henry lord's prayer"`, then `... show henry-concise:Matt.6.9-Matt.6.15` |
| Calvin | `/references/sources/calvin-lcc.md#<excerpt id>` | `#x-p112` | `... search "Calvin prayer"`, then `... show calvin-lcc:x-p112` |

Always copy excerpt ids from `search` or `show` output; never guess one. A search result id may end in `#2` (a part of a long excerpt); the anchor is the excerpt id without it.

**Body:**
- Use the type's sections as `#` headings, in order; search records what each section holds from its heading.
- End each claim, or group of claims, with its footnote markers: `…the disciples asked to be taught.[^kjv-luke11-1]`. Define each footnote at the end of the file: `[^kjv-luke11-1]: Luke 11:1-4 (KJV)`. Each `sources` id is cited at least once, and every marker matches a `sources` id.
- Quotations in "double quotes" must appear word for word in a source whose marker follows them in the same paragraph. Use an ellipsis (`...`) to join fragments, and quote the KJV wording for Scripture.
- Present the Reformed reading first, citing Calvin or Henry where they speak to it.
- Other traditions go in a labelled paragraph ("These are summaries for orientation, not drawn from the corpus:") with no quotations and no footnotes. Keep it short and fair.
- Link related concepts with bundle-absolute Markdown links: `[grace](/themes/grace.md)`. No `[[wikilinks]]`. Links feed the graph, which retrieval uses for one-hop expansion.
- The Key passages section lists the passages as plain references and may link related concepts.

Copy an existing concept of the same type for the tone and density; `parables/sower.md` and `doctrines/justification.md` are good models.

#### A.2.4 Check it

```bash
python -m corpusstudy.okf check        # repeat until 0 errors; read the warnings too
```

| Message | Fix |
|---|---|
| ``missing `title_fr` `` / ``missing `key_passages` `` / ``missing `description` `` | Fill in the field |
| `key passage '…': …` | Not a valid reference, or the verse doesn't exist in the KJV |
| ``footnote [^x] has no matching `sources` id`` / `is not defined in the body` | Add the `sources` entry or the `[^x]:` definition; check spelling |
| ``source `x`: bad passage anchor`` / `no excerpt '…' in …` | Wrong anchor: copy it again from `retrieval show` (A.2.3) |
| `quotation "…" not found in …` | The quote isn't word for word, or its footnote names the wrong source. Fix the words or the marker, or paraphrase without quotation marks |
| `a 'Theme' concept belongs in themes/` | The file is in the wrong folder for its `type` |
| warning: ``source `x` is never cited by a footnote`` | Cite it or remove it |
| warning: `quotation "…" has no footnote` | Add the marker after it in the same paragraph |
| warning: `link to missing concept` | Fix the link, or leave it if that concept is planned |
| warning: ``source `x` text not available; quotes unchecked`` | A commentary isn't imported here; import it so quotations are checked before review |

#### A.2.5 Check how it behaves

Drafts aren't used in answers, so test with drafts turned on:

```bash
python -m corpusstudy.indexing                                      # the knowledge changed
python -m corpusstudy.generation route "How should I pray?"          # does it name the concept?
python -m corpusstudy.retrieval search "How should I pray?" --drafts # which sections come back, via which lists
python -m corpusstudy.generation ask "How should I pray?" --drafts   # a live answer (API key)
python -m corpusstudy.generation eval                                # all offline targets still 100%
python -m corpusstudy.retrieval eval                                 # gates in §6.3 still met
python -m corpusstudy.okf maintain                                   # the gap should now be covered
```

- **The concept doesn't come up:** add an alias for the wording people use, or make the opening section state the subject plainly.
- **Other questions now route to it** (the offline evaluation fails on `concept` or `routing`): an alias is too broad. Remove it; never weaken the evaluation case.
- **Add evaluation cases:** at least one question in `eval/answers.yaml` (`route: knowledge`, `concept: themes/prayer`, a `gist` the owner can check). The held-out set stays untouched until a later confirmation run.

#### A.2.6 Submit it for review

1. Commit on the branch:
   - the concept;
   - the regenerated `index.md` files and `log.md` (the commands update them);
   - the evaluation cases.
2. Open a pull request. List each new concept with what the reviewer should look at: contested points, anything summarised from general knowledge, and sources that cover a claim only indirectly.
3. CI runs the tests and `okf check`; the index marks the concept "(draft)".
4. The owner reviews in the pull request and either asks for changes or approves the concept by name ("approve themes/prayer").
5. **Only on that approval**, run `python -m corpusstudy.okf verify themes/prayer`. It stamps the configured reviewer (`CORPUS_REVIEWER`), sets `status: stable` and logs a Review entry. It refuses a concept that fails the profile.
6. Commit the stamped file and merge. On the owner's machine, `git pull` and `docker compose restart`: the index is stale, so it is rebuilt on start, and the concept is used in answers from then on.

#### A.2.7 Change, replace or retire a concept

- **Edit a reviewed concept:** change the file, then run `okf touch <id> --by human:<your-id>`. The concept goes back to draft and leaves answers until it is reviewed again; its earlier reviews stay as history. A stable concept edited without `touch` fails `okf check`.
- **Merge or retire:** `okf deprecate <id> --reason "merged into themes/prayer"`. The file stays for history and links; the maintenance report lists links that still point to it.
- **Rename:** ids are paths, so create the new concept (A.2.2 or A.2.3), deprecate the old one with a reason naming the new one, and update the links pointing to the old one.
- **A source changed under a concept:** the maintenance report lists reviewed concepts whose source was regenerated, and `okf check` flags any quotation that no longer matches. Fix the concept, touch it and send it for review.

#### A.2.8 Checklist

- [ ] The subject comes from a gap or the owner's request, and no existing concept covers it
- [ ] The type, folder and slug are right, and the sections are in order
- [ ] `title_fr`, `key_passages`, specific aliases and `description` are filled in
- [ ] Every claim is footnoted to a real anchor, and every quotation is word for word
- [ ] The Reformed reading comes first; other views are labelled and kept short
- [ ] `okf check` reports 0 errors, and the warnings are understood
- [ ] Routing, retrieval and both evaluations pass; an evaluation case is added
- [ ] It is submitted as a draft in a pull request; `okf verify` runs only on the owner's approval

### A.3 How to add new routes

#### A.3.1 What a route is

A **route** is the kind of question the bot decides it is facing, chosen *before* anything is searched or generated. Every question gets exactly one. The route decides four things:

1. **Whether the model is called at all.** A lookup ("John 3:16") is read straight from the Bible, and a refused request gets a fixed text: neither costs anything or can be wrong. Other routes call the model once, plus at most one repair.
2. **What evidence is gathered, and how.**
   - A comparison searches each book separately, so every Gospel is represented.
   - A cross-reference question follows the cross-reference index.
   - An open question uses the general hybrid search.
3. **What comes first and what the model is told.**
   - Fixed texts written by code come first: for example, crisis contacts on the pastoral route.
   - Route-specific instructions go to the model: for example, "organise the answer book by book" for comparisons.
4. **How the answer is labelled, logged and evaluated.** The route is shown as a tag on the chat page, written in the log line, and checked case by case in the evaluation sets.

Routing is the job of `router/route.py`. It is **deterministic**: regular expressions over the question, plus the concept names known to search. Rules are tried in a fixed order and **the first match wins**, so every routing decision can be tested offline and explained (`Routing.reason`).

| Order | Route | Chosen when the question… | Example | Model call |
|---|---|---|---|---|
| 1 | `refuse` | asks to demean, harass or incite against people | "Write insults about Catholics" | no |
| 2 | `pastoral` | contains words of self-harm, abuse or despair | "I want to die" | yes, after the fixed safety message |
| 3 | `lookup` | only asks for passage text | "Psalm 23", "Jean 3.16 en Segond" | no |
| 4 | `crossref` | cites a passage and asks for related ones | "Passages parallel to Isaiah 53" | yes |
| 5 | `compare` | compares books or accounts | "How do Matthew and Luke differ on the birth of Jesus?" | yes |
| 6 | `interpretive` | asks about interpretations or traditions | "Main interpretations of Romans 9" | yes |
| 7 | `hybrid` | names a knowledge concept and cites a passage | "Paul on love in 1 Corinthians 13" | yes |
| 8 | `knowledge` | names a knowledge concept | "What is justification?" | yes |
| 9 | `rag` | anything else (retrieval-augmented generation) | "How should I pray?" | yes |

The result is a `Routing` object:
- `route`, `language` and `reason`;
- the parameters the route needs: `refs`, `concepts`, `books`, `concern`, `invalid_refs`;
- the **modifiers**, which apply on top of any route and change notes, not handling:
  - `advice`: medical, legal or financial; adds a fixed note first and an instruction to keep to biblical context;
  - `translation` / `unavailable_translation`: a translation asked for;
- `secondary`: knowledge or hybrid, recorded when another route won but the question also names a concept.

With `CORPUS_LLM_ROUTER=true`, an open (`rag`) question gets one cheap model call (`LLM.classify`) that may re-route it to `compare`, `interpretive` or `crossref` (`Answerer._refine`). It never overrides a rule that matched, and it cannot route to `refuse`, `pastoral` or `lookup`.

#### A.3.2 Do you need a new route?

A route is the right tool only when a kind of question needs **different handling**. Choose the lightest change that works:

| The kind of question needs… | Change | Where |
|---|---|---|
| better passages or ranking, with the same handling | tune retrieval | §6.3 |
| the same handling, plus a fixed note or a caveat | a **modifier** (like `advice`) | `route.py`, `answer._notes` and `_model_notes` |
| a different answer shape from the same evidence | an instruction for an existing route, or a new route with only a `ROUTE_NOTES` entry | `prompts.ROUTE_NOTES` |
| **different evidence** (gathered another way), **no model call**, or a **fixed response** | a **new route** | this section |

Each route adds patterns that can capture questions that used to route elsewhere, so a route that doesn't change handling costs more than it gives.

#### A.3.3 Design it first

Write these down (in the pull request) before coding:

1. **Name:** one lowercase word, the `Route` value. It appears in the API, the logs, the evaluation sets and the page.
2. **Trigger:**
   - Which words, in **English and French**, mark the question? Patterns run on folded text (`_plain`: lower case, accents removed, curly apostrophes straightened), so write `evangiles`, not `Évangiles`.
   - List questions that **must** match and near misses that **must not**.
3. **Position in the order:**
   - `refuse` and `pastoral` always stay first: no route may come before safety.
   - `lookup` stays before anything that needs the model.
   - Put the new route just before the most general route it should take questions from, and say which existing routes it will take questions from.
4. **Parameters:** what the route extracts (books, a passage, a topic) and where it keeps them. Reuse `refs`, `books` and `concepts`; add a `Routing` field only if none fits.
5. **Handling:**
   - the evidence (which `Search` methods, with which filters);
   - the fixed notes, in both languages;
   - the model instruction;
   - or no model call, with the response built in code.
6. **What the evaluation can check offline:** route, parameters and evidence properties, such as "every compared book is represented in the evidence".

#### A.3.4 Implement it

| Step | File | What to do |
|---|---|---|
| 1 | `schemas.py` | Add the value to `Route`, with a comment and an example question |
| 2 | `router/route.py` | Add the patterns (compiled at module level, EN and FR) and a helper if the route extracts parameters (as `compared_books` does). Add the branch in `route()` at the chosen position, returning `Routing(Route.X, language, refs, concepts, secondary=secondary, **base, reason="…")` so concepts and modifiers carry over. Update the module docstring's list |
| 3 | `generation/answer.py` | **Evidence:** a branch in `_gather` (and a helper method). **Model instruction:** a branch in `_model_notes`. **Fixed notes:** `_notes`, with the texts in `_TEXT` for both languages. **No model call:** an early branch in `_answer`, as `REFUSE` and `LOOKUP` have, returning an `Answer` with the right `Outcome` |
| 4 | `generation/prompts.py` | The `ROUTE_NOTES` entry: short, imperative, and never weakening the citation rules in `SYSTEM` |
| 5 | `generation/prompts.py`, `answer._refine` | Optional: let the model-assisted router pick it. Add it to `CLASSIFY`, the `CLASSIFY_SCHEMA` enum and `_refine`, which must check the parameters the route needs, as it does for compare (at least two books) |
| 6 | `okf/maintenance.py` | Add it to `TOPICAL` if knowledge concepts should cover its questions (they then count in coverage, gaps and the gap log) |
| 7 | `api/static/index.html` | Add a label in `LABELS.route`, and a `.tag.<route>` style if it needs a colour |
| 8 | `generation/evaluation.py` | In `evaluate_offline`, a branch for the offline-checkable properties (§A.3.3, item 6). Add a gated check to `OFFLINE_TARGETS` and a `summarise` entry |
| 9 | `tests/test_generation.py` | Add questions to `test_routes` (matches and near misses, EN and FR). Add an answer-pipeline test with `FakeSearch` and `FakeClient` that checks the evidence gathered, the notes, and that the model call happens or not |
| 10 | `eval/answers.yaml` | Add cases with `route:`, a new `category`, the parameters (such as `books:`) and a `gist`, and a few near misses routed elsewhere. Write held-out cases in `eval/answers_holdout.yaml` **separately**, and run them once at the end to confirm |
| 11 | Docs | The README route diagram, §6.4 and the table in §A.3.1 |

#### A.3.5 Validate

```bash
python -m corpusstudy.generation route "<each new example>"     # route, reason, parameters
python -m corpusstudy.generation eval                          # every offline target at 100%
python -m corpusstudy.generation eval --cases eval/answers_holdout.yaml
pytest tests/test_generation.py && pytest
python -m corpusstudy.generation eval --live --judge           # with the key: answer quality and cost
```

Read the offline evaluation's notes for cases whose `route` check now fails: they show which questions the new patterns took from other routes. Narrow the pattern; never edit an existing case to fit it. If the live run shows the route's answers being repaired or refused more often than other routes, its evidence or its instruction is wrong.

#### A.3.6 Worked example: the `compare` route (Phase 7b)

1. **Need:** for "Compare the resurrection accounts in the four Gospels", general search ranks passages by how well they match, so one Gospel can fill the evidence and the others go missing. That is an evidence problem, so it needed a new route.
2. **Trigger** (`route.py`):
   - `_GROUP_BOOKS` maps "the synoptics" and "the four Gospels" (and the French forms) to their books. The synoptics come first, because "les évangiles synoptiques" also contains "les évangiles".
   - Otherwise, `compared_books` needs two or more named books **and** a comparing word (`_COMPARE`: compare, differ, versus, accounts, récits…).
3. **Position:** after `crossref`, before `interpretive`, `hybrid` and `knowledge`, so "How do Matthew and Luke differ…" compares even though "Matthew" names a book concept. That concept stays in `concepts` and `secondary`.
4. **Parameters:** `Routing.books`, a list of OSIS codes.
5. **Evidence** (`answer._gather`): one search per book (`books=[book]`, `COMPARE_PER_BOOK = 3`, Scripture and commentary), interleaved, plus three knowledge results.
6. **Instruction:** `ROUTE_NOTES["compare"]`: book by book, then agreements and differences, without harmonising away a real difference.
7. **Model-assisted:** `CLASSIFY` can return `compare` with books, accepted only with at least two valid books.
8. **Page:** the label "Comparison".
9. **Evaluation:**
   - offline checks: `books` (the books extracted) and `each_book` (every book is in the evidence);
   - category `comparison` in both sets;
   - near misses such as "What does Matthew say about prayer?" stay `knowledge` or `rag`.

#### A.3.7 Sketch: a `summary` route

A hypothetical route for "Summarise John 17" or "Résume Jean 17":

- **Trigger:** `summari[sz]e|outline|overview|resume|resumer|plan de` together with exactly one chapter reference.
- **Position:** after `lookup` ("John 17" alone is still a lookup), before `crossref`.
- **Evidence:**
  - the whole chapter: `search.passage_chunks(refs, language)`;
  - Henry's chapter outline: `search.excerpt_chunks(f"henry-concise:{ref.book}.{ref.start_chapter}.outline")`;
  - a short general search for commentary.
- **Instruction:** "Summarise the chapter section by section, citing verse ranges; quote sparingly."
- **Checks:** the route, and the evidence covering every verse of the chapter.

```python
# router/route.py
_SUMMARY = re.compile(r"\bsummar\w*|\boutline\b|\boverview\b|\bresum\w*|\bplan de\b")
...
    if len(refs) == 1 and refs[0].start_verse is None and _SUMMARY.search(plain):
        return Routing(Route.SUMMARY, language, refs, concepts, secondary=secondary, **base,
                       reason="asks for a chapter summary")  # fmt: skip

# generation/answer.py, in _gather
        if routing.route == Route.SUMMARY:
            ref = routing.refs[0]
            out = self.search.passage_chunks(routing.refs, language)
            out += self.search.excerpt_chunks(f"henry-concise:{ref.book}.{ref.start_chapter}.outline")
            out += self.search.search(question, language, k=4, kinds=("commentary",))
            return pack(out)
```

`refs` holds the passage in the question's own numbering. Commentary is keyed in KJV numbering, so map a French reference first, as `_cross_references` does with `map_passage`.

#### A.3.8 Pitfalls

- **Order is behaviour.** Moving a branch, or broadening a pattern, changes the route of questions you didn't think about. The two evaluation sets are the safety net; run both every time.
- **Safety stays first.** Never put a route before `refuse` or `pastoral`. A pastoral question about a passage still gets the safety message first.
- **Both languages.** A route reachable only in English gives French users a worse answer. Every pattern, fixed text and evaluation category needs a French version.
- **Patterns run on folded text.** Accents are removed and the text is lower-cased before most patterns run. Book-name matching (`named_books`) uses the original text, where capitals matter.
- **Validation still applies.** A route can change the evidence and the instruction, never the citation validator: every answer that calls the model goes through `validate()` and the repair-or-refuse step.
- **No questions in logs.** The log line records the route and `reason`, never the question; keep `reason` free of question text.

#### A.3.9 Checklist

- [ ] The handling really differs (A.3.2), and the design is written down (A.3.3)
- [ ] The `Route` value, patterns (EN and FR), branch position and `reason` are in place
- [ ] Evidence, model instruction, fixed notes (both languages) or no-model handling are done
- [ ] Optional: model-assisted routing, `TOPICAL`, page label
- [ ] Unit tests (matches and near misses) and an answer-pipeline test are added
- [ ] Tuned evaluation cases with a new category are added; held-out cases are written separately and run once
- [ ] Both offline evaluations are at 100%, the full suite passes, and a live run was checked when the key is available
- [ ] The README diagram, §6.4 and §A.3.1 are updated

### A.4 How to tune retrieval

#### A.4.1 What retrieval does, and what "good" means

Retrieval chooses the **evidence** an answer may use. On the general routes the model sees 12 items (`Answerer.k`), a mix of Scripture windows, commentary excerpts and knowledge sections; it may cite nothing else. When retrieval misses the passage that answers the question, the best model can only say it cannot verify the answer. When it fills the 12 slots with near-duplicates, the answer is thin. Tuning means getting the right items into those slots, high up, in a useful mix.

It is measured on two evaluation sets:

| File | Cases | Role |
|---|---|---|
| `eval/retrieval.yaml` | 35 | **Tuned set:** look at it, change things, measure again |
| `eval/retrieval_holdout.yaml` | 12 | **Held-out set:** run once to confirm a change; never tune on it |

A case is a question with what must come back:

```yaml
- {q: "the LORD is my shepherd", lang: en, kind: bible, expect: [Ps.23.1], category: quote_en}
- {q: "Matthew Henry on the prodigal son", lang: en, kind: commentary, expect: [Luke.15.11-Luke.15.32], category: commentary}
```

A case is a **hit** when a result in the top *k* is of that `kind` and overlaps an `expect` passage. For a Bible case, the result must also be in the answer language's translation and numbering. A commentary case may instead name a `topic` (and optionally a `work`) that the excerpt's headings must contain; the current cases all use passages.

The metrics:
- **recall@k**: the share of cases with a hit in the top *k*;
- **MRR**: the mean of 1/rank of the first hit, 0 when there is none. It rewards putting the hit *first*.

Categories:
- `quote_en`, `quote_fr`: verse wording;
- `ref_en`, `ref_fr`: an explicit reference;
- `topic_en`, `topic_fr`: doctrinal words;
- `commentary`: Calvin or Henry on a passage or topic;
- `paraphrase`: modern wording that isn't in the KJV. It is **tracked, not gated** (`evaluation.UNGATED`) until vector search exists.

Current figures:

| | Gated recall@5 | Gated recall@10 | Gated MRR | Paraphrase recall@10 |
|---|---|---|---|---|
| Tuned set | 1.00 | 1.00 | 0.89 | 0.00 (3 cases) |
| Held-out set | 1.00 | 1.00 | 0.85 | 1.00 (1 case) |

The gates in `tests/test_retrieval.py` fail the build when:
- on the tuned set: recall@10 is below 0.95, recall@5 below 0.9, MRR below 0.75, or any `quote_*` or `ref_*` category below 100% recall@5;
- on the held-out set: recall@10 is below 0.9.

The retrieval sets measure Bible and commentary retrieval. **Knowledge** retrieval is measured elsewhere:
- concept naming: the `concept` check in the answer evaluation (`python -m corpusstudy.generation eval`);
- coverage: `python -m corpusstudy.okf maintain`.

#### A.4.2 The knobs, least invasive first

| Knob | Where | Effect | After changing it |
|---|---|---|---|
| List weights | `search.WEIGHTS` | How much each ranked list counts in the fusion (§6.3) | Evaluate |
| Fusion constant | `search.RRF_K` (60) | Lower: the top of each list dominates. Higher: agreement between lists dominates | Evaluate |
| List depth | `Search.search(per_list=30)` | How deep each list is read before fusion | Evaluate; watch latency |
| Results per answer | `Answerer.k` (12), `COMPARE_PER_BOOK` (3) | How much evidence the model sees: more recall, more cost and noise | Answer evaluation; live run for cost |
| Prefix matching | `search._PREFIX_MIN` (4) | Terms of this length or more match as prefixes ("justif*") | Evaluate |
| Kind interleaving | `Search._fuse` | Scripture, commentary and knowledge alternate, strongest kind first; asking for commentary puts it first | Evaluate; answer evaluation |
| Graph expansion | `Search.search` (top 2 seeds per list, 1 hop) | How many neighbouring concepts join | Answer evaluation |
| Commentator names | `search._WORKS` | "Henry" or "Calvin" in a question filters commentary | Evaluate the commentary cases |
| Concept names | concept `aliases` (A.2.3) | Which questions name a concept | Answer evaluation; `okf check` |
| Text analysis | `indexing/analysis.py`: `_ARCHAIC`, `_ETH_KEEP`, `STOPWORDS`, elision | Which terms match: "loveth" → "loves", stop words dropped | **Rebuild the index, bump `SCHEMA_VERSION`** |
| Chunking | `chunking.bible_chunks(size=3, stride=2, max_size=10)`, commentary splitting | What a result *is*: 3-verse windows, sentence-aligned, at most 10 verses | **Rebuild the index, bump `SCHEMA_VERSION`** |
| A new list | `Search.search`, `WEIGHTS` | A new way of finding candidates, such as vectors (A.4.6) | Rebuild; all of the above |

Prefer the top of the table. Analysis and chunking changes affect every query and need a rebuild; weights are cheap to try and to revert.

#### A.4.3 The procedure

1. **Turn the problem into a case.** Write the failing question as a case in `eval/retrieval.yaml`, in the right category, with the passages that *should* come back. Check each expected passage against the corpus (`python -m corpusstudy.retrieval show KJV:Ps.23.1-Ps.23.6`); an expectation that is wrong will mislead every later run.
2. **Record a baseline.**
   ```bash
   python -m corpusstudy.retrieval eval > /tmp/before.txt
   ```
   The output gives recall@1, @5, @10 and MRR per category, the gated and overall totals, and every miss.
3. **Diagnose before changing anything.**
   ```bash
   python -m corpusstudy.retrieval search "the question" -k 12 [--lang fr] [--drafts]
   ```
   The first line shows the detected language, the references found and the **terms** after analysis. Each result shows the **lists that found it** (`phrase:bible`, `reference:commentary`, `keyword:knowledge`…). Use A.4.4 to go from symptom to knob.
4. **Change one knob.** Try weights first without editing code (A.4.5).
5. **Measure on the tuned set.**
   ```bash
   python -m corpusstudy.retrieval eval > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
   ```
   Read the diff case by case, not just the totals. With 35 cases, one case moves a category by up to a third, and a gain in `topic_en` paid for by a loss in `quote_fr` is a regression.
6. **Confirm on the held-out set, once.**
   ```bash
   python -m corpusstudy.retrieval eval --cases eval/retrieval_holdout.yaml
   ```
   If it fails, go back to diagnosing; don't adjust to the held-out set. When a held-out failure shows a general problem, add a *new* tuned case for it.
7. **Check what depends on retrieval.**
   ```bash
   pytest tests/test_retrieval.py && pytest      # the gates and everything else
   python -m corpusstudy.generation eval         # routing, concepts, comparisons: 100%
   python -m corpusstudy.okf maintain            # concept coverage should not drop
   python -m corpusstudy.generation eval --live  # with the key: citation accuracy and cost
   ```
8. **Record it.** Put the before and after numbers in the commit message, and update the figures in A.4.1 and §6.3 when the baseline moves.

#### A.4.4 From symptom to knob

| Symptom (from `retrieval search`) | Likely cause | What to try |
|---|---|---|
| A verse quoted word for word isn't in the top 5 | A word was stemmed or dropped unexpectedly (check `terms=`), or the KJV form differs ("hath", "-eth") | `analysis.py`: an archaism mapping or a stop word; never a per-question rule |
| Modern wording ("love is patient") finds nothing | Keyword search can't bridge synonyms: the `paraphrase` category | Vectors (A.4.6). Don't add synonym lists for individual verses |
| The right verse is found but ranked below loosely related ones | `keyword` (any word) outweighs `all` or `phrase` | Lower `keyword`, or raise `all` or `phrase`, and re-check `topic_*` |
| Commentary on the cited passage ranks low | The `reference` weight, or the excerpt is keyed to a wider range (it ranks narrow ranges first) | `reference` weight; check the excerpt's `refs` with `retrieval show` |
| The evidence is all one kind | The interleaving or a dominant list | The `_fuse` interleaving; per-list weights |
| One book fills a comparison | `COMPARE_PER_BOOK` too high, or the per-book searches return too little | `COMPARE_PER_BOOK`; check the `books=` filter |
| A concept isn't retrieved for questions that name it | Its title or aliases don't match the wording | Aliases (A.2.3), not weights |
| Unrelated concepts appear | `graph` neighbours, or a too-generic alias | Lower `graph`; narrow the alias |
| A French question gets no commentary | By design: the commentaries are English and are reached through references and matched verses (`hits`) | Check that the French reference maps to KJV (`versification.map_passage`) |
| Results cut a sentence or carry too many verses | Chunking | `bible_chunks` parameters: rebuild, bump the schema. Single-verse windows were tried and rank worse |
| A Calvin or Henry question returns the other commentator | `_WORKS` doesn't recognise the name form | Add the form to `_WORKS` (folded text) |

#### A.4.5 Experimenting without editing code

The weights, `RRF_K` and `_PREFIX_MIN` are module globals read on every query, so a short script can sweep them against the evaluation set:

```python
# python -I sweep.py   (from the repository root, with the index built)
from corpusstudy.retrieval import search as S
from corpusstudy.retrieval.evaluation import evaluate, load_cases, summarise

engine = S.Search()
cases = load_cases()
for w in (0.5, 0.7, 0.9):
    S.WEIGHTS["keyword"] = w
    g = summarise(evaluate(engine, cases, k=10))["gated"]
    print(f"keyword={w}: R@1 {g['recall@1']:.2f}  R@5 {g['recall@5']:.2f}  MRR {g['mrr']:.2f}")
```

Here the current 0.7 is as good as 0.5, and 0.9 loses a quote case at rank 5. Pick the setting from the tuned set, write it into `WEIGHTS`, then run A.4.3 steps 5 to 8. Sweeping several knobs at once against 35 cases fits the noise: change one or two.

To compare **analysis or chunking** changes, build a second index next to the first and point the commands at it:

```bash
CORPUS_INDEX_DIR=/tmp/idx-b python -m corpusstudy.indexing           # built with your change
CORPUS_INDEX_DIR=/tmp/idx-b python -m corpusstudy.retrieval eval     # versus the default index
```

#### A.4.6 Adding a list (vector search)

Vector (embedding) search is the planned remedy for paraphrases. `CORPUS_EMBEDDINGS` accepts `voyage` and `local` but nothing reads it yet. A new list plugs into the fusion like the others:

1. **Compute it at indexing time.** Embed every chunk with the configured backend and store the vectors, in the index or a sidecar file named after it. Bump `SCHEMA_VERSION`, and add the embedding model's name to the index `meta` so a model change makes the index stale.
2. **Query it.** In `Search.search`, for each kind, add a list such as `lists["vector:bible"]`: the top `per_list` chunk ids by similarity, filtered with `_allowed` and the `books` filter like the FTS lists.
3. **Weight it.** Add `"vector"` to `WEIGHTS`, starting near `all` (1.0), and to the retriever mapping in `_fuse`.
4. **Tune and gate.** Tune on the `paraphrase` category with A.4.3, and check that `quote_*` and `ref_*` stay at 100%. Once paraphrase meets the gate on both sets, remove it from `UNGATED`.
5. **Keep it optional.** With `CORPUS_EMBEDDINGS=none`, behaviour and tests must be exactly as before; CI runs in that mode.
6. **Tell the owner about privacy.** With an API backend, indexing sends chunk text (public-domain sources and knowledge) to that provider, and every query sends the user's question too. Get the owner's agreement before turning it on; a local model avoids both.

#### A.4.7 Pitfalls

- **Never tune on the held-out set,** and never edit an existing case to make it pass. Fix the expectation only when it is demonstrably wrong against the corpus, and say so in the commit.
- **One change at a time.** Two knobs moved together can cancel or hide each other.
- **Small sets are noisy.** Read the individual cases that moved; a 0.03 change in MRR may be one case going from rank 1 to rank 2.
- **Ties are deterministic.** Equal scores are broken by chunk id, so a run is reproducible, but an "improvement" can be a tie broken differently.
- **CI is Bible-only.** Commentary cases are dropped there; run the commentary categories locally with the commentaries imported.
- **Rebuilds.** Analysis and chunking changes need `python -m corpusstudy.indexing` locally and a `SCHEMA_VERSION` bump, or Docker installations keep their old index.
- **Latency.** A deeper `per_list` or more lists slow every question. Time `retrieval search` before and after; an answer's `usage.seconds` shows the total.

#### A.4.8 Checklist

- [ ] The problem is a case in the tuned set, and its expected passages are checked against the corpus
- [ ] There is a baseline, the cause is diagnosed with `retrieval search`, and one knob is changed
- [ ] The tuned set is read case by case; the gates hold and `quote_*` / `ref_*` stay at 100%
- [ ] The held-out set is run once, and it passes
- [ ] The answer evaluation is at 100%, `okf maintain` coverage hasn't dropped, the full suite passes, and a live run is done if the key is available
- [ ] Analysis or chunking changes come with a rebuilt index and a `SCHEMA_VERSION` bump
- [ ] The before and after numbers are in the commit message, and A.4.1 and §6.3 are updated
