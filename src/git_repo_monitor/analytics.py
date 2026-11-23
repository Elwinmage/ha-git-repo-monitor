"""Home Assistant analytics: active installations of custom integrations."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


async def fetch_analytics(session: aiohttp.ClientSession, url: str) -> dict[str, Any] | None:
    """Download the public custom integrations statistics file."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as response:
            if response.status != 200:
                _LOGGER.warning("Analytics download failed: HTTP %s", response.status)
                return None
            data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.warning("Analytics download failed: %s", err)
        return None
    return data if isinstance(data, dict) else None


def installs_for(data: dict[str, Any] | None, domain: str | None) -> dict[str, Any] | None:
    """Return {'total', 'versions'} for an integration domain, None when absent."""
    if not data or not domain or domain not in data:
        return None
    entry = data[domain]
    if not isinstance(entry, dict):
        return None
    versions = entry.get("versions") or {}
    # Newest versions first is the most useful order for a quick look.
    ordered = dict(sorted(versions.items(), key=lambda item: item[1], reverse=True))
    return {"total": entry.get("total", 0), "versions": ordered}
