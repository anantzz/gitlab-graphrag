"""CodeIndex: knowledge graph + chunk store + BM25 + vectors, with hybrid search and graph expansion."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import numpy as np
from rank_bm25 import BM25Okapi

from ..models import RepoInfo, Symbol
from .embeddings import make_embedder
from .text import tokenize

log = logging.getLogger(__name__)

RRF_K = 60
# How strongly relevance flows along each edge type during graph expansion (out, in)
EDGE_WEIGHTS = {
    "CALLS": (0.65, 0.55),      # callee / caller
    "DEFINES": (0.45, 0.55),    # child member / enclosing class-file
    "INHERITS": (0.5, 0.45),
    "IMPORTS": (0.3, 0.3),
    "CONTAINS": (0.0, 0.0),
}
KIND_PRIOR = {"function": 1.0, "method": 1.0, "class": 0.95, "file": 0.8, "doc": 0.85}


@dataclass
class Hit:
    id: str
    score: float
    via: str = "search"                       # search | symbol | graph
    reasons: list[str] = field(default_factory=list)


class CodeIndex:
    def __init__(self, repos: dict[str, RepoInfo], chunks: dict[str, Symbol], graph: nx.MultiDiGraph,
                 vectors: np.ndarray, embedder, meta: dict):
        self.repos = repos
        self.chunks = chunks
        self.graph = graph
        self.vectors = vectors
        self.embedder = embedder
        self.meta = meta
        self.ids = list(chunks.keys())
        self.pos = {cid: i for i, cid in enumerate(self.ids)}
        self.repo_of = np.array([chunks[c].repo for c in self.ids])
        self.bm25 = BM25Okapi([tokenize(chunks[c].index_text()) or ["_"] for c in self.ids])
        self.name_index: dict[str, list[str]] = defaultdict(list)
        for cid, ch in chunks.items():
            if ch.kind != "doc":
                self.name_index[ch.name.lower()].append(cid)
                self.name_index[ch.qualname.lower()].append(cid)

    # ------------------------------------------------------------------ persistence
    @classmethod
    def build(cls, repos: list[RepoInfo], chunks: list[Symbol], graph: nx.MultiDiGraph, settings) -> "CodeIndex":
        embedder = make_embedder(settings.embedding_backend, settings.embedding_model)
        log.info("Embedding %d chunks with %s", len(chunks), embedder.name)
        vectors = embedder.embed_documents([c.index_text(1500) for c in chunks])
        meta = {"embedding_backend": embedder.name, "embedding_model": getattr(embedder, "model_name", None)}
        return cls({r.name: r for r in repos}, {c.id: c for c in chunks}, graph, vectors, embedder, meta)

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "repos.json").write_text(json.dumps([r.to_dict() for r in self.repos.values()], indent=2))
        with open(directory / "chunks.jsonl", "w") as f:
            for cid in self.ids:
                f.write(json.dumps(self.chunks[cid].to_dict()) + "\n")
        (directory / "graph.json").write_text(json.dumps(nx.node_link_data(self.graph, edges="links")))
        np.save(directory / "vectors.npy", self.vectors)
        (directory / "meta.json").write_text(json.dumps(self.meta, indent=2))

    @classmethod
    def load(cls, directory: Path) -> "CodeIndex":
        if not (directory / "meta.json").exists():
            raise SystemExit(f"No index found in {directory}. Run `graphrag-gitlab ingest` first.")
        repos = {r["name"]: RepoInfo(**r) for r in json.loads((directory / "repos.json").read_text())}
        chunks = {}
        with open(directory / "chunks.jsonl") as f:
            for line in f:
                d = json.loads(line)
                chunks[d["id"]] = Symbol(**d)
        graph = nx.node_link_graph(json.loads((directory / "graph.json").read_text()), multigraph=True,
                                   directed=True, edges="links")
        meta = json.loads((directory / "meta.json").read_text())
        embedder = make_embedder(meta["embedding_backend"], meta.get("embedding_model") or "")
        if embedder.name != meta["embedding_backend"]:
            raise SystemExit(f"Index was built with {meta['embedding_backend']} embeddings; install it or re-ingest.")
        return cls(repos, chunks, graph, np.load(directory / "vectors.npy"), embedder, meta)

    # ------------------------------------------------------------------ search
    def _mask(self, repos: list[str] | None) -> np.ndarray:
        if not repos:
            return np.ones(len(self.ids), dtype=bool)
        return np.isin(self.repo_of, repos)

    def bm25_search(self, query: str, k: int, repos: list[str] | None = None) -> list[tuple[str, float]]:
        toks = tokenize(query)
        if not toks or not self.ids:
            return []
        scores = np.where(self._mask(repos), self.bm25.get_scores(toks), -np.inf)
        order = np.argsort(-scores)[:k]
        return [(self.ids[i], float(scores[i])) for i in order if scores[i] > 0]

    def vector_search(self, query: str, k: int, repos: list[str] | None = None) -> list[tuple[str, float]]:
        if not len(self.ids):
            return []
        sims = np.where(self._mask(repos), self.vectors @ self.embedder.embed_query(query), -np.inf)
        order = np.argsort(-sims)[:k]
        floor = 0.15 if self.meta["embedding_backend"] == "hashing" else 0.3
        return [(self.ids[i], float(sims[i])) for i in order if sims[i] >= floor]

    def lookup_symbols(self, names: list[str], repos: list[str] | None = None) -> list[str]:
        out = []
        for n in names:
            key = n.strip("`()").lower()
            ids = self.name_index.get(key, []) or self.name_index.get(key.split(".")[-1], [])
            out.extend(i for i in ids if not repos or self.chunks[i].repo in repos)
        return list(dict.fromkeys(out))

    def hybrid_search(self, queries: list[str], k: int = 8, repos: list[str] | None = None,
                      symbols: list[str] | None = None) -> list[Hit]:
        """Reciprocal-rank fusion of BM25 + vector results over several query variants, plus exact symbol hits."""
        fused: dict[str, Hit] = {}

        def add(cid: str, score: float, via: str, reason: str) -> None:
            h = fused.setdefault(cid, Hit(cid, 0.0, via))
            h.score += score
            if reason not in h.reasons:
                h.reasons.append(reason)

        for q in queries:
            for rank, (cid, _) in enumerate(self.bm25_search(q, 40, repos)):
                add(cid, 1 / (RRF_K + rank), "search", "bm25")
            for rank, (cid, _) in enumerate(self.vector_search(q, 40, repos)):
                add(cid, 1 / (RRF_K + rank), "search", "vector")
        for cid in self.lookup_symbols(symbols or [], repos):
            add(cid, 2 / RRF_K, "symbol", "exact symbol match")
            fused[cid].via = "symbol"
        for h in fused.values():
            h.score *= KIND_PRIOR.get(self.chunks[h.id].kind, 0.8)
        return sorted(fused.values(), key=lambda h: -h.score)[:k]

    # ------------------------------------------------------------------ graph
    def relations(self, cid: str, limit: int = 12) -> list[tuple[str, str, str]]:
        """(direction, edge_type, other_node) for a node, skipping structural noise."""
        out = []
        if cid not in self.graph:
            return out
        for _, dst, data in self.graph.out_edges(cid, data=True):
            if data["type"] != "CONTAINS":
                out.append(("out", data["type"], dst))
        for src, _, data in self.graph.in_edges(cid, data=True):
            if data["type"] not in ("CONTAINS",) and not src.startswith("repo:"):
                out.append(("in", data["type"], src))
        return out[:limit]

    def expand(self, seeds: list[Hit], hops: int = 1, limit: int = 8,
               edge_types: set[str] | None = None) -> list[Hit]:
        """Spread relevance from seed chunks over the graph; returns new neighbor hits.

        Seeds that are directly connected to a higher-ranked seed get a boost (graph-confirmed relevance),
        so callers should re-sort `seeds` afterwards.
        """
        seen = {h.id for h in seeds}
        seed_by_id = {h.id: h for h in seeds}
        rank = {h.id: i for i, h in enumerate(seeds)}
        frontier = {h.id: h.score for h in seeds}
        found: dict[str, Hit] = {}
        for hop in range(hops):
            nxt: dict[str, float] = {}
            for nid, score in frontier.items():
                for direction, etype, other in self.relations(nid, limit=40):
                    if edge_types and etype not in edge_types:
                        continue
                    if other not in self.chunks:
                        continue
                    w = EDGE_WEIGHTS[etype][0 if direction == "out" else 1]
                    s = score * w
                    if s <= 0:
                        continue
                    label = self.chunks[nid].qualname
                    verb = {("out", "CALLS"): "called by", ("in", "CALLS"): "calls",
                            ("out", "DEFINES"): "member of", ("in", "DEFINES"): "defines",
                            ("out", "INHERITS"): "base class of", ("in", "INHERITS"): "subclass of",
                            ("out", "IMPORTS"): "imported by", ("in", "IMPORTS"): "imports"}[(direction, etype)]
                    if other in seed_by_id:
                        if hop == 0 and rank.get(nid, 1e9) < rank[other]:
                            seed_by_id[other].score += s * 0.5
                            seed_by_id[other].reasons.append(f"{verb} {label}")
                        continue
                    if other in seen:
                        continue
                    h = found.setdefault(other, Hit(other, 0.0, "graph"))
                    h.score = max(h.score, s)
                    reason = f"{verb} {label}"
                    if reason not in h.reasons:
                        h.reasons.append(reason)
                    nxt[other] = max(nxt.get(other, 0.0), s)
            seen |= set(nxt)
            frontier = nxt
        return sorted(found.values(), key=lambda h: -h.score)[:limit]

    def repo_summary(self, hits: list[Hit]) -> list[dict]:
        agg: dict[str, dict] = {}
        for h in hits:
            ch = self.chunks[h.id]
            a = agg.setdefault(ch.repo, {"repo": ch.repo, "score": 0.0, "paths": []})
            a["score"] += h.score
            if ch.path not in a["paths"]:
                a["paths"].append(ch.path)
        for a in agg.values():
            r = self.repos.get(a["repo"])
            a["full_path"] = r.full_path if r else a["repo"]
            a["web_url"] = r.web_url if r else None
        return sorted(agg.values(), key=lambda a: -a["score"])

    def citation_url(self, cid: str) -> str | None:
        ch = self.chunks[cid]
        repo = self.repos.get(ch.repo)
        return repo.blob_url(ch.path, ch.start_line, ch.end_line) if repo else None

    def stats(self) -> dict:
        kinds: dict[str, int] = defaultdict(int)
        for ch in self.chunks.values():
            kinds[ch.kind] += 1
        etypes: dict[str, int] = defaultdict(int)
        for _, _, d in self.graph.edges(data=True):
            etypes[d["type"]] += 1
        return {"repos": list(self.repos), "chunks": dict(kinds), "nodes": self.graph.number_of_nodes(),
                "edges": dict(etypes), **self.meta}
