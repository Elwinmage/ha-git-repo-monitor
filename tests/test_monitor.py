"""Tests for the orchestrator and the CLI."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiohttp
import aiomqtt
import pytest

from conftest import FakeMessage, FakeMqttClient, git
from git_repo_monitor import __main__ as cli
from git_repo_monitor import monitor as monitor_module
from git_repo_monitor.config import Config, ConfigError
from git_repo_monitor.github import GitHubError
from git_repo_monitor.monitor import Monitor


class FakeGitHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.events_changed = True
        self.reruns: list[tuple[str, int]] = []
        self.rerun_result: bool | Exception = True
        self.login_result: str | Exception = "elwin"
        self.poll_interval: int | None = None
        self.rate_remaining = 4000
        self.rate_limit = 5000

    async def login(self) -> str:
        if isinstance(self.login_result, Exception):
            raise self.login_result
        return self.login_result

    async def user_events(self, login: str) -> tuple[list[dict[str, Any]], bool]:
        return self.events, self.events_changed

    async def rerun_failed_jobs(self, full_name: str, run_id: int) -> bool:
        if isinstance(self.rerun_result, Exception):
            raise self.rerun_result
        self.reruns.append((full_name, run_id))
        return self.rerun_result


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def remote(running: int = 0, failed: int = 0, stars: int = 3) -> dict[str, Any]:
    workflows = [{"name": "CI", "state": "failure", "run_id": 7}] if failed else []
    workflows.append({"name": "Lint", "state": "failure", "run_id": None})
    return {
        "available": True,
        "stars": stars,
        "stars_baseline": stars,
        "workflows": workflows,
        "ci": {"total": 1, "ok": 0, "failed": failed, "running": running},
    }


@pytest.fixture
async def setup(config: Config, make_repo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch) -> Any:
    repo = make_repo("ha-alpha", upstream=True)
    git(repo, "remote", "set-url", "origin", "https://github.com/Elwinmage/ha-alpha.git")
    calls: dict[str, list[Any]] = {"remote": [], "fetch": [], "watch": []}
    remote_value: dict[str, Any] = {"value": remote()}

    async def fake_collect_remote(client: Any, full_name: str, previous: Any) -> dict[str, Any]:
        calls["remote"].append(full_name)
        value = remote_value["value"]
        if isinstance(value, Exception):
            raise value
        return dict(value)

    async def fake_fetch(info: Any, token: Any) -> bool:
        calls["fetch"].append(info.slug)
        return True

    async def fake_watch(git_dirs: Any, on_change: Any, stop: asyncio.Event) -> None:
        calls["watch"].append(dict(git_dirs))
        on_change("ha_alpha")
        await stop.wait()

    monkeypatch.setattr(monitor_module, "collect_remote", fake_collect_remote)
    monkeypatch.setattr(monitor_module, "git_fetch", fake_fetch)
    monkeypatch.setattr(monitor_module, "watch_git_dirs", fake_watch)

    client = FakeMqttClient()
    clock = Clock()
    async with aiohttp.ClientSession() as session:
        mon = Monitor(config, session, mqtt_factory=lambda: client, clock=clock)
        mon.github = FakeGitHub()  # type: ignore[assignment]
        mon.publisher.attach(client)  # type: ignore[arg-type]
        yield mon, client, calls, remote_value, clock, repo
        for task in list(mon._tasks):
            task.cancel()
        await asyncio.gather(*mon._tasks, return_exceptions=True)


async def drain(mon: Monitor) -> None:
    for _ in range(5):
        await asyncio.sleep(0.03)
        if mon._tasks:
            await asyncio.gather(*[t for t in mon._tasks if t is not mon._watch_task], return_exceptions=True)


def state_payloads(client: FakeMqttClient, slug: str = "ha_alpha") -> list[dict[str, Any]]:
    return [json.loads(p) for t, p, _ in client.published if t.endswith(f"/{slug}/state") and p]


async def test_rescan_add_and_remove(setup: Any, make_repo: Callable[..., Path], tmp_path: Path) -> None:
    mon, client, calls, *_ = setup
    assert await mon.rescan() is True
    await drain(mon)
    assert list(mon.repos) == ["ha_alpha"]
    assert calls["remote"] == ["Elwinmage/ha-alpha"]
    assert calls["watch"]
    assert state_payloads(client)[-1]["status"] == "ok"
    assert await mon.rescan() is False

    make_repo("second")
    assert await mon.rescan() is True
    assert mon.slugs_for("elwinmage/HA-ALPHA") == ["ha_alpha"]
    import shutil

    shutil.rmtree(tmp_path / "repos" / "second")
    client.published.clear()
    assert await mon.rescan() is True
    assert any(t.endswith("/second/state") and p == "" for t, p, _ in client.published)
    assert "second" not in mon.repos


async def test_refreshers_guard_missing(setup: Any) -> None:
    mon, *_ = setup
    await mon.refresh_local("nope")
    await mon.refresh_remote("nope")
    await mon.fetch("nope")
    await mon.rerun_failed("nope")
    mon.mark_hot("nope")
    await mon.publish_repo("nope")


async def test_refresh_remote_paths(setup: Any, caplog: pytest.LogCaptureFixture) -> None:
    mon, _, calls, remote_value, *_ = setup
    await mon.rescan()
    await drain(mon)
    remote_value["value"] = GitHubError("down")
    await mon.refresh_remote("ha_alpha")
    assert "Remote refresh of ha_alpha failed" in caplog.text

    mon.repos["ha_alpha"].info.full_name = None
    calls["remote"].clear()
    await mon.refresh_remote("ha_alpha")
    mon.github = None
    await mon.refresh_remote("ha_alpha")
    assert calls["remote"] == []


async def test_fetch_and_refresh(setup: Any) -> None:
    mon, _, calls, *_ = setup
    await mon.rescan()
    await mon.fetch("ha_alpha")
    await mon.refresh("ha_alpha")
    assert calls["fetch"] == ["ha_alpha"]


async def test_fetch_failure(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    mon, client, *_ = setup
    await mon.rescan()

    async def failing(*_: Any) -> bool:
        return False

    monkeypatch.setattr(monitor_module, "git_fetch", failing)
    client.published.clear()
    await mon.fetch("ha_alpha")
    assert client.published == []


async def test_rerun_failed(setup: Any, caplog: pytest.LogCaptureFixture) -> None:
    mon, _, _, remote_value, *_ = setup
    remote_value["value"] = remote(failed=1)
    await mon.rescan()
    await drain(mon)
    await mon.rerun_failed("ha_alpha")
    assert mon.github.reruns == [("Elwinmage/ha-alpha", 7)]
    assert mon.repos["ha_alpha"].hot_started is not None

    mon.repos["ha_alpha"].hot_started = None
    mon.github.rerun_result = False
    await mon.rerun_failed("ha_alpha")
    assert mon.repos["ha_alpha"].hot_started is None

    mon.github.rerun_result = GitHubError("nope")
    await mon.rerun_failed("ha_alpha")
    assert "Re-run on ha_alpha failed" in caplog.text

    mon.repos["ha_alpha"].remote = None
    await mon.rerun_failed("ha_alpha")


async def test_hot_mode(setup: Any) -> None:
    mon, _, calls, remote_value, clock, _ = setup
    await mon.rescan()
    await drain(mon)
    state = mon.repos["ha_alpha"]
    await mon.hot_tick()
    assert calls["fetch"] == []

    mon.mark_hot("ha_alpha")
    started = state.hot_started
    mon.mark_hot("ha_alpha")
    assert state.hot_started == started

    remote_value["value"] = remote(running=1)
    await mon.hot_tick()
    assert state.hot_started is not None
    clock.now += mon.config.github.hot_grace
    await mon.hot_tick()
    assert state.hot_started is not None  # still running
    clock.now += mon.config.github.hot_duration
    await mon.hot_tick()
    assert state.hot_started is None

    mon.config.repos.fetch = False
    mon.mark_hot("ha_alpha")
    remote_value["value"] = remote()
    clock.now += mon.config.github.hot_grace
    calls["fetch"].clear()
    await mon.hot_tick()
    assert state.hot_started is None
    assert calls["fetch"] == []


async def test_poll_events(setup: Any) -> None:
    mon, _, calls, *_ = setup
    await mon.rescan()
    await drain(mon)
    gh = mon.github

    mon.login = None
    await mon.poll_events_once()
    mon.login = "elwin"

    gh.events_changed = False
    await mon.poll_events_once()
    gh.events_changed = True
    gh.events = [{"id": "x"}]
    await mon.poll_events_once()
    assert mon.last_event_id is None

    gh.events = [{"id": "10", "type": "PushEvent", "repo": {"name": "Elwinmage/ha-alpha"}}]
    await mon.poll_events_once()
    assert mon.last_event_id == 10

    calls["remote"].clear()
    gh.events = [
        {"id": "13", "type": "PushEvent", "repo": {"name": "Elwinmage/ha-alpha"}},
        {"id": "12", "type": "WatchEvent", "repo": {"name": "Elwinmage/ha-alpha"}},
        {"id": "11", "type": "GollumEvent", "repo": {"name": "Elwinmage/ha-alpha"}},
        {"id": "14", "type": "PushEvent", "repo": {}},
        {"id": "nope"},
        {"id": "10", "type": "PushEvent", "repo": {"name": "Elwinmage/ha-alpha"}},
    ]
    await mon.poll_events_once()
    await drain(mon)
    assert mon.last_event_id == 14
    assert calls["remote"] == ["Elwinmage/ha-alpha", "Elwinmage/ha-alpha"]
    assert mon.repos["ha_alpha"].hot_started is not None

    mon.github = None
    await mon.poll_events_once()


async def test_events_interval(setup: Any) -> None:
    mon, *_ = setup
    assert mon.events_interval() == 60
    mon.github.poll_interval = 120
    assert mon.events_interval() == 120
    mon.github = None
    assert mon.events_interval() == 60


async def test_full_refresh_status_daily(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    mon, client, calls, *_ = setup
    repo_dir = setup[5]
    manifest = repo_dir / "custom_components" / "alpha"
    manifest.mkdir(parents=True)
    (manifest / "manifest.json").write_text('{"domain": "alpha", "version": "1.0"}')
    await mon.rescan()
    await drain(mon)

    await mon.full_refresh_once()
    assert calls["fetch"] == ["ha_alpha"]
    mon.config.repos.fetch = False
    await mon.full_refresh_once()
    assert calls["fetch"] == ["ha_alpha"]
    await mon.status_tick()

    answers: list[Any] = [{"alpha": {"total": 42}}, None]

    async def fake_analytics(session: Any, url: str) -> Any:
        return answers.pop(0)

    monkeypatch.setattr(monitor_module, "fetch_analytics", fake_analytics)
    mon.repos["ha_alpha"].remote["stars"] = 9
    await mon.daily_once()
    assert mon.repos["ha_alpha"].remote["stars_baseline"] == 9
    assert state_payloads(client)[-1]["installs"]["total"] == 42
    await mon.daily_once()
    assert mon.analytics == {"alpha": {"total": 42}}
    mon.config.analytics.enabled = False
    mon.repos["ha_alpha"].remote = None
    await mon.daily_once()


async def test_publish_repo_paths(setup: Any) -> None:
    mon, client, _, remote_value, *_ = setup
    await mon.rescan()
    await drain(mon)
    client.published.clear()
    await mon.publish_repo("ha_alpha")
    assert client.published == []  # unchanged

    remote_value["value"] = {"available": False}
    await mon.refresh_remote("ha_alpha")
    removed = [t for t, p, _ in client.published if t.endswith("_stars/config") and p == ""]
    assert removed

    mon.publisher.detach()
    client.published.clear()
    mon.repos["ha_alpha"].local["dirty"] = True
    await mon.publish_repo("ha_alpha")
    assert client.published == []


async def test_bridge_without_github(setup: Any) -> None:
    mon, client, *_ = setup
    mon.github = None
    await mon.publish_bridge()
    assert json.loads(client.published[-1][1])["rate_remaining"] is None


async def test_handle_message(setup: Any) -> None:
    mon, client, calls, *_ = setup
    await mon.rescan()
    await drain(mon)
    topics = mon.topics

    client.published.clear()
    await mon.handle_message(topics.ha_status, b"offline")
    assert client.published == []
    await mon.handle_message(topics.ha_status, b"online")
    assert (topics.availability, "online", True) in client.published

    calls["remote"].clear()
    await mon.handle_message(topics.bridge_cmd, b"nope")
    await mon.handle_message(topics.bridge_cmd, b"refresh_all")
    await drain(mon)
    assert calls["remote"] == ["Elwinmage/ha-alpha"]

    await mon.handle_message(topics.repo_cmd("ha_alpha"), b"fetch")
    await mon.handle_message(topics.repo_cmd("ha_alpha"), b"explode")
    await mon.handle_message(topics.repo_cmd("ghost"), b"refresh")
    await mon.handle_message("random/topic", b"refresh")
    await drain(mon)
    assert calls["fetch"][-1] == "ha_alpha"


async def test_mqtt_session_and_loop(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    mon, client, *_ = setup
    await mon.rescan()
    client._messages = [FakeMessage(mon.topics.repo_cmd("ha_alpha"), b"refresh")]
    mon.publisher.detach()
    await mon.mqtt_session()
    assert client.subscribed == [mon.topics.ha_status, mon.topics.bridge_cmd, mon.topics.repo_cmd_wildcard]
    assert mon.publisher.connected is False

    attempts: list[int] = []

    def failing_factory() -> Any:
        attempts.append(1)
        raise aiomqtt.MqttError("refused")

    async def stop_sleep(_: float) -> None:
        raise asyncio.CancelledError

    mon._mqtt_factory = failing_factory
    monkeypatch.setattr(monitor_module.asyncio, "sleep", stop_sleep)
    with pytest.raises(asyncio.CancelledError):
        await mon.mqtt_loop()
    assert attempts == [1]


async def test_every(setup: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    mon, *_ = setup
    runs: list[int] = []
    sleeps = {"n": 0}

    async def job() -> None:
        runs.append(1)
        if len(runs) == 2:
            raise RuntimeError("bad")

    async def fake_sleep(_: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(monitor_module.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await mon.every(lambda: 1, job, "job")
    assert runs == [1, 1]
    assert "Periodic job job failed" in caplog.text


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_serve_webhook(setup: Any) -> None:
    mon, *_ = setup
    mon.config.webhook.host = "127.0.0.1"
    mon.config.webhook.port = free_port()
    task = asyncio.create_task(mon.serve_webhook())
    await asyncio.sleep(0.2)
    url = f"http://127.0.0.1:{mon.config.webhook.port}/health"
    async with aiohttp.ClientSession() as session, session.get(url) as response:
        assert await response.text() == "ok"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run(setup: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    mon, *_ = setup
    started: list[str] = []

    async def fake_every(interval: Callable[[], float], job: Any, name: str) -> None:
        interval()
        started.append(name)

    async def fake_loop() -> None:
        started.append("mqtt")

    async def fake_serve() -> None:
        started.append("webhook")

    async def fake_daily() -> None:
        started.append("daily-now")

    monkeypatch.setattr(mon, "every", fake_every)
    monkeypatch.setattr(mon, "mqtt_loop", fake_loop)
    monkeypatch.setattr(mon, "serve_webhook", fake_serve)
    monkeypatch.setattr(mon, "daily_once", fake_daily)
    mon.config.webhook.enabled = True
    mon.login = None
    mon.github.login_result = GitHubError("401")
    await mon.run()
    await drain(mon)
    assert "Cannot resolve the GitHub login" in caplog.text
    expected = {"mqtt", "rescan", "status", "daily", "events", "full refresh", "hot", "webhook", "daily-now"}
    assert set(started) == expected

    started.clear()
    mon.github.login_result = "elwin"
    await mon.run()
    assert mon.login == "elwin"

    started.clear()
    mon.github = None
    mon.config.webhook.enabled = False
    mon.config.analytics.enabled = False
    await mon.run()
    assert set(started) == {"mqtt", "rescan", "status", "daily"}
    assert "No GitHub token" in caplog.text


async def test_monitor_defaults(config: Config) -> None:
    config.github.token = "t"
    async with aiohttp.ClientSession() as session:
        mon = Monitor(config, session)
        assert mon.github is not None
        assert isinstance(mon._mqtt_factory(), aiomqtt.Client)


# ---------------------------------------------------------------- CLI


def test_parse_args() -> None:
    assert cli.parse_args(["-c", "x.yaml"]).config == "x.yaml"


def test_main_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert cli.main(["-c", str(tmp_path / "missing.yaml")]) == 2

    def bad(_: str) -> Any:
        raise ConfigError("broken")

    monkeypatch.setattr(cli, "load_config", bad)
    assert cli.main(["-c", "x"]) == 2


def test_main_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[Config] = []

    async def fake_run(self: Monitor) -> None:
        ran.append(self.config)

    monkeypatch.setattr(cli.Monitor, "run", fake_run)
    path = tmp_path / "c.yaml"
    path.write_text("log_level: debug\nrepos: {base_dirs: [/tmp]}\n")
    monkeypatch.setattr(cli.sys, "argv", ["git-repo-monitor", "-c", str(path)])
    assert cli.main() == 0
    assert ran[0].log_level == "debug"


def test_main_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def interrupted(_: str) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "amain", interrupted)
    assert cli.main(["-c", "x"]) == 0


async def test_rescan_without_github(setup: Any) -> None:
    mon, _, calls, *_ = setup
    mon.github = None
    await mon.rescan()
    await drain(mon)
    assert calls["remote"] == []
