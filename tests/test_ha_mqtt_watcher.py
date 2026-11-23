"""Tests for HA payloads/discovery, MQTT helpers and the git dir watcher."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import aiomqtt
import pytest
from watchfiles import Change

from conftest import FakeMqttClient, git
from git_repo_monitor.config import MqttConfig
from git_repo_monitor.ha import (
    Topics,
    bridge_discovery,
    build_payload,
    compute_status,
    current_version,
    repo_discovery,
)
from git_repo_monitor.mqtt import Publisher, make_client
from git_repo_monitor.watcher import owner_of, relevant, watch_git_dirs

TOPICS = Topics("git_repo_monitor/", "alare", "homeassistant/")


def test_topics() -> None:
    assert TOPICS.availability == "git_repo_monitor/alare/availability"
    assert TOPICS.bridge_state == "git_repo_monitor/alare/bridge"
    assert TOPICS.bridge_cmd == "git_repo_monitor/alare/cmd"
    assert TOPICS.repo_cmd_wildcard == "git_repo_monitor/alare/+/cmd"
    assert TOPICS.ha_status == "homeassistant/status"
    assert TOPICS.repo_state("r") == "git_repo_monitor/alare/r/state"
    assert TOPICS.discovery_topic("sensor", "r_status") == "homeassistant/sensor/grm_alare/r_status/config"
    assert TOPICS.slug_from_cmd("git_repo_monitor/alare/r/cmd") == "r"
    assert TOPICS.slug_from_cmd("git_repo_monitor/alare/cmd") is None
    assert TOPICS.slug_from_cmd("git_repo_monitor/alare/a/b/cmd") is None
    assert TOPICS.slug_from_cmd("other/alare/r/cmd") is None


@pytest.mark.parametrize(
    ("local", "remote", "status"),
    [
        (None, None, "unknown"),
        ({"upstream": "o/m"}, {"ci": {"failed": 1}}, "ci_failed"),
        ({"upstream": "o/m"}, {"ci": {"running": 1}}, "ci_running"),
        ({"upstream": "o/m", "dirty": True}, None, "needs_sync"),
        ({"upstream": "o/m", "behind": 2}, {}, "needs_sync"),
        ({"upstream": None}, None, "no_upstream"),
        ({"upstream": "o/m"}, {"ci": None}, "ok"),
    ],
)
def test_compute_status(local: dict | None, remote: dict | None, status: str) -> None:
    assert compute_status(local, remote) == status


def test_current_version() -> None:
    assert current_version(None) is None
    assert current_version({"tag": "v1"}) == "v1"
    assert current_version({"tag": "v1", "manifest_version": "1.1"}) == "1.1"


def payload(**overrides: object) -> dict:
    base = {
        "host": "alare",
        "slug": "r",
        "name": "R",
        "path": "/srv/r",
        "full_name": "o/r",
        "local": {"upstream": "o/m", "coverage": 99.0, "manifest_version": "1.0"},
        "remote": {"available": True, "stars": 5, "stars_baseline": 3, "ci": {"failed": 0}, "html_url": "h"},
        "installs": {"total": 3, "versions": {}},
        "cmd_topic": "c",
        "updated_at": "now",
    }
    base.update(overrides)
    return build_payload(**base)  # type: ignore[arg-type]


def test_build_payload() -> None:
    full = payload()
    assert full["stars_delta"] == 2
    assert "stars_baseline" not in full["remote"]
    assert full["html_url"] == "h"
    bare = payload(remote=None, full_name=None, local=None, installs=None)
    assert bare["html_url"] is None
    assert bare["stars_delta"] is None
    assert bare["ci"]["total"] == 0
    assert payload(remote={}, full_name="o/r")["html_url"] == "https://github.com/o/r"


def test_repo_discovery_full() -> None:
    discovery = repo_discovery(TOPICS, payload())
    keys = sorted(topic.split("/")[3] for topic in discovery)
    assert keys == [
        "r_active_installs",
        "r_ci_problem",
        "r_coverage",
        "r_fetch",
        "r_needs_sync",
        "r_refresh",
        "r_rerun_failed",
        "r_stars",
        "r_status",
        "r_version",
    ]
    status = discovery["homeassistant/sensor/grm_alare/r_status/config"]
    assert status["json_attributes_topic"] == TOPICS.repo_state("r")
    assert status["device"]["sw_version"] == "1.0"
    assert status["device"]["configuration_url"] == "h"
    assert status["default_entity_id"] == "sensor.git_r_status"
    button = discovery["homeassistant/button/grm_alare/r_fetch/config"]
    assert button["command_topic"] == TOPICS.repo_cmd("r")
    assert "state_topic" not in button


def test_repo_discovery_minimal() -> None:
    discovery = repo_discovery(TOPICS, payload(remote=None, full_name=None, local={}, installs=None))
    keys = sorted(topic.split("/")[3] for topic in discovery)
    assert keys == ["r_ci_problem", "r_fetch", "r_needs_sync", "r_refresh", "r_status", "r_version"]
    device = discovery["homeassistant/sensor/grm_alare/r_status/config"]["device"]
    assert "sw_version" not in device
    assert "configuration_url" not in device


def test_bridge_discovery() -> None:
    discovery = bridge_discovery(TOPICS, "Alare")
    assert len(discovery) == 3
    assert all(c["availability_topic"] == TOPICS.availability for c in discovery.values())


async def test_make_client() -> None:
    assert isinstance(make_client(MqttConfig(), "t/avail", "id"), aiomqtt.Client)
    assert isinstance(make_client(MqttConfig(tls=True), "t/avail", "id"), aiomqtt.Client)


async def test_publisher() -> None:
    publisher = Publisher()
    assert publisher.connected is False
    assert await publisher.publish("t", "x") is False
    client = FakeMqttClient()
    publisher.attach(client)  # type: ignore[arg-type]
    assert await publisher.publish("t", {"a": 1}) is True
    assert await publisher.publish("t", "plain", retain=False) is True
    assert client.published == [("t", '{"a":1}', True), ("t", "plain", False)]
    client.fail_publish = True
    assert await publisher.publish("t", "x") is False
    publisher.detach()
    assert publisher.connected is False


def test_relevant_and_owner(tmp_path: Path) -> None:
    git_dir = tmp_path / "r" / ".git"
    keep = relevant([git_dir])
    assert keep(Change.modified, str(git_dir / "HEAD"))
    assert not keep(Change.modified, str(git_dir / "config"))
    assert not keep(Change.modified, str(git_dir / "index.lock"))
    assert keep(Change.added, str(git_dir / "refs" / "heads" / "main"))
    assert not keep(Change.added, str(git_dir / "objects" / "ab" / "cd"))

    dirs = {"/a/.git": "a", "/a/.git/modules/sub": "sub"}
    assert owner_of("/a/.git/HEAD", dirs) == "a"
    assert owner_of("/a/.git/modules/sub/HEAD", dirs) == "sub"
    assert owner_of("/a/.git", dirs) == "a"
    assert owner_of("/b/.git/HEAD", dirs) is None


async def test_watch_git_dirs(make_repo: Callable[..., Path], tmp_path: Path) -> None:
    repo = make_repo("w")
    changed: list[str] = []
    stop = asyncio.Event()
    dirs = {str(repo / ".git"): "w", str(tmp_path / "ghost" / ".git"): "ghost"}
    task = asyncio.create_task(watch_git_dirs(dirs, changed.append, stop))
    await asyncio.sleep(0.3)
    (repo / "more.txt").write_text("x")
    git(repo, "add", "more.txt")
    git(repo, "commit", "-q", "-m", "more")
    for _ in range(50):
        if changed:
            break
        await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert "w" in changed


async def test_watch_nothing(tmp_path: Path) -> None:
    await watch_git_dirs({str(tmp_path / "none"): "x"}, lambda _: None, asyncio.Event())


async def test_watch_unknown_owner(make_repo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from git_repo_monitor import watcher

    repo = make_repo("u")
    seen: list[str] = []
    stop = asyncio.Event()

    def nobody(path: str, dirs: dict[str, str]) -> None:
        stop.set()

    monkeypatch.setattr(watcher, "owner_of", nobody)
    task = asyncio.create_task(watch_git_dirs({str(repo / ".git"): "u"}, seen.append, stop))
    await asyncio.sleep(0.3)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    await asyncio.wait_for(task, 10)
    assert seen == []
