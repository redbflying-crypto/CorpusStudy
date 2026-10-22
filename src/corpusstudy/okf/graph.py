"""The concept graph and trust-aware selection, for retrieval and reports.

OKF links are untyped directed edges (section 6.1); the graph also records which source
works each concept cites. Retrieval expands a matching concept by one hop to its
neighbours, keeping only concepts the bot may use (see `usable`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from corpusstudy.okf import document
from corpusstudy.okf.bundle import Bundle, Concept
from corpusstudy.okf.models import Status, TrustTier
from corpusstudy.okf.references import SOURCES_DIR


def usable(c: Concept, *, use_drafts: bool = False, now: datetime | None = None) -> bool:
    """Whether answers may draw on this concept.

    Default: human-reviewed and stable. With `use_drafts`, unreviewed drafts too (the
    answer must then label them). Deprecated or stale concepts are never used.
    """
    meta = c.meta
    if meta.lifecycle == Status.DEPRECATED or meta.is_stale(now):
        return False
    if meta.type == "Reference":
        return bool(meta.verified)
    if meta.trust_tier == TrustTier.HUMAN_REVIEWED and meta.lifecycle == Status.STABLE:
        return True
    return use_drafts


@dataclass
class ConceptGraph:
    out: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    into: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    cites: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))  # -> source works
    broken: list[tuple[str, str]] = field(default_factory=list)
    nodes: set[str] = field(default_factory=set)

    def neighbours(self, concept_id: str, *, backlinks: bool = True) -> set[str]:
        found = set(self.out.get(concept_id, set()))
        if backlinks:
            found |= self.into.get(concept_id, set())
        found.discard(concept_id)
        return found

    def expand(self, seeds: list[str], hops: int = 1, keep=lambda cid: True) -> list[str]:
        """Seeds first, then neighbours hop by hop (deterministic order), filtered by `keep`."""
        seen = [s for s in seeds if s in self.nodes and keep(s)]
        frontier = list(seen)
        for _ in range(hops):
            nxt = []
            for cid in frontier:
                for n in sorted(self.neighbours(cid)):
                    if n not in seen and keep(n):
                        seen.append(n)
                        nxt.append(n)
            frontier = nxt
        return seen

    def orphans(self) -> list[str]:
        """Knowledge concepts with no links in or out."""
        return sorted(
            n
            for n in self.nodes
            if not n.startswith("references/") and not self.out.get(n) and not self.into.get(n)
        )


def build_graph(bundle: Bundle) -> ConceptGraph:
    g = ConceptGraph()
    g.nodes = set(bundle.concepts)
    for c in bundle.concepts.values():
        for link in c.links:
            if not link.concept:
                continue
            if link.concept in bundle.concepts:
                g.out[c.id].add(link.concept)
                g.into[link.concept].add(c.id)
            else:
                g.broken.append((c.id, link.target))
        for src in c.meta.sources:
            path = document.resolve_path(src.resource, c.id)
            if path.startswith(SOURCES_DIR + "/"):
                g.cites[c.id].add(path.removesuffix(".md"))
    return g


def select(
    bundle: Bundle, *, use_drafts: bool = False, now: datetime | None = None
) -> dict[str, Concept]:
    """The concepts answers may use."""
    return {
        cid: c for cid, c in bundle.concepts.items() if usable(c, use_drafts=use_drafts, now=now)
    }
