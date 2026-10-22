"""Hybrid, OKF-aware retrieval over the search index.

For each kind of source (bible, commentary, knowledge) a query produces ranked lists:

    lookup              the passage(s) cited in the question, read from the Bible itself
    reference           chunks about a cited passage (commentary and knowledge use KJV
                        numbering; a French question's Louis Segond references are mapped)
    hits                commentary / knowledge about the verses that matched the words best
    phrase              the question's words in sequence (a quoted verse)
    all                 every content word present (BM25)
    keyword             any content word (BM25)
    graph               neighbours of the best knowledge concepts (one OKF link hop)

Lists are fused per kind with weighted reciprocal rank fusion (RRF), then the kinds are
interleaved so an answer sees Scripture, commentary and knowledge side by side. Terms of 4+
letters match as prefixes, which absorbs stemmer differences ("justification" ~ "justified").

Naming a commentator ("Matthew Henry", "Calvin") restricts commentary to that work and is
not searched as words. Knowledge chunks are kept only for concepts the bot may use
(`okf.graph.usable`): human-reviewed and stable unless `use_drafts`. Dense vectors plug in
later as one more list per kind (settings.embeddings).
"""

from __future__ import annotations

import re
import sqlite3
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from corpusstudy.config import Language, Translation, get_settings
from corpusstudy.corpus import bible
from corpusstudy.indexing.analysis import detect_language, fold, query_terms, stems
from corpusstudy.indexing.build import index_status
from corpusstudy.indexing.store import index_path, passage_bounds
from corpusstudy.okf.bundle import load_bundle
from corpusstudy.okf.graph import build_graph, select
from corpusstudy.retrieval.references import extract_references, strip_references
from corpusstudy.schemas import Chunk, PassageRef, RetrievalResult, Retriever, SourceType
from corpusstudy.versification import map_passage

RRF_K = 60
WEIGHTS = {
    "lookup": 3.0,
    "reference": 1.2,
    "hits": 0.8,
    "phrase": 1.5,
    "all": 1.0,
    "keyword": 0.7,
    "graph": 0.5,
}
KINDS = ("bible", "commentary", "knowledge")
MAX_LOOKUP_VERSES = 40
_PREFIX_MIN = 4

# Commentator names: (folded pattern, work). Matched names are removed from the words.
_WORKS = [
    (re.compile(r"\b(?:matthew\s+)?henry\b"), "henry"),
    (re.compile(r"\b(?:jean\s+|john\s+)?calvin\b"), "calvin"),
]
_COMMENTARY_WORDS = re.compile(r"\b(?:commentary|commentaries|commentaire|commentaires)\b")


class IndexNotBuilt(FileNotFoundError):
    def __init__(self, path: Path):
        super().__init__(f"{path} not found; run `python -m corpusstudy.indexing` first")


@dataclass
class Query:
    text: str
    language: Language
    refs: list[PassageRef]  # in the question language's numbering
    terms: list[str]  # content stems, no stopwords
    phrase: list[str]  # every stem in order, for a phrase match
    works: list[str] = field(default_factory=list)  # commentators named in the question
    wants_commentary: bool = False
    lists: dict[str, list[str]] = field(default_factory=dict)  # "<list>:<kind>" -> chunk ids


def _term(t: str) -> str:
    return f'"{t}"*' if len(t) >= _PREFIX_MIN else f'"{t}"'


