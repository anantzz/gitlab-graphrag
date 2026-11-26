"""End-to-end ingestion: fetch repos → parse → build graph → index (BM25 + vectors) → save."""
from __future__ import annotations

import logging

from .config import Settings
from .graph.builder import build_graph
from .index.store import CodeIndex
from .models import RepoInfo
from .parsing import parse_repo

log = logging.getLogger(__name__)


def ingest(settings: Settings, source: str = "gitlab", local_paths: list[str] | None = None) -> CodeIndex:
    if source == "gitlab":
        from .ingest.gitlab_source import fetch_repos

        repos = fetch_repos(settings)
    else:
        from .ingest.local_source import discover

        repos = discover(local_paths or [])
    if not repos:
        raise SystemExit("No repositories selected.")
    return build_index(settings, repos)


def build_index(settings: Settings, repos: list[RepoInfo]) -> CodeIndex:
    names = [r.name for r in repos]
    for r in repos:  # same project name in two subgroups -> use the full path to keep ids unique
        if names.count(r.name) > 1:
            r.name = r.full_path.replace("/", "__")
    parsed = {}
    chunks = []
    for repo in repos:
        files = parse_repo(repo, settings.max_file_bytes)
        parsed[repo.name] = files
        for info, summary in files:
            chunks.append(summary)
            chunks.extend(info.symbols)
    graph = build_graph(repos, parsed)
    index = CodeIndex.build(repos, chunks, graph, settings)
    index.save(settings.index_dir)
    log.info("Index saved to %s: %s", settings.index_dir, index.stats())
    return index
