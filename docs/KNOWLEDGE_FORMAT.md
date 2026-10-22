# Knowledge format: OKF v0.2 and the CorpusStudy profile

`knowledge/` is a knowledge bundle in **Google's Open Knowledge Format (OKF) v0.2** ([spec](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)). It is curated, kept in git, and readable by any OKF consumer. This page describes the OKF features we use and the stricter rules we apply to our own concepts (the *producer profile*).

```bash
.venv/bin/python -m corpusstudy.okf check         # OKF conformance + producer profile
.venv/bin/python -m corpusstudy.okf check --no-profile   # OKF conformance only
.venv/bin/python -m corpusstudy.okf index         # regenerate every index.md
.venv/bin/python -m corpusstudy.okf references    # regenerate references/sources from the manifest
```

## Bundle layout

```
knowledge/
  index.md          # root index; frontmatter okf_version: "0.2" (the only frontmatter allowed)
  log.md            # dated change history, newest first
  books/ themes/ people/ doctrines/ events/     # knowledge concepts, each with an index.md
  references/sources/                           # one Reference concept per source work (generated)
```

- A concept's identity is its path without `.md`, e.g. `doctrines/justification`. There is no `id` field.
- `index.md` and `log.md` are reserved and generated. Every other `.md` file is a concept and must have frontmatter with a non-empty `type`.
- Concepts link with standard Markdown links, preferably bundle-absolute ones such as `[faith](/doctrines/faith.md)`. No `[[wikilinks]]`.

## A knowledge concept

```markdown
---
type: Doctrine
title: Justification by Faith
description: God declares sinners righteous through faith in Christ alone.
tags: [soteriology, reformation]
status: draft
generated: { by: corpusstudy/drafter-1, at: 2026-10-23T10:00:00Z }
sources:
  - id: henry-rom3
    resource: /references/sources/henry-concise.md#Rom.3.21-Rom.3.31
    title: Matthew Henry on Romans 3:21-31
  - id: kjv-rom5
    resource: /references/sources/kjv.md#Rom.5.1
title_fr: La justification par la foi
aliases: [justified, imputed righteousness]
key_passages: ["Rom 3:21-26", "Rom 5:1", "Gal 2:16"]
tradition: reformed
other_views: true
---

# Definition
Justification is God's legal declaration that a sinner is righteous...[^henry-rom3]
Being justified by faith, we have peace with God.[^kjv-rom5]

# Where other traditions differ
...

See also [faith](/doctrines/faith.md).

[^henry-rom3]: Matthew Henry on Romans 3:21-31
[^kjv-rom5]: Romans 5:1 (KJV)
```

The OKF keys are `type`, `title`, `description`, `tags`, `status`, `generated`, `verified` and `sources`. The project's own keys are `title_fr`, `aliases`, `key_passages`, `tradition` and `other_views`; OKF allows producers to add keys like these.

## Provenance

Each `sources` entry points into `/references/sources/`. That keeps every cited source public domain, and each source work is described by its own Reference concept: edition, licence and checksum. The part after `#` (the anchor) selects what is cited:

| Source | Anchor | Example |
|---|---|---|
| `kjv.md`, `lsg1910.md` | OSIS passage in that translation's numbering | `#Rom.3.21-Rom.3.26` |
| `calvin-lcc.md` | excerpt id | `#vi-p90` |
| `henry-concise.md` | excerpt id | `#Rom.3.21-Rom.3.31` |

Each claim is credited with a Markdown footnote whose label is a `sources` id (OKF section 5.1). The profile checks that every footnote has a source, that every anchor resolves to a real passage or excerpt, and warns about sources that are never cited.

## Trust and review

| Field | Meaning |
|---|---|
| `generated: { by, at }` | Who wrote the current content, and when. Claude: `corpusstudy/drafter-<n>`; the ingest pipeline: `process:ingest` |
| `verified: [{ by, at }]` | Who confirmed it. Your review adds `human:<your-id>` |
| `status` | `draft` → `stable` once reviewed; `deprecated` keeps a concept for history |

Trust tiers come from `verified`, as OKF section 5.3 defines them:
- **unverified:** no verification yet;
- **machine-confirmed:** verified by processes only;
- **human-reviewed:** verified by a person.

The bot uses only human-reviewed knowledge concepts, unless drafts are explicitly enabled. The generated source references are machine-confirmed, by the ingest process and its checksums.

## Producer profile (our rules)

OKF readers are tolerant: they accept unknown types and keys, missing optional fields and broken links. For our own bundle, `okf check` also enforces the following:

- **Type:** `type` is one of `Bible Book`, `Theme`, `Person`, `Doctrine`, `Event` or `Reference`, and the file sits in the matching directory.
- **Required fields:** `title`, `description`, `generated` (with `at`) and an explicit `status` are present, and every actor follows the OKF convention.
- **Review state:**
  - `status: stable` requires a `human:` verification, except for Reference concepts.
  - A concept changed after its last review must go back to `draft`.
- **Knowledge concepts:**
  - `title_fr` is set.
  - `key_passages` parse and exist in the KJV.
  - `tradition` is a known stance.
  - There is at least one source.
- **Provenance:** footnotes and `sources` ids match, and every anchor resolves.
- **Links:** standard Markdown links only.
