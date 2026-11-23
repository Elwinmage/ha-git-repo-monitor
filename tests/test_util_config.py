"""Tests for util and config."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from git_repo_monitor.config import ConfigError, load_config
from git_repo_monitor.util import Debouncer, slugify, utcnow_iso


def test_slugify() -> None:
    assert slugify("ha-Reef Card") == "ha_reef_card"
    assert slugify("---") == "unnamed"


def test_utcnow_iso() -> None:
    assert utcnow_iso().endswith("+00:00")


async def test_debouncer_coalesces() -> None:
    calls: list[str] = []

    async def callback(key: object) -> None:
        calls.append(str(key))

    debouncer = Debouncer(0.01, callback)
    debouncer.trigger("a")
    debouncer.trigger("a")
    debouncer.trigger("b")
    assert debouncer.pending == {"a", "b"}
    await asyncio.sleep(0.05)
    assert sorted(calls) == ["a", "b"]
    assert debouncer.pending == set()


async def test_debouncer_cancel() -> None:
    calls: list[object] = []

    async def callback(key: object) -> None:
        calls.append(key)

    debouncer = Debouncer(0.05, callback)
    debouncer.trigger("a")
    debouncer.cancel()
    await asyncio.sleep(0.1)
    assert calls == []


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_load_full_config(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
host_id: My Host
mqtt:
  host: broker
  port: 1884
github:
  login: someone
webhook:
  enabled: true
  secret: s3cret
  path: hook
repos:
  base_dirs: [/srv/git]
analytics:
""",
    )
    config = load_config(path, env={})
    assert config.host_slug == "my_host"
    assert config.mqtt.host == "broker"
    assert config.mqtt.port == 1884
    assert config.webhook.path == "/hook"
    assert config.repos.base_dirs == ["/srv/git"]
    assert config.analytics.enabled is True


def test_env_overrides(tmp_path: Path) -> None:
    path = write(tmp_path, "repos:\n  base_dirs: [/a]\nmqtt:\n  tls: false\n")
    env = {
        "GRM_MQTT_PORT": "8883",
        "GRM_MQTT_PASSWORD": "pw",
        "GITHUB_TOKEN": "tok",
        "GRM_BASE_DIRS": "/x::/y",
        "GRM_HOST_ID": "",
    }
    config = load_config(path, env=env)
    assert config.mqtt.port == 8883
    assert config.mqtt.password == "pw"
    assert config.github.token == "tok"
    assert config.repos.base_dirs == ["/x", "/y"]
    assert config.host_id  # defaults to the hostname


def test_env_bool_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from git_repo_monitor import config as config_module

    monkeypatch.setitem(config_module.ENV_OVERRIDES, "GRM_MQTT_TLS", ("mqtt", "tls"))
    config = load_config(None, env={"GRM_MQTT_TLS": "yes", "GRM_BASE_DIRS": "/a"})
    assert config.mqtt.tls is True


def test_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(write(tmp_path, "nope: 1\n"), env={})
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(write(tmp_path, "mqtt: [1]\n"), env={})
    with pytest.raises(ConfigError, match="base_dirs"):
        load_config(write(tmp_path, ""), env={})
    with pytest.raises(ConfigError, match="secret"):
        load_config(write(tmp_path, "webhook: {enabled: true}\nrepos: {base_dirs: [/a]}\n"), env={})


def test_default_env_is_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRM_BASE_DIRS", "/from/env")
    assert load_config(None).repos.base_dirs == ["/from/env"]
