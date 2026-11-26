"""Build the code knowledge graph.

Nodes:  repo:*, file:*, sym:* (class / function / method / doc), ext:* (external dependency)
Edges:  CONTAINS (repo→file), DEFINES (file→symbol, class→method), IMPORTS (file→file|ext),
        CALLS (function→function), INHERITS (class→class)
"""
from __future__ import annotations

import posixpath
from collections import defaultdict

import networkx as nx

from ..models import FileInfo, RepoInfo, Symbol, repo_id

CODE_SUFFIXES = (".py", ".java", ".kt", ".scala", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".cs", ".php",
                 ".rb", ".rs", ".c", ".h", ".cpp", ".hpp")


def _node_attrs(sym: Symbol) -> dict:
    return dict(kind=sym.kind, repo=sym.repo, path=sym.path, name=sym.name, qualname=sym.qualname,
                language=sym.language, start_line=sym.start_line, end_line=sym.end_line)


class _ImportResolver:
    """Map an import string to a file in the same repo (best effort, language agnostic)."""

    def __init__(self, paths: list[str]):
        self.paths = paths
        self.by_stem: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            stem = p
            for suf in CODE_SUFFIXES:
                if stem.endswith(suf):
                    stem = stem[: -len(suf)]
                    break
            if stem.endswith("/__init__") or stem.endswith("/index"):
                self.by_stem[stem.rsplit("/", 1)[0]].append(p)
            self.by_stem[stem].append(p)
        self.dirs: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            self.dirs[posixpath.dirname(p)].append(p)

    def _match_suffix(self, key: str) -> list[str]:
        if key in self.by_stem:
            return self.by_stem[key]
        hits = [p for stem, ps in self.by_stem.items() if stem.endswith("/" + key) for p in ps]
        return hits if len(hits) <= 3 else []

    def resolve(self, importer: str, imp: str) -> list[str]:
        if not imp:
            return []
        here = posixpath.dirname(importer)
        if imp.startswith("./") or imp.startswith("../"):            # JS/TS relative
            return self._match_suffix(posixpath.normpath(posixpath.join(here, imp)))
        if imp.startswith("."):                                       # python relative
            level = len(imp) - len(imp.lstrip("."))
            base = here
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            rest = imp.lstrip(".").replace(".", "/")
            return self._match_suffix(posixpath.join(base, rest) if rest else base)
        key = imp.replace("\\", "/").replace("::", "/").replace(".", "/").strip("/")
        if key.startswith("crate/"):
            key = key[6:]
        hits = self._match_suffix(key)
        if not hits and "/" in key:                                   # Go package dir / Java wildcard
            d = key.rstrip("/*")
            hits = [p for dpath, ps in self.dirs.items() if dpath == d or dpath.endswith("/" + d) for p in ps][:20]
        return hits

    def is_internal(self, name: str) -> bool:
        """True if `name` (a/b/c) looks like a package that lives inside this repo."""
        needle = "/" + name.strip("/") + "/"
        return any(needle in "/" + stem + "/" for stem in self.by_stem) or any(
            needle in "/" + d + "/" for d in self.dirs)


# Attribute calls with these names are almost always stdlib/container methods, not project code
COMMON_METHODS = {
    "get", "set", "put", "add", "append", "extend", "insert", "remove", "pop", "push", "update", "clear", "copy",
    "keys", "values", "items", "join", "split", "strip", "format", "replace", "lower", "upper", "startswith",
    "endswith", "read", "write", "close", "open", "map", "filter", "reduce", "forEach", "toString", "equals",
    "hashCode", "length", "size", "isEmpty", "contains", "trim", "then", "catch", "log", "info", "debug", "warn",
    "error", "print", "println", "printf", "encode", "decode", "parse", "stringify", "sort", "count", "index",
}


def _ext_name(imp: str) -> str:
    imp = imp.lstrip("@").replace("\\", "/").replace("::", "/").replace(".", "/")
    parts = [p for p in imp.split("/") if p]
    if not parts:
        return ""
    if parts[0] in {"com", "org", "io", "net", "github", "gitlab", "golang", "microsoft", "system"} and len(parts) > 2:
        return "/".join(parts[:3])
    return "/".join(parts[:2]) if parts[0] in {"github", "gitlab"} else parts[0]


def build_graph(repos: list[RepoInfo], parsed: dict[str, list[tuple[FileInfo, Symbol]]]) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for repo in repos:
        rid = repo_id(repo.name)
        g.add_node(rid, kind="repo", repo=repo.name, name=repo.name, qualname=repo.full_path, path="")
        files = parsed.get(repo.name, [])
        resolver = _ImportResolver([f.path for f, _ in files])
        by_name: dict[str, list[Symbol]] = defaultdict(list)
        file_imports: dict[str, set[str]] = defaultdict(set)

        for info, summary in files:
            g.add_node(summary.id, **_node_attrs(summary))
            g.add_edge(rid, summary.id, type="CONTAINS")
            for sym in info.symbols:
                g.add_node(sym.id, **_node_attrs(sym))
                g.add_edge(sym.parent or summary.id, sym.id, type="DEFINES")
                if sym.kind != "doc":
                    by_name[sym.name].append(sym)

        for info, summary in files:
            for imp in dict.fromkeys(info.imports):
                targets = [t for t in resolver.resolve(info.path, imp) if t != info.path]
                if targets:
                    for t in targets:
                        tid = f"file:{repo.name}:{t}"
                        if t not in file_imports[info.path]:
                            g.add_edge(summary.id, tid, type="IMPORTS")
                            file_imports[info.path].add(t)
                else:
                    ext = _ext_name(imp)
                    if ext and not ext.startswith(".") and not resolver.is_internal(ext):
                        eid = f"ext:{ext}"
                        if eid not in g:
                            g.add_node(eid, kind="external", name=ext, qualname=ext, repo="", path="")
                        if not g.has_edge(summary.id, eid):
                            g.add_edge(summary.id, eid, type="IMPORTS")

        for info, summary in files:
            imported = file_imports[info.path]
            for sym in info.symbols:
                for base in sym.bases:
                    for target in by_name.get(base, []):
                        if target.kind == "class" and target.id != sym.id:
                            g.add_edge(sym.id, target.id, type="INHERITS")
                for raw in sym.calls:
                    if "." in raw.lstrip("."):  # typed receiver: "Type.method"
                        typ, meth = raw.rsplit(".", 1)
                        for c in by_name.get(meth, []):
                            if c.qualname == f"{typ}.{meth}" or c.qualname.endswith(f".{typ}.{meth}"):
                                g.add_edge(sym.id, c.id, type="CALLS")
                        continue
                    is_attr = raw.startswith(".")
                    callee = raw.lstrip(".")
                    cands = [c for c in by_name.get(callee, []) if c.id != sym.id and c.kind != "file"]
                    if not cands:
                        continue
                    same_file = [c for c in cands if c.path == sym.path]
                    if is_attr and callee in COMMON_METHODS and not same_file:
                        continue
                    if not is_attr and sym.kind == "method":  # self.foo(): prefer the same class
                        same_class = [c for c in same_file if c.parent == sym.parent]
                        same_file = same_class or same_file
                    via_import = [c for c in cands if c.path in imported]
                    chosen = same_file or via_import or (cands if len(cands) <= 2 else [])
                    for c in chosen:
                        g.add_edge(sym.id, c.id, type="CALLS")
    return g
