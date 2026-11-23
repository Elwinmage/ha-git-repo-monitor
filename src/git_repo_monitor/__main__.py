"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys

import aiohttp

from . import __version__
from .config import ConfigError, load_config
from .monitor import Monitor


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="git-repo-monitor", description="Publish git repository states to MQTT")
    parser.add_argument("-c", "--config", default="/etc/git-repo-monitor/config.yaml", help="YAML configuration")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


async def amain(config_path: str) -> None:
    config = load_config(config_path)
    logging.getLogger().setLevel(config.log_level.upper())
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        await Monitor(config, session).run()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(amain(args.config))
    except (ConfigError, OSError) as err:
        logging.getLogger("git_repo_monitor").error("Configuration error: %s", err)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
