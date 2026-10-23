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

**Tuning procedure:**
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

**Adding a route:**
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

**Re-import a commentary.**
1. Run `python -m corpusstudy.ingest --import <source> <file>`.
2. Run `okf check`, which catches excerpt ids or texts that changed under existing citations.
3. Run the retrieval evaluation.
4. Rebuild the index.

**Add a third language or translation.** This is a design change, not a configuration change:
- `config.Translation`, `TRANSLATION_LANGUAGE` and `Settings.translations` map exactly one translation per language;
- references, quotations and the validator follow the answer language's translation;
- you need a parser, a pinned source, versification tables and the mapping to KJV, analysis rules for the language (stemmer, stopwords), concept `title_<lang>` and aliases, fixed texts in that language, and evaluation cases.

Plan it as a phase.

**Add embeddings (vectors).** `CORPUS_EMBEDDINGS` is reserved but not implemented. Add a `vector` list per kind to `Search.search`, give it a weight, store the vectors alongside the index (and bump `SCHEMA_VERSION`), and tune on the `paraphrase` category, which keyword search handles worst. Keep the gates green.

**Change a fixed text** (safety, advice, refusal or translation). Edit `answer.SAFETY` or `answer._TEXT` in both languages. The safety tests and the offline evaluation check that they appear first. The owner reviews crisis numbers and wording.

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
