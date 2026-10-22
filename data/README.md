# data/

Downloaded and generated corpus data lives here and is **git-ignored** (only this README is tracked).

Rebuild everything with `python -m corpusstudy.ingest`.

```
data/
  raw/
    kjv/            # eng-kjv.osis.xml
    lsg1910/        # 66 USFM files
    calvin-lcc/     # calcom.doc, imported by hand (--import calvin-lcc FILE)
  processed/
    KJV.jsonl       # {"book","chapter","verse","text"} per line; verse 0 = psalm title
    LSG1910.jsonl
    commentary/
      calvin-lcc.jsonl  # {"id","work","source","topic","refs","mentions","text"} per excerpt
    manifest.json   # sources, URLs, checksums, counts, repairs, corrections
```

Only public-domain texts may be placed here.
