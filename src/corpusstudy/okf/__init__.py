"""Google Open Knowledge Format (OKF) v0.2 support for the CorpusStudy knowledge bundle.

- `document`: frontmatter parsing/rendering, links, footnotes
- `models`: tolerant OKF frontmatter (trust tiers, lifecycle, provenance)
- `bundle`: loading a bundle and checking OKF conformance
- `profile`: the strict CorpusStudy producer profile for our own concepts
- `indexes`: generated index.md and log.md
- `references`: source-work concepts generated from the ingestion manifest

Spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md
"""

from corpusstudy.okf.bundle import Bundle, Concept, Issue, load_bundle
from corpusstudy.okf.models import OKF_VERSION, OkfFrontmatter, TrustTier

__all__ = [
    "OKF_VERSION",
    "Bundle",
    "Concept",
    "Issue",
    "OkfFrontmatter",
    "TrustTier",
    "load_bundle",
]
