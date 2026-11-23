"""Orchestrator: keeps every repository state up to date and publishes it."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import aiomqtt
from aiohttp import web

from . import __version__
from .analytics import fetch_analytics, installs_for
from .config import Config
from .github import FAILED_CONCLUSIONS, GitHubClient, GitHubError, collect_remote
from .gitlocal import RepoInfo, assign_slugs, collect_local, find_repos, git_fetch
from .ha import COMMANDS, Topics, bridge_discovery, build_payload, repo_discovery
from .mqtt import Publisher, make_client
from .util import Debouncer, utcnow_iso
from .watcher import watch_git_dirs
from .webhook import make_app

_LOGGER = logging.getLogger(__name__)

HOT_EVENT_TYPES = {"PushEvent"}
REFRESH_EVENT_TYPES = {
    "ReleaseEvent",
    "CreateEvent",
    "DeleteEvent",
    "PullRequestEvent",
    "WatchEvent",
    "ForkEvent",
    "IssuesEvent",
    "PublicEvent",
}
REMOTE_ERRORS = (GitHubError, aiohttp.ClientError, TimeoutError)
MQTT_RETRY_DELAY = 5


@dataclass
class RepoState:
    info: RepoInfo
    local: dict[str, Any] | None = None
    remote: dict[str, Any] | None = None
    installs: dict[str, Any] | None = None
    discovery_topics: set[str] = field(default_factory=set)
    last_published: dict[str, Any] | None = None
    hot_started: float | None = None
    hot_until: float | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Monitor:
    def __init__(
        self,
        config: Config,
        session: aiohttp.ClientSession,
        mqtt_factory: Callable[[], aiomqtt.Client] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.session = session
        self.topics = Topics(config.mqtt.base_topic, config.host_slug, config.mqtt.discovery_prefix)
        self.publisher = Publisher()
        self.github = GitHubClient(session, config.github.token, config.github.api_url) if config.github.token else None
        self.repos: dict[str, RepoState] = {}
        self.login = config.github.login
        self.analytics: dict[str, Any] | None = None
        self.last_event_id: int | None = None
        self._clock = clock
        self._mqtt_factory = mqtt_factory or (
            lambda: make_client(config.mqtt, self.topics.availability, f"git-repo-monitor-{config.host_slug}")
        )
        self._debouncer = Debouncer(config.repos.debounce, self._debounced_local)
        self._watch_stop: asyncio.Event | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._last_statuses: dict[str, str] = {}

    # ------------------------------------------------------------------ helpers

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def slugs_for(self, full_name: str) -> list[str]:
        wanted = full_name.lower()
        return [s for s, r in self.repos.items() if (r.info.full_name or "").lower() == wanted]

    # ------------------------------------------------------------------ discovery of working copies

    async def rescan(self) -> bool:
        cfg = self.config.repos
        found = {info.slug: info for info in assign_slugs(find_repos(cfg.base_dirs, cfg.depth, cfg.exclude))}
        removed = [slug for slug in self.repos if slug not in found]
        added = [slug for slug in found if slug not in self.repos]
        for slug in removed:
            _LOGGER.info("Repository %s disappeared", slug)
            await self.unpublish_repo(slug)
            del self.repos[slug]
        for slug in added:
            _LOGGER.info("Repository %s found at %s", slug, found[slug].path)
            self.repos[slug] = RepoState(found[slug])
            await self.refresh_local(slug)
            if self.github:
                self.spawn(self.refresh_remote(slug))
        changed = bool(added or removed)
        if changed:
            self.restart_watcher()
            await self.publish_bridge()
        return changed

    # ------------------------------------------------------------------ refreshers

    async def refresh_local(self, slug: str) -> None:
        state = self.repos.get(slug)
        if state is None:
            return
        async with state.lock:
            state.local = await collect_local(state.info, self.config.repos.coverage_paths)
            state.installs = installs_for(self.analytics, state.local.get("domain"))
        await self.publish_repo(slug)

    async def _debounced_local(self, slug: Any) -> None:
        await self.refresh_local(str(slug))

    async def refresh_remote(self, slug: str) -> None:
        state = self.repos.get(slug)
        if state is None or self.github is None or not state.info.full_name:
            return
        async with state.lock:
            try:
                state.remote = await collect_remote(self.github, state.info.full_name, state.remote)
            except REMOTE_ERRORS as err:
                _LOGGER.warning("Remote refresh of %s failed: %s", slug, err)
                return
        await self.publish_repo(slug)

    async def refresh(self, slug: str) -> None:
        await self.refresh_local(slug)
        await self.refresh_remote(slug)

    async def fetch(self, slug: str) -> None:
        state = self.repos.get(slug)
        if state is None:
            return
        if await git_fetch(state.info, self.config.github.token):
            await self.refresh_local(slug)

    async def rerun_failed(self, slug: str) -> None:
        state = self.repos.get(slug)
        if state is None or self.github is None or not state.info.full_name or not state.remote:
            return
        rerun = False
        for workflow in state.remote.get("workflows", []):
            if workflow["state"] in FAILED_CONCLUSIONS and workflow.get("run_id"):
                try:
                    rerun |= await self.github.rerun_failed_jobs(state.info.full_name, workflow["run_id"])
                except REMOTE_ERRORS as err:
                    _LOGGER.warning("Re-run on %s failed: %s", slug, err)
        if rerun:
            self.mark_hot(slug)
            await self.refresh_remote(slug)

    # ------------------------------------------------------------------ hot mode

    def mark_hot(self, slug: str) -> None:
        state = self.repos.get(slug)
        if state is None:
            return
        now = self._clock()
        if state.hot_started is None:
            state.hot_started = now
        state.hot_until = now + self.config.github.hot_duration

    async def hot_tick(self) -> None:
        now = self._clock()
        for slug, state in list(self.repos.items()):
            if state.hot_started is None:
                continue
            if self.config.repos.fetch:
                await self.fetch(slug)
            await self.refresh_remote(slug)
            running = ((state.remote or {}).get("ci") or {}).get("running", 0)
            grace_over = now - state.hot_started >= self.config.github.hot_grace
            if (grace_over and not running) or now >= (state.hot_until or 0):
                _LOGGER.debug("Repository %s leaves hot mode", slug)
                state.hot_started = state.hot_until = None

    # ------------------------------------------------------------------ remote triggers

    async def on_remote_event(self, full_name: str, action: str) -> None:
        for slug in self.slugs_for(full_name):
            if action == "hot":
                self.mark_hot(slug)
            self.spawn(self.refresh_remote(slug))

    async def poll_events_once(self) -> None:
        if self.github is None or not self.login:
            return
        events, changed = await self.github.user_events(self.login)
        if not changed:
            return
        ids = [int(e["id"]) for e in events if str(e.get("id", "")).isdigit()]
        if not ids:
            return
        if self.last_event_id is None:
            # First poll only sets the baseline: the startup refresh covers older activity.
            self.last_event_id = max(ids)
            return
        fresh = [e for e in events if str(e.get("id", "")).isdigit() and int(e["id"]) > self.last_event_id]
        self.last_event_id = max([*ids, self.last_event_id])
        for event in fresh:
            full_name = (event.get("repo") or {}).get("name")
            kind = event.get("type")
            if not full_name:
                continue
            if kind in HOT_EVENT_TYPES:
                await self.on_remote_event(full_name, "hot")
            elif kind in REFRESH_EVENT_TYPES:
                await self.on_remote_event(full_name, "refresh")

    def events_interval(self) -> float:
        return max(self.config.github.events_interval, (self.github.poll_interval if self.github else 0) or 0)

    async def full_refresh_once(self) -> None:
        for slug in list(self.repos):
            if self.config.repos.fetch:
                await self.fetch(slug)
            await self.refresh(slug)
        await self.publish_bridge()

    async def status_tick(self) -> None:
        for slug in list(self.repos):
            await self.refresh_local(slug)

    async def daily_once(self) -> None:
        for state in self.repos.values():
            if state.remote and state.remote.get("stars") is not None:
                state.remote["stars_baseline"] = state.remote["stars"]
        if self.config.analytics.enabled:
            data = await fetch_analytics(self.session, self.config.analytics.url)
            if data is not None:
                self.analytics = data
        for slug, state in list(self.repos.items()):
            state.installs = installs_for(self.analytics, (state.local or {}).get("domain"))
            await self.publish_repo(slug)

    # ------------------------------------------------------------------ publication

    def payload_for(self, slug: str) -> dict[str, Any]:
        state = self.repos[slug]
        return build_payload(
            host=self.config.host_id,
            slug=slug,
            name=state.info.name,
            path=str(state.info.path),
            full_name=state.info.full_name,
            local=state.local,
            remote=state.remote,
            installs=state.installs,
            cmd_topic=self.topics.repo_cmd(slug),
            updated_at=utcnow_iso(),
        )

    async def publish_repo(self, slug: str, force: bool = False) -> None:
        state = self.repos.get(slug)
        if state is None:
            return
        payload = self.payload_for(slug)
        core = {k: v for k, v in payload.items() if k != "updated_at"}
        if not force and core == state.last_published:
            return
        if not self.publisher.connected:
            return
        discovery = repo_discovery(self.topics, payload)
        for topic in state.discovery_topics - set(discovery):
            await self.publisher.publish(topic, "")
        for topic, config in discovery.items():
            await self.publisher.publish(topic, config)
        state.discovery_topics = set(discovery)
        await self.publisher.publish(self.topics.repo_state(slug), payload)
        state.last_published = core
        if self._last_statuses.get(slug) != payload["status"]:
            self._last_statuses[slug] = payload["status"]
            await self.publish_bridge()

    async def unpublish_repo(self, slug: str) -> None:
        state = self.repos[slug]
        for topic in state.discovery_topics:
            await self.publisher.publish(topic, "")
        await self.publisher.publish(self.topics.repo_state(slug), "")
        self._last_statuses.pop(slug, None)

    async def publish_bridge(self) -> None:
        payload = {
            "repos": len(self.repos),
            "statuses": dict(sorted(self._last_statuses.items())),
            "rate_remaining": self.github.rate_remaining if self.github else None,
            "rate_limit": self.github.rate_limit if self.github else None,
            "login": self.login,
            "version": __version__,
        }
        await self.publisher.publish(self.topics.bridge_state, payload)

    async def publish_all(self) -> None:
        await self.publisher.publish(self.topics.availability, "online")
        for topic, config in bridge_discovery(self.topics, self.config.host_id).items():
            await self.publisher.publish(topic, config)
        for slug in list(self.repos):
            await self.publish_repo(slug, force=True)
        await self.publish_bridge()

    # ------------------------------------------------------------------ MQTT

    async def handle_message(self, topic: str, payload: bytes) -> None:
        text = payload.decode(errors="replace").strip()
        if topic == self.topics.ha_status:
            if text == "online":
                await self.publish_all()
            return
        if topic == self.topics.bridge_cmd:
            if text == "refresh_all":
                self.spawn(self.full_refresh_once())
            return
        slug = self.topics.slug_from_cmd(topic)
        if slug is None or slug not in self.repos or text not in COMMANDS:
            _LOGGER.debug("Ignoring command %r on %s", text, topic)
            return
        handler: Callable[[str], Awaitable[None]] = {
            "refresh": self.refresh,
            "fetch": self.fetch,
            "rerun_failed": self.rerun_failed,
        }[text]
        self.spawn(handler(slug))

    async def mqtt_session(self) -> None:
        """One MQTT connection lifetime."""
        async with self._mqtt_factory() as client:
            self.publisher.attach(client)
            try:
                for topic in (self.topics.ha_status, self.topics.bridge_cmd, self.topics.repo_cmd_wildcard):
                    await client.subscribe(topic, qos=1)
                await self.publish_all()
                async for message in client.messages:
                    await self.handle_message(str(message.topic), bytes(message.payload or b""))
            finally:
                self.publisher.detach()

    async def mqtt_loop(self) -> None:
        while True:
            try:
                await self.mqtt_session()
            except aiomqtt.MqttError as err:
                _LOGGER.warning("MQTT connection lost: %s; retrying in %ss", err, MQTT_RETRY_DELAY)
            await asyncio.sleep(MQTT_RETRY_DELAY)

    # ------------------------------------------------------------------ watcher

    def restart_watcher(self) -> None:
        if self._watch_stop is not None:
            self._watch_stop.set()
        self._watch_stop = asyncio.Event()
        git_dirs = {str(state.info.git_dir): slug for slug, state in self.repos.items()}
        self._watch_task = self.spawn(watch_git_dirs(git_dirs, self._debouncer.trigger, self._watch_stop))

    # ------------------------------------------------------------------ main

    async def every(self, interval: Callable[[], float], job: Callable[[], Awaitable[Any]], name: str) -> None:
        while True:
            await asyncio.sleep(interval())
            try:
                await job()
            except Exception:  # a failing job must not stop the daemon
                _LOGGER.exception("Periodic job %s failed", name)

    async def serve_webhook(self) -> None:
        cfg = self.config.webhook
        runner = web.AppRunner(make_app(cfg.secret or "", cfg.path, self.on_remote_event))
        await runner.setup()
        site = web.TCPSite(runner, cfg.host, cfg.port)
        await site.start()
        _LOGGER.info("Webhook listening on %s:%s%s", cfg.host, cfg.port, cfg.path)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    async def run(self) -> None:
        cfg = self.config
        await self.rescan()
        if self.github and not self.login:
            try:
                self.login = await self.github.login()
            except REMOTE_ERRORS as err:
                _LOGGER.warning("Cannot resolve the GitHub login: %s", err)
        if cfg.analytics.enabled:
            self.spawn(self.daily_once())

        jobs: list[Coroutine[Any, Any, Any]] = [
            self.mqtt_loop(),
            self.every(lambda: cfg.repos.rescan_interval, self.rescan, "rescan"),
            self.every(lambda: cfg.repos.status_interval, self.status_tick, "status"),
            self.every(lambda: 86400, self.daily_once, "daily"),
        ]
        if self.github:
            jobs += [
                self.every(self.events_interval, self.poll_events_once, "events"),
                self.every(lambda: cfg.github.full_refresh_interval, self.full_refresh_once, "full refresh"),
                self.every(lambda: cfg.github.hot_interval, self.hot_tick, "hot"),
            ]
        else:
            _LOGGER.warning("No GitHub token: only the local state is monitored")
        if cfg.webhook.enabled:
            jobs.append(self.serve_webhook())
        await asyncio.gather(*jobs)
