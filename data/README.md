# data/

Downloaded and generated corpus data lives here and is **git-ignored** (only this README is tracked).

Planned layout (Phase 2):

```
data/
  raw/          # source files as downloaded (USFM / JSON / CSV)
    kjv/
    lsg1910/
    commentaries/   # Calvin, Matthew Henry (public domain)
    tsk/            # Treasury of Scripture Knowledge
  processed/    # normalised verse JSONL, one file per translation
```

Only public-domain texts may be placed here.
