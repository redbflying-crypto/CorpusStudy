# knowledge/

Curated Markdown knowledge files, one per book, theme, person, doctrine or event. They are drafted in Phase 4 and reviewed by a person before they are marked `reviewed`.

The format is Obsidian-style Markdown: YAML frontmatter plus `[[wikilinks]]` between files. `corpusstudy.schemas.KnowledgeFrontmatter` validates the frontmatter.

```markdown
---
id: justification
type: doctrine
title: Justification by Faith
title_fr: La justification par la foi
aliases: [justification, justified]
key_passages: ["Rom 3:21-26", "Rom 5:1", "Gal 2:16", "Eph 2:8-9"]
related: [faith, grace, imputation]
tradition: reformed
other_views: true
sources: [calvin, henry]
review_status: draft        # draft | reviewed
reviewed_by:                # required when reviewed
reviewed_on:                # required when reviewed (YYYY-MM-DD)
---

## Definition
...

## Reformed view
...

## Where other traditions differ
...
```

Rules:
- `id` and the entries in `related` are lowercase-hyphenated slugs that match file names.
- `key_passages` must be valid references, in English or French form ("Rom 3:21-26" or "Romains 3.21-26").
- `sources` may only name approved public-domain works: `calvin`, `henry`, `tsk`.
- Interpretive content is labelled as the Reformed view, and never presented as the text of Scripture.
