"""GitHub webhook receiver."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable

from aiohttp import web

_LOGGER = logging.getLogger(__name__)

# Events after which workflows are expected to start: follow the repository closely.
HOT_EVENTS = {"push", "workflow_run", "workflow_job", "check_suite"}
# Events that only change counters or metadata: refresh once.
REFRESH_EVENTS = {"release", "create", "delete", "star", "watch", "fork", "pull_request", "issues", "repository"}

WebhookCallback = Callable[[str, str], Awaitable[None]]


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Check the X-Hub-Signature-256 header."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def classify(event: str) -> str | None:
    if event in HOT_EVENTS:
        return "hot"
    if event in REFRESH_EVENTS:
        return "refresh"
    return None


def make_app(secret: str, path: str, callback: WebhookCallback) -> web.Application:
    """Build the aiohttp application; callback(full_name, action) is awaited."""

    async def handle(request: web.Request) -> web.Response:
        body = await request.read()
        if not verify_signature(secret, body, request.headers.get("X-Hub-Signature-256")):
            _LOGGER.warning("Rejected webhook with invalid signature from %s", request.remote)
            return web.Response(status=401, text="invalid signature")
        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return web.Response(text="pong")
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            return web.Response(status=400, text="invalid json")
        full_name = ((payload.get("repository") or {}) if isinstance(payload, dict) else {}).get("full_name")
        action = classify(event)
        if not full_name or action is None:
            return web.Response(status=202, text="ignored")
        _LOGGER.debug("Webhook %s for %s -> %s", event, full_name, action)
        await callback(full_name, action)
        return web.Response(status=202, text="accepted")

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application(client_max_size=25 * 1024 * 1024)
    app.router.add_post(path, handle)
    app.router.add_get("/health", health)
    return app
