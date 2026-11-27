"""Configuration loading (YAML file + environment overrides)."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from .util import slugify


class ConfigError(Exception):
    """Raised when the configuration is invalid."""


@dataclass
class MqttConfig:
    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    tls: bool = False
    base_topic: str = "git_repo_monitor"
    discovery_prefix: str = "homeassistant"


@dataclass
class GitHubConfig:
    token: str | None = None
    # Login whose public activity feed is polled; resolved from the token when empty.
    login: str | None = None
    events_interval: int = 60
    full_refresh_interval: int = 1200
    hot_interval: int = 20
    # Hot mode lasts at most this long, and at least hot_grace even if no run is visible yet.
    hot_duration: int = 900
    hot_grace: int = 120
    api_url: str = "https://api.github.com"


@dataclass
class WebhookConfig:
    enabled: bool = False
    host: str = "0.0.0.0"  # noqa: S104 - listening on all interfaces is the intended default
    port: int = 8099
    path: str = "/webhook"
    secret: str | None = None


@dataclass
class ReposConfig:
    base_dirs: list[str] = field(default_factory=list)
    depth: int = 1
    exclude: list[str] = field(default_factory=list)
    rescan_interval: int = 600
    status_interval: int = 90
    debounce: float = 2.0
    fetch: bool = True
    coverage_paths: list[str] = field(default_factory=lambda: ["reports/coverage.xml", "coverage.xml"])


@dataclass
class AnalyticsConfig:
    enabled: bool = True
    interval: int = 86400
    url: str = "https://analytics.home-assistant.io/custom_integrations.json"


@dataclass
class Config:
    host_id: str = ""
    log_level: str = "INFO"
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    repos: ReposConfig = field(default_factory=ReposConfig)
    analytics: AnalyticsConfig = field(default_factory=AnalyticsConfig)

    @property
    def host_slug(self) -> str:
        return slugify(self.host_id)


# Environment variables taking precedence over the YAML file (secrets mostly).
ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "GRM_HOST_ID": ("", "host_id"),
    "GRM_MQTT_HOST": ("mqtt", "host"),
    "GRM_MQTT_PORT": ("mqtt", "port"),
    "GRM_MQTT_USERNAME": ("mqtt", "username"),
    "GRM_MQTT_PASSWORD": ("mqtt", "password"),
    "GITHUB_TOKEN": ("github", "token"),
    "GRM_GITHUB_TOKEN": ("github", "token"),
    "GRM_WEBHOOK_SECRET": ("webhook", "secret"),
}


def _is_text(field_type: str) -> bool:
    """True for fields declared as plain text (``str`` or ``str | None``)."""
    return field_type.replace(" ", "") in ("str", "str|None")


def _build(cls: type, data: dict[str, Any], where: str) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"'{where}' must be a mapping")
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(f"unknown key(s) in '{where}': {', '.join(sorted(unknown))}")
    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        default = known[name].default_factory() if callable(known[name].default_factory) else None
        if is_dataclass(default):
            kwargs[name] = _build(type(default), value or {}, f"{where}.{name}" if where else name)
        elif _is_text(known[name].type) and value is not None and not isinstance(value, str):
            # YAML reads an all-digit password (or host_id, token...) as a number: keep text textual.
            kwargs[name] = str(value)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def _coerce(field_type: str, raw: str) -> Any:
    """Convert an environment value to the type declared on the field, not to the current value's type."""
    plain = field_type.replace(" ", "")
    if plain.startswith("bool"):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if plain.startswith("int"):
        return int(raw)
    return raw


def load_config(path: str | os.PathLike[str] | None, env: dict[str, str] | None = None) -> Config:
    """Load the YAML configuration and apply environment overrides."""
    env = os.environ if env is None else env
    data: dict[str, Any] = {}
    if path:
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    config: Config = _build(Config, data, "")

    for var, (section, key) in ENV_OVERRIDES.items():
        if var in env and env[var] != "":
            target = getattr(config, section) if section else config
            declared = next(f.type for f in fields(target) if f.name == key)
            setattr(target, key, _coerce(declared, env[var]))
    if env.get("GRM_BASE_DIRS"):
        config.repos.base_dirs = [d for d in env["GRM_BASE_DIRS"].split(":") if d]

    if not config.host_id:
        config.host_id = socket.gethostname()
    if not config.repos.base_dirs:
        raise ConfigError("repos.base_dirs must list at least one directory")
    if config.webhook.enabled and not config.webhook.secret:
        raise ConfigError("webhook.secret is required when the webhook is enabled")
    if not config.webhook.path.startswith("/"):
        config.webhook.path = "/" + config.webhook.path
    return config
