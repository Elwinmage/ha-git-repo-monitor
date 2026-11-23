"""Shared fixtures."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from git_repo_monitor.config import Config, ReposConfig


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "HOME": str(path),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )
    return result.stdout.strip()


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[..., Path]:
    """Create a working copy, optionally with a bare 'origin' upstream."""

    def factory(name: str, *, upstream: bool = False, commit: bool = True, base: Path | None = None) -> Path:
        root = (base or tmp_path / "repos") / name
        root.mkdir(parents=True)
        git(root, "init", "-q", "-b", "main")
        if commit:
            (root / "file.txt").write_text("hello\n")
            git(root, "add", "file.txt")
            git(root, "commit", "-q", "-m", "initial commit")
        if upstream:
            bare = tmp_path / "remotes" / f"{name}.git"
            bare.parent.mkdir(parents=True, exist_ok=True)
            git(tmp_path, "init", "-q", "--bare", str(bare))
            git(root, "remote", "add", "origin", str(bare))
            git(root, "push", "-q", "-u", "origin", "main")
        return root

    return factory


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(host_id="Alare", repos=ReposConfig(base_dirs=[str(tmp_path / "repos")], debounce=0.01))


@dataclass
class FakeMessage:
    topic: str
    payload: bytes


class FakeMqttClient:
    """Stand-in for aiomqtt.Client."""

    def __init__(self, messages: list[FakeMessage] | None = None, fail_publish: bool = False) -> None:
        self.published: list[tuple[str, Any, bool]] = []
        self.subscribed: list[str] = []
        self._messages = messages or []
        self.fail_publish = fail_publish

    async def publish(self, topic: str, payload: Any, qos: int = 0, retain: bool = False) -> None:
        if self.fail_publish:
            import aiomqtt

            raise aiomqtt.MqttError("boom")
        self.published.append((topic, payload, retain))

    async def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscribed.append(topic)

    async def __aenter__(self) -> FakeMqttClient:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    @property
    def messages(self) -> Any:
        async def gen() -> Any:
            for message in self._messages:
                yield message

        return gen()

    def topics(self) -> list[str]:
        return [topic for topic, _, _ in self.published]
