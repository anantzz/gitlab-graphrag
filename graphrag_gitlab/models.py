"""Core data structures shared by ingestion, parsing, graph and retrieval."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from urllib.parse import quote


@dataclass
class RepoInfo:
    name: str                      # short name, e.g. "order-service"
    full_path: str                 # path_with_namespace, e.g. "MS-ClientServices/order-service"
    local_path: str                # where the source lives on disk
    web_url: str | None = None     # e.g. https://gitlab.example.com/MS-ClientServices/order-service
    ref: str | None = None         # commit sha (preferred, permalinks) or branch
    description: str = ""

    def blob_url(self, path: str, start: int | None = None, end: int | None = None) -> str | None:
        if not self.web_url:
            return None
        url = f"{self.web_url.rstrip('/')}/-/blob/{self.ref or 'HEAD'}/{quote(path)}"
        if start:
            url += f"#L{start}" + (f"-{end}" if end and end != start else "")
        return url

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Symbol:
    """A retrievable unit of code: function, method, class, file summary or doc section."""

    id: str
    repo: str
    path: str
    name: str
    qualname: str
    kind: str                      # function | method | class | file | doc
    language: str
    start_line: int
    end_line: int
    signature: str = ""
    docstring: str = ""
    code: str = ""
    parent: str | None = None      # id of the enclosing class / file
    calls: list[str] = field(default_factory=list)   # raw callee names
    bases: list[str] = field(default_factory=list)   # raw base-class names

    def header(self) -> str:
        return f"{self.repo}/{self.path} :: {self.qualname} ({self.kind}, {self.language})"

    def index_text(self, max_chars: int = 2000) -> str:
        parts = [self.header()]
        if self.signature:
            parts.append(self.signature)
        if self.docstring:
            parts.append(self.docstring)
        parts.append(self.code[:max_chars])
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FileInfo:
    repo: str
    path: str
    language: str
    imports: list[str] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)


def file_id(repo: str, path: str) -> str:
    return f"file:{repo}:{path}"


def symbol_id(repo: str, path: str, qualname: str, line: int) -> str:
    return f"sym:{repo}:{path}:{qualname}:{line}"


def repo_id(repo: str) -> str:
    return f"repo:{repo}"
