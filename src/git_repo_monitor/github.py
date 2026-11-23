"""GitHub REST API client with ETag caching, and remote state collection."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from .util import utcnow_iso

_LOGGER = logging.getLogger(__name__)

OK_CONCLUSIONS = {"success", "skipped", "neutral"}
FAILED_CONCLUSIONS = {"failure", "cancelled", "startup_failure", "timed_out"}
RUNNING_STATUSES = {"queued", "in_progress", "waiting", "requested", "pending"}
DEPENDABOT_LOGIN = "dependabot[bot]"


class GitHubError(Exception):
    """Raised on unexpected GitHub API answers."""


class GitHubClient:
    """Minimal async GitHub client.

    Conditional requests (If-None-Match) answered with 304 do not count against the
    rate limit, so polling unchanged resources is free.
    """

    def __init__(self, session: aiohttp.ClientSession, token: str | None, api_url: str) -> None:
        self._session = session
        self._api = api_url.rstrip("/")
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ha-git-repo-monitor",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._cache: dict[str, tuple[str, Any]] = {}
        self.rate_remaining: int | None = None
        self.rate_limit: int | None = None
        self.poll_interval: int | None = None

    def _track(self, response: aiohttp.ClientResponse) -> None:
        if "X-RateLimit-Remaining" in response.headers:
            self.rate_remaining = int(response.headers["X-RateLimit-Remaining"])
            self.rate_limit = int(response.headers.get("X-RateLimit-Limit", 0)) or None
        if "X-Poll-Interval" in response.headers:
            self.poll_interval = int(response.headers["X-Poll-Interval"])

    async def get(self, path: str) -> tuple[Any, bool]:
        """GET a resource; return (data, changed). data is None on 404."""
        url = path if path.startswith("http") else f"{self._api}{path}"
        headers = dict(self._headers)
        cached = self._cache.get(url)
        if cached:
            headers["If-None-Match"] = cached[0]
        async with self._session.get(url, headers=headers) as response:
            self._track(response)
            if response.status == 304 and cached:
                return cached[1], False
            if response.status == 404:
                self._cache.pop(url, None)
                return None, True
            if response.status >= 400:
                raise GitHubError(f"GET {url}: HTTP {response.status}")
            data = await response.json(content_type=None)
            etag = response.headers.get("ETag")
            if etag:
                self._cache[url] = (etag, data)
            return data, True

    async def post(self, path: str) -> int:
        async with self._session.post(f"{self._api}{path}", headers=self._headers) as response:
            self._track(response)
            return response.status

    async def login(self) -> str | None:
        data, _ = await self.get("/user")
        return data.get("login") if isinstance(data, dict) else None

    async def user_events(self, login: str) -> tuple[list[dict[str, Any]], bool]:
        data, changed = await self.get(f"/users/{quote(login)}/events?per_page=50")
        return (data if isinstance(data, list) else []), changed

    async def rerun_failed_jobs(self, full_name: str, run_id: int) -> bool:
        status = await self.post(f"/repos/{full_name}/actions/runs/{run_id}/rerun-failed-jobs")
        if status >= 400:
            _LOGGER.warning("Re-run of %s run %s refused: HTTP %s", full_name, run_id, status)
        return status < 400


def summarize_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the latest run of each workflow (the API lists newest first)."""
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        name = run.get("name") or run.get("display_title") or "?"
        if name in latest:
            continue
        state = run.get("conclusion") or run.get("status") or "unknown"
        latest[name] = {
            "name": name,
            "state": state,
            "branch": run.get("head_branch"),
            "event": run.get("event"),
            "run_id": run.get("id"),
            "url": run.get("html_url"),
            "updated_at": run.get("updated_at"),
        }
    return sorted(latest.values(), key=lambda item: item["name"].lower())


def ci_summary(workflows: list[dict[str, Any]]) -> dict[str, int]:
    ok = sum(1 for w in workflows if w["state"] in OK_CONCLUSIONS)
    failed = sum(1 for w in workflows if w["state"] in FAILED_CONCLUSIONS)
    running = sum(1 for w in workflows if w["state"] in RUNNING_STATUSES)
    return {"total": len(workflows), "ok": ok, "failed": failed, "running": running}


async def collect_remote(client: GitHubClient, full_name: str, previous: dict[str, Any] | None) -> dict[str, Any]:
    """Collect stars, release, CI and pull requests of a repository."""
    repo, _ = await client.get(f"/repos/{full_name}")
    if repo is None:
        return {"available": False, "updated_at": utcnow_iso()}

    default_branch = repo.get("default_branch") or "main"
    release, _ = await client.get(f"/repos/{full_name}/releases/latest")
    latest_release = None
    unreleased = None
    if release:
        tag = release.get("tag_name")
        latest_release = {"tag": tag, "published_at": release.get("published_at"), "url": release.get("html_url")}
        compare, _ = await client.get(f"/repos/{full_name}/compare/{quote(tag, safe='')}...{quote(default_branch)}")
        unreleased = compare.get("ahead_by") if compare else None

    runs, _ = await client.get(f"/repos/{full_name}/actions/runs?per_page=30")
    workflows = summarize_runs((runs or {}).get("workflow_runs", []))

    pulls, _ = await client.get(f"/repos/{full_name}/pulls?state=open&per_page=100")
    pulls = pulls or []
    open_prs = len(pulls)
    dependabot = sum(1 for p in pulls if (p.get("user") or {}).get("login") == DEPENDABOT_LOGIN)

    stars = repo.get("stargazers_count", 0)
    stars_baseline = (previous or {}).get("stars_baseline")
    if stars_baseline is None:
        stars_baseline = stars

    return {
        "available": True,
        "html_url": repo.get("html_url"),
        "private": repo.get("private", False),
        "archived": repo.get("archived", False),
        "default_branch": default_branch,
        "stars": stars,
        "stars_baseline": stars_baseline,
        "forks": repo.get("forks_count", 0),
        # GitHub counts pull requests as issues.
        "open_issues": max(repo.get("open_issues_count", 0) - open_prs, 0),
        "open_prs": open_prs,
        "dependabot_prs": dependabot,
        "pushed_at": repo.get("pushed_at"),
        "latest_release": latest_release,
        "unreleased_commits": unreleased,
        "workflows": workflows,
        "ci": ci_summary(workflows),
        "updated_at": utcnow_iso(),
    }
