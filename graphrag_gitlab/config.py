"""Runtime configuration, read from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_list(name: str) -> list[str]:
    raw = _env(name, "") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- GitLab -----------------------------------------------------------
    gitlab_url: str = field(default_factory=lambda: _env("GITLAB_URL", ""))
    gitlab_token: str | None = field(default_factory=lambda: _env("GITLAB_TOKEN"))
    gitlab_group: str = field(default_factory=lambda: _env("GITLAB_GROUP", "MS-ClientServices"))
    # Explicit project list (path_with_namespace or project name). Empty = pick from the group.
    gitlab_projects: list[str] = field(default_factory=lambda: _env_list("GITLAB_PROJECTS"))
    gitlab_max_projects: int = field(default_factory=lambda: int(_env("GITLAB_MAX_PROJECTS", "5")))
    gitlab_ref: str | None = field(default_factory=lambda: _env("GITLAB_REF"))  # default branch if unset
    gitlab_verify_ssl: bool = field(default_factory=lambda: _env_bool("GITLAB_VERIFY_SSL", True))

    # --- Storage ----------------------------------------------------------
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")).resolve())

    # --- Parsing limits ---------------------------------------------------
    max_file_bytes: int = field(default_factory=lambda: int(_env("MAX_FILE_BYTES", "400000")))

    # --- Retrieval --------------------------------------------------------
    # "sentence-transformers" (semantic, runs locally on CPU) or "hashing" (no ML deps, lexical only)
    embedding_backend: str = field(default_factory=lambda: _env("EMBEDDING_BACKEND", "sentence-transformers"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    top_k: int = field(default_factory=lambda: int(_env("TOP_K", "6")))
    # Kept small for CPU-only laptops: every extra snippet adds seconds of prompt processing.
    max_context_chunks: int = field(default_factory=lambda: int(_env("MAX_CONTEXT_CHUNKS", "6")))
    max_snippet_lines: int = field(default_factory=lambda: int(_env("MAX_SNIPPET_LINES", "40")))

    # --- Local LLM (Ollama) -----------------------------------------------
    use_llm: bool = field(default_factory=lambda: _env_bool("USE_LLM", True))
    ollama_host: str = field(default_factory=lambda: _env("OLLAMA_HOST_URL", "http://127.0.0.1:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "qwen2.5-coder:7b"))
    num_ctx: int = field(default_factory=lambda: int(_env("NUM_CTX", "8192")))
    temperature: float = field(default_factory=lambda: float(_env("TEMPERATURE", "0.1")))
    ollama_keep_alive: str = field(default_factory=lambda: _env("OLLAMA_KEEP_ALIVE", "30m"))
    # Thinking models (qwen3, gpt-oss): "false" makes them answer directly (much faster on CPU).
    # Leave empty for non-thinking models such as qwen2.5-coder.
    ollama_think: bool | None = field(default_factory=lambda: None if _env("OLLAMA_THINK") is None
                                      else _env_bool("OLLAMA_THINK", False))
    # Use the LLM to plan queries (better recall, one extra LLM call). Off by default on CPU: rule-based planner.
    use_llm_planner: bool = field(default_factory=lambda: _env_bool("USE_LLM_PLANNER", False))

    # --- Privacy ----------------------------------------------------------
    # Extra hostnames treated as internal (e.g. an internal GPU server running Ollama)
    allowed_hosts: list[str] = field(default_factory=lambda: _env_list("ALLOWED_HOSTS"))
    require_internal_gitlab: bool = field(default_factory=lambda: _env_bool("REQUIRE_INTERNAL_GITLAB", False))

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"
