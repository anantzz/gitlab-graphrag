"""Fetch a limited set of repositories from a GitLab group (e.g. MS-ClientServices).

Downloads a zip archive of each project at a pinned commit, so citations can be
permalinks (`/-/blob/<sha>/<path>#L10-20`) and no git credentials are needed.
"""
from __future__ import annotations

import io
import logging
import shutil
import zipfile
from pathlib import Path

from ..config import Settings
from ..models import RepoInfo

log = logging.getLogger(__name__)


def _select_projects(gl, settings: Settings):
    group = gl.groups.get(settings.gitlab_group)
    group_projects = list(
        group.projects.list(include_subgroups=True, archived=False, iterator=True, order_by="last_activity_at")
    )
    if settings.gitlab_projects:
        wanted = {p.lower() for p in settings.gitlab_projects}
        chosen = [
            gp for gp in group_projects
            if gp.path_with_namespace.lower() in wanted or gp.path.lower() in wanted or gp.name.lower() in wanted
        ]
        missing = wanted - {c.path_with_namespace.lower() for c in chosen} - {c.path.lower() for c in chosen} \
            - {c.name.lower() for c in chosen}
        if missing:
            log.warning("Projects not found in group %s: %s", settings.gitlab_group, ", ".join(sorted(missing)))
    else:
        chosen = group_projects[: settings.gitlab_max_projects]
    return chosen


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract, stripping the archive's top-level folder and refusing path traversal."""
    dest = dest.resolve()
    for member in zf.infolist():
        parts = Path(member.filename).parts
        if len(parts) <= 1 or member.is_dir():
            continue
        target = (dest / Path(*parts[1:])).resolve()
        if dest not in target.parents:
            log.warning("Skipping suspicious archive entry %s", member.filename)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def fetch_repos(settings: Settings) -> list[RepoInfo]:
    import gitlab  # python-gitlab

    if not settings.gitlab_url:
        raise SystemExit("GITLAB_URL is not set (your company GitLab, e.g. https://gitlab.company.internal).")
    if not settings.gitlab_token:
        raise SystemExit("GITLAB_TOKEN is not set (needs read_api + read_repository scopes).")

    gl = gitlab.Gitlab(settings.gitlab_url, private_token=settings.gitlab_token, ssl_verify=settings.gitlab_verify_ssl)
    gl.auth()

    repos: list[RepoInfo] = []
    for gp in _select_projects(gl, settings):
        project = gl.projects.get(gp.id)
        ref = settings.gitlab_ref or project.default_branch
        if not ref:
            log.warning("Skipping %s: empty repository", project.path_with_namespace)
            continue
        sha = project.commits.get(ref).id
        dest = settings.repos_dir / project.path_with_namespace.replace("/", "__")
        log.info("Downloading %s @ %s (%s)", project.path_with_namespace, ref, sha[:10])
        archive = project.repository_archive(sha=sha, format="zip")
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            _safe_extract(zf, dest)
        repos.append(
            RepoInfo(
                name=project.path,
                full_path=project.path_with_namespace,
                local_path=str(dest),
                web_url=project.web_url,
                ref=sha,
                description=project.description or "",
            )
        )
    return repos
