"""Local git inspection: repository discovery and working copy state."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .util import slugify

_LOGGER = logging.getLogger(__name__)

GIT_TIMEOUT = 30
FETCH_TIMEOUT = 120

_REMOTE_RE = re.compile(
    r"^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<full>[^/]+/[^/]+?)(?:\.git)?/?$"
)


@dataclass
class RepoInfo:
    """A discovered working copy."""

    slug: str
    name: str
    path: Path
    git_dir: Path
    full_name: str | None = None
    remote_url: str | None = None


def parse_remote_url(url: str | None) -> str | None:
    """Return 'owner/repo' for a GitHub remote URL, None otherwise."""
    if not url:
        return None
    match = _REMOTE_RE.match(url.strip())
    return match.group("full") if match else None


def resolve_git_dir(repo: Path) -> Path | None:
    """Return the git directory of a working copy (handles '.git' files of worktrees)."""
    dot_git = repo / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        content = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        if content.startswith("gitdir:"):
            target = Path(content.split(":", 1)[1].strip())
            if not target.is_absolute():
                target = (repo / target).resolve()
            return target if target.is_dir() else None
    return None


def find_repos(base_dirs: list[str], depth: int, exclude: list[str]) -> list[tuple[Path, Path]]:
    """List (working copy, git dir) pairs below the base directories."""
    found: list[tuple[Path, Path]] = []
    seen: set[Path] = set()

    def walk(directory: Path, level: int) -> None:
        try:
            children = sorted(p for p in directory.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError as err:
            _LOGGER.warning("Cannot list %s: %s", directory, err)
            return
        for child in children:
            if any(fnmatch(child.name, pattern) for pattern in exclude):
                continue
            git_dir = resolve_git_dir(child)
            if git_dir is not None:
                real = child.resolve()
                if real not in seen:
                    seen.add(real)
                    found.append((child, git_dir))
            elif level < depth:
                walk(child, level + 1)

    for base in base_dirs:
        walk(Path(base).expanduser(), 1)
    return found


def assign_slugs(paths: list[tuple[Path, Path]]) -> list[RepoInfo]:
    """Build RepoInfo objects with unique slugs."""
    repos: list[RepoInfo] = []
    used: set[str] = set()
    for path, git_dir in paths:
        base = slugify(path.name)
        slug = base
        index = 2
        while slug in used:
            slug = f"{base}_{index}"
            index += 1
        used.add(slug)
        repos.append(RepoInfo(slug=slug, name=path.name, path=path, git_dir=git_dir))
    return repos


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"})
    return env


async def run_git(path: Path, *args: str, timeout: float = GIT_TIMEOUT) -> tuple[int, str]:
    """Run a git command in 'path' and return (exit code, stripped stdout)."""
    # --no-optional-locks keeps 'git status' from rewriting the index, which would
    # otherwise re-trigger our own file watcher.
    proc = await asyncio.create_subprocess_exec(
        "git",
        "--no-optional-locks",
        "-C",
        str(path),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_git_env(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        _LOGGER.warning("git %s timed out in %s", " ".join(args), path)
        return 124, ""
    if proc.returncode and stderr:
        _LOGGER.debug("git %s in %s: %s", " ".join(args), path, stderr.decode(errors="replace").strip())
    return proc.returncode or 0, stdout.decode(errors="replace").strip()


def auth_args(remote_url: str | None, token: str | None) -> list[str]:
    """Extra git options authenticating GitHub remotes with the API token.

    SSH remotes are rewritten to HTTPS for the command, so no SSH key is needed
    (handy in a container).
    """
    if not token or parse_remote_url(remote_url) is None:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return [
        "-c",
        "url.https://github.com/.insteadOf=git@github.com:",
        "-c",
        "url.https://github.com/.insteadOf=ssh://git@github.com/",
        "-c",
        f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}",
    ]


async def git_fetch(repo: RepoInfo, token: str | None) -> bool:
    """Fetch origin; return True on success."""
    code, _ = await run_git(
        repo.path, *auth_args(repo.remote_url, token), "fetch", "--quiet", "--prune", "origin", timeout=FETCH_TIMEOUT
    )
    if code:
        _LOGGER.warning("git fetch failed for %s (exit %s)", repo.name, code)
    return code == 0


def read_manifest(path: Path) -> dict[str, Any]:
    """Return version/domain from an HA integration manifest or a package.json."""
    for manifest in sorted(path.glob("custom_components/*/manifest.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return {"version": data.get("version"), "domain": data.get("domain"), "kind": "integration"}
    package = path / "package.json"
    if package.is_file():
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {"version": data.get("version"), "domain": None, "kind": "package"}
    return {"version": None, "domain": None, "kind": None}


def read_coverage(path: Path, candidates: list[str]) -> tuple[float | None, float | None]:
    """Return (coverage percent, report mtime) from a Cobertura XML report."""
    for rel in candidates:
        report = path / rel
        if not report.is_file():
            continue
        try:
            mtime = report.stat().st_mtime
            root = ET.parse(report).getroot()  # noqa: S314 - local report written by the user's own tooling
            rate = float(root.attrib["line-rate"])
        except (OSError, ET.ParseError, KeyError, ValueError):
            continue
        return round(rate * 100, 1), mtime
    return None, None


async def collect_local(repo: RepoInfo, coverage_paths: list[str]) -> dict[str, Any]:
    """Collect the local state of a working copy."""
    path = repo.path
    _, branch = await run_git(path, "rev-parse", "--abbrev-ref", "HEAD")
    code, upstream = await run_git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    upstream = upstream if code == 0 and upstream else None

    ahead = behind = 0
    if upstream:
        code, counts = await run_git(path, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
        parts = counts.split()
        if code == 0 and len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])

    _, porcelain = await run_git(path, "status", "--porcelain")
    dirty_count = len([line for line in porcelain.splitlines() if line.strip()])

    code, tag = await run_git(path, "describe", "--tags", "--abbrev=0")
    tag = tag if code == 0 and tag else None
    commits_since_tag = None
    if tag:
        code, count = await run_git(path, "rev-list", "--count", f"{tag}..HEAD")
        commits_since_tag = int(count) if code == 0 and count.isdigit() else None

    last_commit = None
    code, log = await run_git(path, "log", "-1", "--format=%H%x1f%s%x1f%cI")
    if code == 0 and log.count("\x1f") == 2:
        sha, subject, date = log.split("\x1f")
        last_commit = {"sha": sha[:10], "subject": subject, "date": date}

    code, remote_url = await run_git(path, "remote", "get-url", "origin")
    repo.remote_url = remote_url if code == 0 and remote_url else None
    repo.full_name = parse_remote_url(repo.remote_url)

    manifest = read_manifest(path)
    coverage, coverage_mtime = read_coverage(path, coverage_paths)

    return {
        "branch": branch or None,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "dirty": dirty_count > 0,
        "dirty_count": dirty_count,
        "tag": tag,
        "commits_since_tag": commits_since_tag,
        "last_commit": last_commit,
        "manifest_version": manifest["version"],
        "domain": manifest["domain"],
        "kind": manifest["kind"],
        "coverage": coverage,
        "coverage_mtime": coverage_mtime,
    }