class Search:
    def __init__(
        self,
        path: Path | None = None,
        *,
        use_drafts: bool | None = None,
        knowledge_root: Path | None = None,
    ):
        settings = get_settings()
        self.path = path or index_path()
        if not self.path.exists():
            raise IndexNotBuilt(self.path)
        if path is None and index_status(self.path) == "stale":
            warnings.warn("the search index is older than its inputs; rebuild it", stacklevel=2)
        self.con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        self.use_drafts = settings.use_drafts if use_drafts is None else use_drafts
        bundle = load_bundle(knowledge_root or settings.knowledge_dir)
        self.usable = set(select(bundle, use_drafts=self.use_drafts))
        self.graph = build_graph(bundle)
        self._chunks: dict[str, Chunk] = {}

    # ------------------------------------------------------------------ chunks

    def _chunk(self, chunk_id: str) -> Chunk:
        if chunk_id not in self._chunks:
            row = self.con.execute("SELECT data FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            self._chunks[chunk_id] = Chunk.model_validate_json(row[0])
        return self._chunks[chunk_id]

    def _allowed(self, chunk_id: str, kind: str, works: list[str]) -> bool:
        if kind == "knowledge":
            return chunk_id.split("#", 1)[0] in self.usable
        if kind == "commentary" and works:
            return self._chunk(chunk_id).work in works
        return True

    # ------------------------------------------------------------------ lists

    def _fts(self, q: Query, terms: list[str], lang: Language, kind: str, mode: str, limit: int):
        if not terms or (mode != "OR" and len(terms) < 2):
            return []
        if mode == "PHRASE":
            match = " + ".join(_term(t) for t in terms)
        else:
            match = f" {mode} ".join(_term(t) for t in terms)
        rows = self.con.execute(
            "SELECT c.id FROM fts JOIN chunks c ON c.rowid = fts.chunk "
            "WHERE fts MATCH ? AND fts.lang = ? AND fts.kind = ? ORDER BY bm25(fts) LIMIT ?",
            (match, str(lang), kind, limit * 4),
        ).fetchall()
        ids = list(dict.fromkeys(r[0] for r in rows))
        return [i for i in ids if self._allowed(i, kind, q.works)][:limit]

    def _about(self, q: Query, refs: list[PassageRef], kind: str, limit: int) -> list[str]:
        """Chunks whose passages overlap `refs` (KJV numbering), tightest first."""
        scored: dict[str, tuple[int, int]] = {}
        for ref in refs:
            start, stop = passage_bounds(ref)
            rows = self.con.execute(
                "SELECT c.id, r.start, r.stop FROM refs r JOIN chunks c ON c.rowid = r.chunk "
                "WHERE r.book = ? AND r.numbering = ? AND c.kind = ? AND r.start <= ? "
                "AND r.stop >= ?",
                (ref.book, str(Translation.KJV), kind, stop, start),
            ).fetchall()
            for cid, s, e in rows:
                if not self._allowed(cid, kind, q.works):
                    continue
                key = (e - s, -(min(e, stop) - max(s, start)))  # narrow and on-topic first
                if cid not in scored or key < scored[cid]:
                    scored[cid] = key
        return sorted(scored, key=lambda c: (scored[c], c))[:limit]

    def _lookup(self, refs: list[PassageRef], language: Language) -> list[Chunk]:
        tr = get_settings().translation_for(language)
        out = []
        for ref in refs:
            verses = bible(tr).passage(ref)
            if not verses:
                continue
            truncated = len(verses) > MAX_LOOKUP_VERSES
            verses = verses[:MAX_LOOKUP_VERSES]
            text = " ".join((f"[{v.ref.verse}] " if v.ref.verse else "") + v.text for v in verses)
            out.append(
                Chunk(
                    id=f"{tr}:{ref.osis}",
                    source_type=SourceType.SCRIPTURE,
                    text=text,
                    language=tr.language,
                    refs=[ref],
                    translation=tr,
                    metadata={"numbering": str(tr), "lookup": True, "truncated": truncated},
                )
            )
        return out

    def _kjv(self, refs: list[PassageRef], numbering: str) -> list[PassageRef]:
        if numbering == str(Translation.KJV):
            return refs
        return [map_passage(r, Translation(numbering), Translation.KJV) for r in refs]

    def _first_chunk(self, concept_id: str) -> str | None:
        row = self.con.execute(
            "SELECT id FROM chunks WHERE kind = 'knowledge' AND id LIKE ? ORDER BY rowid LIMIT 1",
            (f"{concept_id}#%",),
        ).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------ search

    def analyse(self, text: str, language: Language | None = None) -> Query:
        language = language or detect_language(text)
        refs = extract_references(text)
        words = strip_references(text)
        folded = fold(words)
        works = [work for pattern, work in _WORKS if pattern.search(folded)]
        wants = bool(works) or bool(_COMMENTARY_WORDS.search(folded))
        for pattern, _ in _WORKS:
            folded = pattern.sub(" ", folded)
        folded = _COMMENTARY_WORDS.sub(" ", folded)
        terms = [t for t in query_terms(folded, language) if not t.isdigit()]
        phrase = [t for t in stems(folded, language) if not t.isdigit()]
        return Query(text, language, refs, terms, phrase, works, wants)

    def search(
        self,
        text: str,
        language: Language | None = None,
        *,
        k: int = 10,
        kinds: tuple[str, ...] = KINDS,
        per_list: int = 30,
    ) -> list[RetrievalResult]:
        q = self.analyse(text, language)
        lists: dict[str, list[str]] = {}
        extra: dict[str, Chunk] = {}
        bible_numbering = str(get_settings().translation_for(q.language))

        if q.refs and "bible" in kinds:
            for c in self._lookup(q.refs, q.language):
                extra[c.id] = c
                lists.setdefault("lookup:bible", []).append(c.id)
        kjv_refs = self._kjv(q.refs, bible_numbering)

        for kind in kinds:
            # Commentary is English: a French question reaches it by reference and by the
            # verses its words matched ("hits"), not by its words.
            lang = Language.EN if kind == "commentary" else q.language
            if kind != "bible" and kjv_refs:
                lists[f"reference:{kind}"] = self._about(q, kjv_refs, kind, per_list)
            if kind == "commentary" and q.language != Language.EN:
                continue
            lists[f"phrase:{kind}"] = self._fts(q, q.phrase, lang, kind, "PHRASE", per_list)
            lists[f"all:{kind}"] = self._fts(q, q.terms, lang, kind, "AND", per_list)
            lists[f"keyword:{kind}"] = self._fts(q, q.terms, lang, kind, "OR", per_list)

        # Commentary and knowledge about the verses the words matched best.
        strong = (lists.get("phrase:bible") or [])[:2] or (lists.get("all:bible") or [])[:2]
        if strong and not q.refs:
            hit_refs = [r for cid in strong for r in self._chunk(cid).refs]
            for kind in ("commentary", "knowledge"):
                if kind in kinds:
                    found = self._about(q, self._kjv(hit_refs, bible_numbering), kind, per_list)
                    lists[f"hits:{kind}"] = found

        if "knowledge" in kinds:
            seeds = []
            for name in ("reference:knowledge", "phrase:knowledge", "all:knowledge",
                         "keyword:knowledge"):  # fmt: skip
                seeds += [cid.split("#", 1)[0] for cid in lists.get(name, [])[:2]]
            seeds = list(dict.fromkeys(seeds))
            neighbours = self.graph.expand(seeds, hops=1, keep=lambda c: c in self.usable)
            lists["graph:knowledge"] = [
                first for c in neighbours if c not in seeds and (first := self._first_chunk(c))
            ]
        q.lists = lists
        return self._fuse(q, lists, extra, k)

    def _fuse(
        self, q: Query, lists: dict[str, list[str]], extra: dict[str, Chunk], k: int
    ) -> list[RetrievalResult]:
        scores: dict[str, float] = defaultdict(float)
        via: dict[str, list[str]] = defaultdict(list)
        kind_of: dict[str, str] = {}
        for name, ids in lists.items():
            list_name, kind = name.split(":", 1)
            for rank, cid in enumerate(ids, 1):
                scores[cid] += WEIGHTS[list_name] / (RRF_K + rank)
                via[cid].append(name)
                kind_of[cid] = kind

        per_kind: dict[str, list[str]] = defaultdict(list)
        for cid in sorted(scores, key=lambda c: (-scores[c], c)):
            per_kind[kind_of[cid]].append(cid)
        # Interleave the kinds, strongest first; asking for commentary puts it first.
        order = sorted(per_kind, key=lambda kd: -scores[per_kind[kd][0]])
        if q.wants_commentary and "commentary" in order:
            order.remove("commentary")
            order.insert(0, "commentary")
        ranked: list[str] = []
        cursors = {kd: 0 for kd in order}
        while len(ranked) < k and any(cursors[kd] < len(per_kind[kd]) for kd in order):
            for kd in order:
                if cursors[kd] < len(per_kind[kd]) and len(ranked) < k:
                    ranked.append(per_kind[kd][cursors[kd]])
                    cursors[kd] += 1

        results = []
        for rank, cid in enumerate(ranked, 1):
            chunk = extra.get(cid) or self._chunk(cid)
            names = {n.split(":", 1)[0] for n in via[cid]}
            if len(names) > 1:
                retriever = Retriever.FUSED
            else:
                retriever = {
                    "lookup": Retriever.LOOKUP,
                    "reference": Retriever.REFERENCE,
                    "hits": Retriever.REFERENCE,
                    "graph": Retriever.GRAPH,
                }.get(names.pop(), Retriever.KEYWORD)
            results.append(
                RetrievalResult(
                    chunk=chunk,
                    score=round(scores[cid], 6),
                    retriever=retriever,
                    rank=rank,
                    via=via[cid],
                )  # fmt: skip
            )
        return results
