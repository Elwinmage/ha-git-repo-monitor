"""Tests for local git inspection."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import git
from git_repo_monitor import gitlocal
from git_repo_monitor.gitlocal import (
    RepoInfo,
    assign_slugs,
    auth_args,
    collect_local,
    find_repos,
    git_fetch,
    parse_remote_url,
    read_coverage,
    read_manifest,
    resolve_git_dir,
    run_git,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/Elwinmage/ha-reef-card.git", "Elwinmage/ha-reef-card"),
        ("https://user:tok@github.com/Elwinmage/ha-reef-card/", "Elwinmage/ha-reef-card"),
        ("git@github.com:Elwinmage/ha-reef-card.git", "Elwinmage/ha-reef-card"),
        ("ssh://git@github.com/Elwinmage/repo", "Elwinmage/repo"),
        ("https://gitlab.com/a/b.git", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_remote_url(url: str | None, expected: str | None) -> None:
    assert parse_remote_url(url) == expected


def test_resolve_git_dir(tmp_path: Path, make_repo: Callable[..., Path]) -> None:
    repo = make_repo("plain")
    assert resolve_git_dir(repo) == repo / ".git"

    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {repo / '.git'}\n")
    assert resolve_git_dir(worktree) == repo / ".git"

    relative = tmp_path / "rel"
    relative.mkdir()
    (relative / ".git").write_text("gitdir: ../repos/plain/.git\n")
    assert resolve_git_dir(relative) == (repo / ".git").resolve()

    missing = tmp_path / "missing"
    missing.mkdir()
    (missing / ".git").write_text("gitdir: /does/not/exist\n")
    assert resolve_git_dir(missing) is None

    garbage = tmp_path / "garbage"
    garbage.mkdir()
    (garbage / ".git").write_text("nonsense")
    assert resolve_git_dir(garbage) is None

    assert resolve_git_dir(tmp_path) is None


def test_find_repos(tmp_path: Path, make_repo: Callable[..., Path]) -> None:
    base = tmp_path / "repos"
    make_repo("a")
    make_repo("skipme")
    make_repo("deep", base=base / "group")
    (base / ".hidden").mkdir()
    (base / "link").symlink_to(base / "a")

    found = [p.name for p, _ in find_repos([str(base)], depth=1, exclude=["skip*"])]
    assert found == ["a"]
    found = [p.name for p, _ in find_repos([str(base)], depth=2, exclude=[])]
    assert found == ["a", "deep", "skipme"]


def test_find_repos_unreadable(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert find_repos([str(tmp_path / "nope")], depth=1, exclude=[]) == []
    assert "Cannot list" in caplog.text


def test_assign_slugs(tmp_path: Path) -> None:
    pairs = [(tmp_path / "x" / "ha-card", tmp_path), (tmp_path / "y" / "ha_card", tmp_path)]
    pairs.append((tmp_path / "z" / "HA-card", tmp_path))
    assert [r.slug for r in assign_slugs(pairs)] == ["ha_card", "ha_card_2", "ha_card_3"]


async def test_run_git(make_repo: Callable[..., Path], caplog: pytest.LogCaptureFixture) -> None:
    repo = make_repo("r")
    code, out = await run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    assert (code, out) == (0, "main")
    caplog.set_level("DEBUG")
    code, _ = await run_git(repo, "rev-parse", "nope-ref")
    assert code != 0
    assert "nope-ref" in caplog.text


async def test_run_git_timeout(make_repo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo("r")
    real = asyncio.create_subprocess_exec

    async def slow(*_: object, **kwargs: object) -> asyncio.subprocess.Process:
        return await real("sleep", "5", **kwargs)

    monkeypatch.setattr(gitlocal.asyncio, "create_subprocess_exec", slow)
    assert await run_git(repo, "status", timeout=0.05) == (124, "")


def test_auth_args() -> None:
    assert auth_args(None, "t") == []
    assert auth_args("https://github.com/a/b", None) == []
    assert auth_args("https://gitlab.com/a/b", "t") == []
    args = auth_args("git@github.com:a/b", "t")
    assert "url.https://github.com/.insteadOf=git@github.com:" in args
    assert "AUTHORIZATION: basic" in args[-1]


async def test_git_fetch(tmp_path: Path, make_repo: Callable[..., Path]) -> None:
    repo = make_repo("r", upstream=True)
    info = RepoInfo(slug="r", name="r", path=repo, git_dir=repo / ".git")
    assert await git_fetch(info, None) is True
    lonely = make_repo("lonely")
    info = RepoInfo(slug="l", name="l", path=lonely, git_dir=lonely / ".git", remote_url="https://github.com/a/b")
    assert await git_fetch(info, "token") is False


def test_read_manifest(tmp_path: Path) -> None:
    assert read_manifest(tmp_path) == {"version": None, "domain": None, "kind": None}

    (tmp_path / "package.json").write_text("{broken")
    assert read_manifest(tmp_path)["kind"] == "package"
    (tmp_path / "package.json").write_text('{"version": "2.0.0"}')
    assert read_manifest(tmp_path) == {"version": "2.0.0", "domain": None, "kind": "package"}

    broken = tmp_path / "custom_components" / "aaa"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{broken")
    good = tmp_path / "custom_components" / "redsea"
    good.mkdir()
    (good / "manifest.json").write_text('{"domain": "redsea", "version": "1.4.0"}')
    assert read_manifest(tmp_path) == {"version": "1.4.0", "domain": "redsea", "kind": "integration"}


def test_read_coverage(tmp_path: Path) -> None:
    paths = ["reports/coverage.xml", "coverage.xml"]
    assert read_coverage(tmp_path, paths) == (None, None)
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "coverage.xml").write_text("<not xml")
    (tmp_path / "coverage.xml").write_text("<coverage/>")
    assert read_coverage(tmp_path, paths) == (None, None)
    (reports / "coverage.xml").write_text('<coverage line-rate="0.9876"/>')
    pct, mtime = read_coverage(tmp_path, paths)
    assert pct == 98.8
    assert mtime is not None


async def test_collect_local_full(tmp_path: Path, make_repo: Callable[..., Path]) -> None:
    repo = make_repo("full", upstream=True)
    git(repo, "tag", "v1.0.0")
    (repo / "new.txt").write_text("n")
    git(repo, "add", "new.txt")
    git(repo, "commit", "-q", "-m", "second")
    (repo / "dirty.txt").write_text("d")
    git(repo, "remote", "set-url", "origin", "git@github.com:Elwinmage/full.git")
    # The remote-tracking ref still points to the bare repository state.
    info = RepoInfo(slug="full", name="full", path=repo, git_dir=repo / ".git")
    local = await collect_local(info, ["coverage.xml"])
    assert local["branch"] == "main"
    assert local["upstream"] == "origin/main"
    assert (local["ahead"], local["behind"]) == (1, 0)
    assert local["dirty"] is True
    assert local["dirty_count"] == 1
    assert local["tag"] == "v1.0.0"
    assert local["commits_since_tag"] == 1
    assert local["last_commit"]["subject"] == "second"
    assert info.full_name == "Elwinmage/full"
    assert local["coverage"] is None


async def test_collect_local_dirty_ignores_host_status_config(
    tmp_path: Path, make_repo: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Untracked files count as dirty even when the host hides them from git status."""
    repo = make_repo("hidden")
    (repo / "dirty.txt").write_text("d")
    hostile = tmp_path / "gitconfig-hostile"
    hostile.write_text("[status]\n\tshowUntrackedFiles = no\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    info = RepoInfo(slug="hidden", name="hidden", path=repo, git_dir=repo / ".git")
    local = await collect_local(info, [])
    assert local["dirty"] is True
    assert local["dirty_count"] == 1


async def test_collect_local_dirty_honours_gitignore(make_repo: Callable[..., Path]) -> None:
    """Files ignored by the repository itself are not uncommitted work."""
    repo = make_repo("ignored")
    (repo / ".gitignore").write_text("*.log\n")
    (repo / "build.log").write_text("noise")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ignore logs")
    info = RepoInfo(slug="ignored", name="ignored", path=repo, git_dir=repo / ".git")
    local = await collect_local(info, [])
    assert local["dirty"] is False
    assert local["dirty_count"] == 0


async def test_collect_local_empty_repo(make_repo: Callable[..., Path]) -> None:
    repo = make_repo("empty", commit=False)
    info = RepoInfo(slug="e", name="e", path=repo, git_dir=repo / ".git")
    local = await collect_local(info, [])
    assert local["upstream"] is None
    assert local["tag"] is None
    assert local["last_commit"] is None
    assert info.full_name is None
    assert info.remote_url is None


async def test_collect_local_odd_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {
        "rev-parse": (0, ""),
        "rev-list": (0, "garbage"),
        "describe": (0, "v1"),
        "log": (0, "no separators"),
        "remote": (0, ""),
        "status": (0, ""),
    }

    async def fake(_: Path, *args: str, timeout: float = 0) -> tuple[int, str]:
        if args[:2] == ("rev-parse", "--abbrev-ref") and "@{upstream}" in args:
            return 0, "origin/main"
        return answers[args[0]]

    monkeypatch.setattr(gitlocal, "run_git", fake)
    info = RepoInfo(slug="o", name="o", path=tmp_path, git_dir=tmp_path)
    local = await collect_local(info, [])
    assert (local["ahead"], local["behind"]) == (0, 0)
    assert local["commits_since_tag"] is None
    assert local["last_commit"] is None
    assert local["branch"] is None
