"""Tests for the GitHub client, analytics and webhook receiver."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from git_repo_monitor.analytics import fetch_analytics, installs_for
from git_repo_monitor.github import GitHubClient, GitHubError, ci_summary, collect_remote, summarize_runs
from git_repo_monitor.webhook import classify, make_app, verify_signature

Routes = dict[str, Any]


@pytest.fixture
async def api() -> AsyncIterator[Callable[[Routes], Any]]:
    """Serve canned JSON answers; a route value may be (status, body) or a body."""
    servers: list[TestServer] = []
    sessions: list[aiohttp.ClientSession] = []

    async def start(routes: Routes) -> tuple[GitHubClient, TestServer]:
        async def handler(request: web.Request) -> web.Response:
            key = f"{request.method} {request.path_qs}"
            if key not in routes:
                return web.Response(status=404)
            value = routes[key]
            status, body = value if isinstance(value, tuple) else (200, value)
            etag = f'"{hashlib.md5(json.dumps(body).encode()).hexdigest()}"'  # noqa: S324
            headers = {"ETag": etag, "X-RateLimit-Remaining": "4999", "X-RateLimit-Limit": "5000"}
            if request.headers.get("If-None-Match") == etag:
                return web.Response(status=304, headers=headers)
            return web.json_response(body, status=status, headers=headers)

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        session = aiohttp.ClientSession()
        sessions.append(session)
        return GitHubClient(session, "token", str(server.make_url(""))), server

    yield start
    for session in sessions:
        await session.close()
    for server in servers:
        await server.close()


async def test_get_etag_and_errors(api: Callable[[Routes], Any]) -> None:
    client, server = await api({"GET /a": {"x": 1}, "GET /boom": (500, {})})
    assert await client.get("/a") == ({"x": 1}, True)
    assert await client.get("/a") == ({"x": 1}, False)
    assert client.rate_remaining == 4999
    assert client.rate_limit == 5000
    assert await client.get(str(server.make_url("/missing"))) == (None, True)
    with pytest.raises(GitHubError):
        await client.get("/boom")


async def test_get_without_etag_and_poll_interval() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.json_response([], headers={"X-Poll-Interval": "90"})

    app = web.Application()
    app.router.add_get("/users/me/events", handler)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = GitHubClient(session, None, str(server.make_url("")))
        assert await client.user_events("me") == ([], True)
        assert await client.user_events("me") == ([], True)
        assert client.poll_interval == 90
        assert client.rate_remaining is None


async def test_login_events_and_rerun(api: Callable[[Routes], Any]) -> None:
    client, _ = await api(
        {
            "GET /user": {"login": "elwin"},
            "GET /users/elwin/events?per_page=50": {"not": "a list"},
            "POST /repos/o/r/actions/runs/1/rerun-failed-jobs": (201, {}),
            "POST /repos/o/r/actions/runs/2/rerun-failed-jobs": (403, {}),
        }
    )
    assert await client.login() == "elwin"
    assert await client.user_events("elwin") == ([], True)
    assert await client.rerun_failed_jobs("o/r", 1) is True
    assert await client.rerun_failed_jobs("o/r", 2) is False


async def test_login_missing(api: Callable[[Routes], Any]) -> None:
    client, _ = await api({})
    assert await client.login() is None


def test_summarize_runs() -> None:
    runs = [
        {"name": "Tests", "conclusion": "failure", "status": "completed", "id": 3, "head_branch": "main"},
        {"name": "Tests", "conclusion": "success", "status": "completed", "id": 2},
        {"display_title": "Untitled", "conclusion": None, "status": "in_progress", "id": 4},
        {"name": "Lint"},
        {},
    ]
    summary = summarize_runs(runs)
    assert [w["name"] for w in summary] == ["?", "Lint", "Tests", "Untitled"]
    assert summary[2]["state"] == "failure"
    assert summary[1]["state"] == "unknown"
    assert ci_summary(summary) == {"total": 4, "ok": 0, "failed": 1, "running": 1}


async def test_collect_remote_full(api: Callable[[Routes], Any]) -> None:
    client, _ = await api(
        {
            "GET /repos/o/r": {
                "default_branch": "main",
                "stargazers_count": 12,
                "forks_count": 2,
                "open_issues_count": 3,
                "html_url": "https://github.com/o/r",
            },
            "GET /repos/o/r/releases/latest": {"tag_name": "v1.0", "published_at": "2026-01-01", "html_url": "u"},
            "GET /repos/o/r/compare/v1.0...main": {"ahead_by": 4},
            "GET /repos/o/r/actions/runs?per_page=30": {
                "workflow_runs": [{"name": "CI", "conclusion": "success", "id": 9}]
            },
            "GET /repos/o/r/pulls?state=open&per_page=100": [
                {"user": {"login": "dependabot[bot]"}},
                {"user": None},
            ],
        }
    )
    remote = await collect_remote(client, "o/r", {"stars_baseline": 10})
    assert remote["available"] is True
    assert remote["stars"] == 12
    assert remote["stars_baseline"] == 10
    assert remote["open_issues"] == 1
    assert (remote["open_prs"], remote["dependabot_prs"]) == (2, 1)
    assert remote["latest_release"]["tag"] == "v1.0"
    assert remote["unreleased_commits"] == 4
    assert remote["ci"] == {"total": 1, "ok": 1, "failed": 0, "running": 0}


async def test_collect_remote_minimal(api: Callable[[Routes], Any]) -> None:
    client, _ = await api(
        {
            "GET /repos/o/r": {"default_branch": None},
            "GET /repos/o/r/releases/latest": {"tag_name": "v1"},
        }
    )
    remote = await collect_remote(client, "o/r", None)
    assert remote["default_branch"] == "main"
    assert remote["unreleased_commits"] is None
    assert remote["workflows"] == []
    assert remote["open_prs"] == 0
    assert remote["stars_baseline"] == 0


async def test_collect_remote_missing(api: Callable[[Routes], Any]) -> None:
    client, _ = await api({})
    assert (await collect_remote(client, "o/r", None))["available"] is False


async def test_collect_remote_no_release(api: Callable[[Routes], Any]) -> None:
    client, _ = await api({"GET /repos/o/r": {"stargazers_count": 1}})
    remote = await collect_remote(client, "o/r", None)
    assert remote["latest_release"] is None


# ---------------------------------------------------------------- analytics


async def analytics_server(handler: Callable[[web.Request], Any]) -> TestServer:
    app = web.Application()
    app.router.add_get("/stats.json", handler)
    server = TestServer(app)
    await server.start_server()
    return server


async def test_fetch_analytics() -> None:
    async def ok(_: web.Request) -> web.Response:
        return web.json_response({"redsea": {"total": 5}})

    async def bad(_: web.Request) -> web.Response:
        return web.Response(status=503)

    async def not_dict(_: web.Request) -> web.Response:
        return web.json_response([1])

    async def invalid(_: web.Request) -> web.Response:
        return web.Response(text="{nope", content_type="application/json")

    async with aiohttp.ClientSession() as session:
        for handler, expected in ((ok, {"redsea": {"total": 5}}), (bad, None), (not_dict, None), (invalid, None)):
            server = await analytics_server(handler)
            assert await fetch_analytics(session, str(server.make_url("/stats.json"))) == expected
            await server.close()
        assert await fetch_analytics(session, "http://127.0.0.1:1/none.json") is None


def test_installs_for() -> None:
    data = {"redsea": {"total": 7, "versions": {"1.0": 2, "1.1": 5}}, "odd": [1], "bare": {}}
    assert installs_for(data, "redsea") == {"total": 7, "versions": {"1.1": 5, "1.0": 2}}
    assert installs_for(data, "bare") == {"total": 0, "versions": {}}
    assert installs_for(data, "odd") is None
    assert installs_for(data, "unknown") is None
    assert installs_for(None, "redsea") is None
    assert installs_for(data, None) is None


# ---------------------------------------------------------------- webhook


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature_and_classify() -> None:
    assert verify_signature("s", b"x", sign("s", b"x"))
    assert not verify_signature("s", b"x", sign("t", b"x"))
    assert not verify_signature("s", b"x", None)
    assert not verify_signature("s", b"x", "sha1=abc")
    assert classify("push") == "hot"
    assert classify("star") == "refresh"
    assert classify("member") is None


async def test_webhook_app() -> None:
    received: list[tuple[str, str]] = []

    async def callback(full_name: str, action: str) -> None:
        received.append((full_name, action))

    app = make_app("secret", "/webhook", callback)
    async with TestClient(TestServer(app)) as client:

        async def post(event: str, body: bytes, signature: str | None = None) -> aiohttp.ClientResponse:
            headers = {"X-GitHub-Event": event, "X-Hub-Signature-256": signature or sign("secret", body)}
            return await client.post("/webhook", data=body, headers=headers)

        assert (await post("push", b"{}", "sha256=bad")).status == 401
        assert await (await post("ping", b"{}")).text() == "pong"
        assert (await post("push", b"{nope")).status == 400
        assert await (await post("push", b"[]")).text() == "ignored"
        assert await (await post("member", b'{"repository": {"full_name": "o/r"}}')).text() == "ignored"
        assert await (await post("push", b"")).text() == "ignored"
        response = await post("workflow_run", b'{"repository": {"full_name": "o/r"}}')
        assert response.status == 202
        assert received == [("o/r", "hot")]
        assert await (await client.get("/health")).text() == "ok"
