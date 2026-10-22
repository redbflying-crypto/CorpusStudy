# Running CorpusStudy (runbook)

CorpusStudy runs in one Docker container on your own computer. The chat page is at
<http://127.0.0.1:8000> and is reachable from this computer only.

What lives where:

| Where | What | Kept when |
|---|---|---|
| `.env` (next to `compose.yaml`) | your API key and settings; read when the container starts, never copied into the image | you delete it |
| `knowledge/` (git) | the OKF knowledge bundle you review; mounted read-only | always: it is in git |
| `imports/` | the two commentary files you supply | you delete them |
| volume `corpus-data` | downloaded Bibles, imported commentaries, the processed corpus | `docker compose down -v` deletes it |
| volume `corpus-index` | the search index | the same; it is rebuilt automatically |

Everything in the two volumes can be rebuilt; nothing there needs a backup.

## First start

Requirements: Docker with Compose 2.24 or later (Docker Desktop on macOS or Windows, Docker
Engine on Linux), and internet access for the first start.

1. Get the code: `git clone` the repository and `cd` into it.
2. Your API key (optional for a first look):
   ```bash
   cp .env.example .env
   ```
   Open `.env` in an editor and fill in `ANTHROPIC_API_KEY=`. Do not paste the key anywhere
   else. Without a key, verse lookups and the safety messages work and other questions return
   a "not available" message.
3. The commentaries (optional, recommended): copy `calcom.doc` (Calvin) and `mhcc.txt`
   (Matthew Henry) into `imports/` (see `imports/README.md`).
4. Start it:
   ```bash
   docker compose up -d
   docker compose logs -f        # watch the first start; Ctrl+C stops watching, not the app
   ```
   The first start builds the image, downloads the two Bibles from their pinned sources
   (checked against the committed checksums), imports the commentaries and builds the search
   index: a few minutes. Later starts take seconds.
5. Open <http://127.0.0.1:8000>.

`docker compose ps` shows the container as `healthy` once the page is up.

## Everyday use

| Task | Command |
|---|---|
| Stop / start | `docker compose stop` / `docker compose start` |
| After `git pull` (new code or knowledge) | `docker compose up -d --build` |
| Logs (one line per answer: route, outcome, tokens, cost, time; never your question) | `docker compose logs -f` |
| Another port (8000 in use) | set `CORPUS_HOST_PORT=8080` in `.env`, then `docker compose up -d` |
| Use draft (unreviewed) knowledge too | set `CORPUS_USE_DRAFTS=true` in `.env`, then `docker compose up -d` |
| Change the model's effort | set `CORPUS_LLM_EFFORT=high` (low, medium, high, xhigh, max) in `.env`, then `docker compose up -d` |

A change to `.env` needs `docker compose up -d` (which recreates the container), not just a
restart.

## Rebuilding the corpus

- **New or updated commentary file:** put it in `imports/` and run `docker compose restart`.
  The container imports it and rebuilds the corpus and the index.
- **Everything from scratch:** `docker compose down -v` (deletes both volumes), then
  `docker compose up -d`.
- **A checksum error on start** means a downloaded Bible differs from the committed
  checksums. Do not override it; report it (the sources are pinned to exact commits).

## Re-indexing

The index is rebuilt on start whenever the corpus or the knowledge bundle changed, so after
any change a `docker compose restart` is enough. To rebuild it by hand while running:

```bash
docker compose exec corpusstudy python -m corpusstudy.indexing build
docker compose restart
```

## Reviewing knowledge concepts

Concepts are drafts until you review them; the bot uses only reviewed ones (unless
`CORPUS_USE_DRAFTS=true`). The review runs on the files in `knowledge/`, so it works the same
on GitHub (pull request review), on your computer with Python, or through the container.

1. Read the concept (`knowledge/doctrines/election.md`, for example) and check it against its
   footnoted sources. To read a source passage or excerpt:
   ```bash
   docker compose exec corpusstudy python -m corpusstudy.retrieval show KJV:Rom.9.11-Rom.9.16
   docker compose exec corpusstudy python -m corpusstudy.retrieval show calvin-lcc:xi-p60
   ```
2. Ask for changes, or approve it. Approving stamps it `verified` by you and sets it `stable`.
   The app mounts `knowledge/` read-only; the `okf` tools service mounts it read-write:
   ```bash
   docker compose run --rm okf verify doctrines/election
   ```
   (With Python on your computer: `python -m corpusstudy.okf verify doctrines/election`.)
   The command refuses a concept that fails the format checks.
3. Commit and push the change (`git add knowledge && git commit && git push`), then
   `docker compose restart` so the index includes it.

## Updating the knowledge bundle

Always through the bundle's own commands, then a check, then git:

```bash
docker compose run --rm okf new Theme hope "Hope"   # a draft skeleton with the type's headings
# edit knowledge/themes/hope.md: sources, footnotes, title_fr, aliases, key_passages
docker compose run --rm okf check                   # must report 0 errors
docker compose run --rm okf index                   # refresh the folder indexes
docker compose run --rm okf log "Drafted Hope" --kind Creation
docker compose run --rm okf touch themes/hope       # after editing a reviewed concept
docker compose run --rm okf deprecate themes/old --reason "merged into themes/hope"
```

`docs/KNOWLEDGE_FORMAT.md` describes the format, the checks and the naming rules for
aliases. Commit, push, then `docker compose restart`.

## Checks

```bash
docker compose exec corpusstudy python -m corpusstudy.generation smoke      # two live answers (needs the key)
docker compose exec corpusstudy python -m corpusstudy.generation eval       # offline evaluation
docker compose exec corpusstudy python -m corpusstudy.generation eval --live --judge   # live, with cost
docker compose run --rm okf check
```

## Troubleshooting

| Symptom | What to do |
|---|---|
| The page does not open | `docker compose ps` (is it healthy?) and `docker compose logs`; the first start takes a few minutes |
| "ANTHROPIC_API_KEY is not set" | add it to `.env`, then `docker compose up -d` |
| Port already in use | `CORPUS_HOST_PORT=8080` in `.env`, then `docker compose up -d` |
| "could not build the corpus" on first start | no internet access, or a proxy: see below |
| Behind a company proxy that inspects TLS | set `HTTPS_PROXY` in `.env`, and build with the proxy's CA certificate: `docker build --secret id=extra_ca,src=company-ca.crt -t corpusstudy:local .`; give the container the same certificate (`SSL_CERT_FILE`) for the first download |
| An answer is "not verified: passages shown" | the model's citations failed the checks twice; the logs say which ("warnings") |

## Security notes

- The API key is read from `.env` at start and lives only in the running container's
  environment; `.dockerignore` keeps `.env` out of the image and git ignores it.
- The page listens on 127.0.0.1 only. Do not change the port binding to `0.0.0.0` unless you
  add authentication in front of it.
- Logs never contain your questions or the answers.
