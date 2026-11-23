"""inotify-based watcher on the .git directories."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from watchfiles import Change, awatch

_LOGGER = logging.getLogger(__name__)

# Files at the root of a git dir whose change means a local state change.
ROOT_FILES = {"HEAD", "index", "packed-refs", "FETCH_HEAD", "ORIG_HEAD", "MERGE_HEAD"}


def relevant(git_dirs: list[Path]) -> Callable[[Change, str], bool]:
    """Build a watchfiles filter keeping ref updates and root state files."""
    roots = {str(d) for d in git_dirs}

    def keep(_: Change, path: str) -> bool:
        if path.endswith(".lock"):
            return False
        candidate = Path(path)
        if str(candidate.parent) in roots:
            return candidate.name in ROOT_FILES
        return "/refs/" in path

    return keep


def owner_of(path: str, git_dirs: dict[str, str]) -> str | None:
    """Map a changed path to the slug owning the git dir it lives in."""
    best: str | None = None
    best_len = -1
    for git_dir, slug in git_dirs.items():
        if (path == git_dir or path.startswith(git_dir + "/")) and len(git_dir) > best_len:
            best, best_len = slug, len(git_dir)
    return best


async def watch_git_dirs(git_dirs: dict[str, str], on_change: Callable[[str], None], stop: asyncio.Event) -> None:
    """Watch '<git dir>' (non recursive) and '<git dir>/refs' (recursive) until stop is set.

    Watching the whole git dir recursively would register a watch on every
    objects/xx directory for nothing.
    """
    dirs = [Path(d) for d in git_dirs]
    keep = relevant(dirs)

    async def run(paths: list[Path], recursive: bool) -> None:
        existing = [p for p in paths if p.is_dir()]
        if not existing:
            return
        async for changes in awatch(
            *existing, watch_filter=keep, recursive=recursive, stop_event=stop, debounce=500, step=100
        ):
            for _, path in changes:
                slug = owner_of(path, git_dirs)
                if slug:
                    on_change(slug)

    _LOGGER.info("Watching %d git directories", len(dirs))
    await asyncio.gather(run(dirs, False), run([d / "refs" for d in dirs], True))
