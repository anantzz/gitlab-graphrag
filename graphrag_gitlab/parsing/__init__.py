"""Walk a repository and turn every relevant file into a FileInfo with Symbols."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ..models import FileInfo, RepoInfo, Symbol, file_id, symbol_id
from .generic_parser import parse_generic
from .python_parser import parse_python

log = logging.getLogger(__name__)

CODE_EXT = {
    ".py": "python", ".java": "java", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".cs": "csharp",
    ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala", ".php": "php", ".rb": "ruby", ".rs": "rust",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
}
DOC_EXT = {".md": "markdown", ".rst": "rst", ".txt": "text", ".adoc": "asciidoc"}
CONFIG_EXT = {
    ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".xml": "xml", ".properties": "properties",
    ".toml": "toml", ".gradle": "gradle", ".sql": "sql", ".sh": "shell", ".tf": "terraform", ".graphql": "graphql",
    ".proto": "protobuf",
}
CONFIG_NAMES = {"Dockerfile": "dockerfile", "Makefile": "make", "Jenkinsfile": "groovy", ".gitlab-ci.yml": "yaml"}
SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "target", "out", "bin", "obj", "vendor", "venv", ".venv", "__pycache__",
    ".idea", ".vscode", ".gradle", "coverage", ".next", ".nuxt", "site-packages", ".tox", ".mypy_cache",
}
SKIP_FILES = re.compile(r"(\.min\.js|\.lock|-lock\.json|\.map|\.snap)$")
FILE_SUMMARY_LINES = 60
DOC_SECTION_CHARS = 2500


def language_of(path: str) -> tuple[str | None, str]:
    """Return (language, category) where category is code | doc | config | ''."""
    name = os.path.basename(path)
    if name in CONFIG_NAMES:
        return CONFIG_NAMES[name], "config"
    ext = os.path.splitext(name)[1].lower()
    if ext in CODE_EXT:
        return CODE_EXT[ext], "code"
    if ext in DOC_EXT:
        return DOC_EXT[ext], "doc"
    if ext in CONFIG_EXT:
        return CONFIG_EXT[ext], "config"
    return None, ""


def iter_files(root: Path, max_bytes: int):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            rel = full.relative_to(root).as_posix()
            lang, cat = language_of(rel)
            if not lang or SKIP_FILES.search(fn):
                continue
            try:
                if full.stat().st_size > max_bytes:
                    continue
                raw = full.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:4096]:
                continue  # binary
            yield rel, lang, cat, raw.decode("utf-8", errors="replace")


def _doc_sections(repo: str, path: str, lang: str, text: str) -> list[Symbol]:
    """Split markdown-ish docs on headings; long sections are windowed."""
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if re.match(r"^#{1,4}\s", l)] if lang == "markdown" else []
    if not starts or starts[0] != 0:
        starts = [0] + starts
    out = []
    for n, s in enumerate(starts):
        e = (starts[n + 1] if n + 1 < len(starts) else len(lines)) - 1
        body = "\n".join(lines[s: e + 1]).strip()
        if not body:
            continue
        title = lines[s].lstrip("# ").strip() if lines[s].startswith("#") else os.path.basename(path)
        out.append(Symbol(
            id=symbol_id(repo, path, f"doc:{title}", s + 1), repo=repo, path=path, name=title,
            qualname=f"{os.path.basename(path)} § {title}", kind="doc", language=lang,
            start_line=s + 1, end_line=e + 1, code=body[:DOC_SECTION_CHARS], parent=file_id(repo, path),
        ))
    return out


def _file_summary(repo: str, path: str, lang: str, text: str, info: FileInfo) -> Symbol:
    lines = text.splitlines()
    top_level = [s.qualname for s in info.symbols if s.kind in ("class", "function")][:40]
    head = "\n".join(lines[:FILE_SUMMARY_LINES])
    doc = f"Defines: {', '.join(top_level)}" if top_level else ""
    return Symbol(
        id=file_id(repo, path), repo=repo, path=path, name=os.path.basename(path), qualname=path,
        kind="file", language=lang, start_line=1, end_line=max(len(lines), 1), docstring=doc, code=head,
    )


def parse_repo(repo: RepoInfo, max_bytes: int) -> list[tuple[FileInfo, Symbol]]:
    """Return (FileInfo, file-summary Symbol) for every indexed file."""
    results = []
    for rel, lang, cat, text in iter_files(Path(repo.local_path), max_bytes):
        if cat == "code":
            info = parse_python(repo.name, rel, text) if lang == "python" else parse_generic(repo.name, rel, text, lang)
        else:
            info = FileInfo(repo=repo.name, path=rel, language=lang)
            if cat == "doc":
                info.symbols = _doc_sections(repo.name, rel, lang, text)
        summary = _file_summary(repo.name, rel, lang, text, info)
        results.append((info, summary))
    log.info("Parsed %s: %d files, %d symbols", repo.name, len(results), sum(len(i.symbols) for i, _ in results))
    return results
