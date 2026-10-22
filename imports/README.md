# Commentary imports

The two public-domain commentaries are not downloaded automatically. Put the files here, with
exactly these names, and (re)start the container (`docker compose restart`), or run
`python -m corpusstudy.prepare` locally:

| File | Work |
|---|---|
| `calcom.doc` | John Calvin, *Calvin: Commentaries* (Library of Christian Classics XXIII), CCEL |
| `mhcc.txt` | Matthew Henry, *Concise Commentary on the Whole Bible*, CCEL plain text |

They are imported when new or changed, then the corpus and the search index are rebuilt.
Files in this folder are not committed (see `.gitignore`).
