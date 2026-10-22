# data/

Downloaded and generated corpus data lives here and is **git-ignored** (only this README is tracked).

Rebuild everything with `python -m corpusstudy.ingest`.

```
data/
  raw/
    kjv/            # eng-kjv.osis.xml
    lsg1910/        # 66 USFM files
  processed/
    KJV.jsonl       # {"book","chapter","verse","text"} per line; verse 0 = psalm title
    LSG1910.jsonl
    manifest.json   # sources, URLs, checksums, counts, repairs
```

Commentaries (Calvin, Matthew Henry, TSK) will go under `raw/` once a source is reachable.

Only public-domain texts may be placed here.
