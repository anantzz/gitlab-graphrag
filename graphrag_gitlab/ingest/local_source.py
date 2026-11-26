"""Use already-cloned repositories on disk (handy for offline demos and tests)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..models import RepoInfo


def _git(path: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=10)
        return (out.stdout.strip() or None) if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _remote_to_web_url(remote: str | None) -> str | None:
    if not remote:
        return None
    remote = re.sub(r"\.git$", "", remote)
    m = re.match(r"git@([^:]+):(.+)", remote)  # git@gitlab.example.com:group/repo
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    m = re.match(r"https?://(?:[^@/]+@)?(.+)", remote)  # strip embedded credentials
    return f"https://{m.group(1)}" if m else None


def discover(paths: list[str]) -> list[RepoInfo]:
    repos = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            raise SystemExit(f"Not a directory: {path}")
        web_url = _remote_to_web_url(_git(path, "remote", "get-url", "origin"))
        full_path = web_url.split("/", 3)[3] if web_url and web_url.count("/") >= 3 else path.name
        repos.append(
            RepoInfo(
                name=path.name,
                full_path=full_path,
                local_path=str(path),
                web_url=web_url,
                ref=_git(path, "rev-parse", "HEAD"),
            )
        )
    return repos
