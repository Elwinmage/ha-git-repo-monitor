"""Home Assistant side: state payloads, statuses and MQTT discovery messages."""

from __future__ import annotations

from typing import Any

from . import MONITOR_ID, __version__

STATUSES = ["ok", "ci_failed", "ci_running", "needs_sync", "no_upstream", "unknown"]
COMMANDS = ["refresh", "fetch", "rerun_failed"]


class Topics:
    """MQTT topic layout for one host."""

    def __init__(self, base: str, host: str, discovery_prefix: str) -> None:
        self.base = base.rstrip("/")
        self.host = host
        self.discovery = discovery_prefix.rstrip("/")

    @property
    def availability(self) -> str:
        return f"{self.base}/{self.host}/availability"

    @property
    def bridge_state(self) -> str:
        return f"{self.base}/{self.host}/bridge"

    @property
    def bridge_cmd(self) -> str:
        return f"{self.base}/{self.host}/cmd"

    @property
    def repo_cmd_wildcard(self) -> str:
        return f"{self.base}/{self.host}/+/cmd"

    @property
    def ha_status(self) -> str:
        return f"{self.discovery}/status"

    def repo_state(self, slug: str) -> str:
        return f"{self.base}/{self.host}/{slug}/state"

    def repo_cmd(self, slug: str) -> str:
        return f"{self.base}/{self.host}/{slug}/cmd"

    def discovery_topic(self, component: str, object_id: str) -> str:
        return f"{self.discovery}/{component}/grm_{self.host}/{object_id}/config"

    def slug_from_cmd(self, topic: str) -> str | None:
        prefix = f"{self.base}/{self.host}/"
        if topic.startswith(prefix) and topic.endswith("/cmd"):
            middle = topic[len(prefix) : -len("/cmd")]
            if middle and "/" not in middle:
                return middle
        return None


def compute_status(local: dict[str, Any] | None, remote: dict[str, Any] | None) -> str:
    if not local:
        return "unknown"
    ci = (remote or {}).get("ci") or {}
    if ci.get("failed"):
        return "ci_failed"
    if ci.get("running"):
        return "ci_running"
    if local.get("dirty") or local.get("ahead") or local.get("behind"):
        return "needs_sync"
    if not local.get("upstream"):
        return "no_upstream"
    return "ok"


def current_version(local: dict[str, Any] | None) -> str | None:
    """The version present in the working copy: manifest/package first, then the last tag."""
    local = local or {}
    return local.get("manifest_version") or local.get("tag")


def build_payload(
    *,
    host: str,
    slug: str,
    name: str,
    path: str,
    full_name: str | None,
    local: dict[str, Any] | None,
    remote: dict[str, Any] | None,
    installs: dict[str, Any] | None,
    cmd_topic: str,
    updated_at: str,
) -> dict[str, Any]:
    remote = remote or {}
    public_remote = {k: v for k, v in remote.items() if k not in ("ci", "stars_baseline")}
    stars = remote.get("stars")
    baseline = remote.get("stars_baseline")
    return {
        "monitor": MONITOR_ID,
        "host": host,
        "repo": slug,
        "name": name,
        "path": path,
        "full_name": full_name,
        "html_url": remote.get("html_url") or (f"https://github.com/{full_name}" if full_name else None),
        "status": compute_status(local, remote),
        "version": current_version(local),
        "local": local or {},
        "remote": public_remote,
        "ci": remote.get("ci") or {"total": 0, "ok": 0, "failed": 0, "running": 0},
        "stars": stars,
        "stars_delta": (stars - baseline) if stars is not None and baseline is not None else None,
        "installs": installs,
        "cmd_topic": cmd_topic,
        "updated_at": updated_at,
    }


def _device(host: str, slug: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    device: dict[str, Any] = {
        "identifiers": [f"grm_{host}_{slug}"],
        "name": name,
        "manufacturer": "ha-git-repo-monitor",
        "model": "Git repository",
        "via_device": f"grm_{host}_bridge",
    }
    if payload.get("version"):
        device["sw_version"] = str(payload["version"])
    if payload.get("html_url"):
        device["configuration_url"] = payload["html_url"]
    return device


def repo_entities(payload: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Return (component, key, config) for the entities this repository should expose."""
    entities: list[tuple[str, str, dict[str, Any]]] = [
        (
            "sensor",
            "status",
            {
                "name": "Status",
                "icon": "mdi:source-repository",
                "device_class": "enum",
                "options": STATUSES,
                "value_template": "{{ value_json.status }}",
                "json_attributes_template": "{{ value_json | tojson }}",
            },
        ),
        (
            "binary_sensor",
            "ci_problem",
            {
                "name": "CI problem",
                "device_class": "problem",
                "value_template": "{{ 'ON' if value_json.ci.failed > 0 else 'OFF' }}",
            },
        ),
        (
            "binary_sensor",
            "needs_sync",
            {
                "name": "Needs sync",
                "icon": "mdi:source-branch-sync",
                "value_template": (
                    "{{ 'ON' if value_json.local.dirty or value_json.local.ahead or value_json.local.behind "
                    "else 'OFF' }}"
                ),
            },
        ),
        (
            "sensor",
            "version",
            {"name": "Version", "icon": "mdi:tag-outline", "value_template": "{{ value_json.version }}"},
        ),
    ]
    if payload.get("remote", {}).get("available"):
        entities.append(
            (
                "sensor",
                "stars",
                {
                    "name": "Stars",
                    "icon": "mdi:star",
                    "state_class": "measurement",
                    "unit_of_measurement": "stars",
                    "value_template": "{{ value_json.stars }}",
                },
            )
        )
    if payload.get("installs") is not None:
        entities.append(
            (
                "sensor",
                "active_installs",
                {
                    "name": "Active installs",
                    "icon": "mdi:download-network",
                    "state_class": "measurement",
                    "unit_of_measurement": "installs",
                    "value_template": "{{ value_json.installs.total }}",
                },
            )
        )
    if payload.get("local", {}).get("coverage") is not None:
        entities.append(
            (
                "sensor",
                "coverage",
                {
                    "name": "Coverage",
                    "icon": "mdi:shield-check-outline",
                    "state_class": "measurement",
                    "unit_of_measurement": "%",
                    "value_template": "{{ value_json.local.coverage }}",
                },
            )
        )
    for command in COMMANDS:
        if command == "rerun_failed" and not payload.get("full_name"):
            continue
        entities.append(
            (
                "button",
                command,
                {
                    "name": command.replace("_", " ").capitalize(),
                    "icon": {"refresh": "mdi:refresh", "fetch": "mdi:cloud-download", "rerun_failed": "mdi:replay"}[
                        command
                    ],
                    "payload_press": command,
                    "entity_category": "config",
                },
            )
        )
    return entities


def repo_discovery(topics: Topics, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map discovery topic -> config for one repository."""
    host, slug = topics.host, payload["repo"]
    device = _device(host, slug, payload["name"], payload)
    result: dict[str, dict[str, Any]] = {}
    for component, key, extra in repo_entities(payload):
        config: dict[str, Any] = {
            "unique_id": f"grm_{host}_{slug}_{key}",
            "default_entity_id": f"{component}.git_{slug}_{key}",
            "device": device,
            "availability_topic": topics.availability,
            "origin": {"name": "ha-git-repo-monitor", "sw_version": __version__},
        }
        if component == "button":
            config["command_topic"] = topics.repo_cmd(slug)
        else:
            config["state_topic"] = topics.repo_state(slug)
            if "json_attributes_template" in extra:
                config["json_attributes_topic"] = topics.repo_state(slug)
        config.update(extra)
        result[topics.discovery_topic(component, f"{slug}_{key}")] = config
    return result


def bridge_discovery(topics: Topics, host_id: str) -> dict[str, dict[str, Any]]:
    host = topics.host
    device = {
        "identifiers": [f"grm_{host}_bridge"],
        "name": f"Git repo monitor ({host_id})",
        "manufacturer": "ha-git-repo-monitor",
        "model": "Monitor daemon",
        "sw_version": __version__,
    }
    common = {"device": device, "availability_topic": topics.availability}
    return {
        topics.discovery_topic("sensor", "bridge_repos"): {
            **common,
            "unique_id": f"grm_{host}_bridge_repos",
            "default_entity_id": f"sensor.git_repo_monitor_{host}_repos",
            "name": "Repositories",
            "icon": "mdi:source-repository-multiple",
            "state_topic": topics.bridge_state,
            "value_template": "{{ value_json.repos }}",
            "json_attributes_topic": topics.bridge_state,
            "json_attributes_template": "{{ value_json.statuses | tojson }}",
        },
        topics.discovery_topic("sensor", "bridge_rate_limit"): {
            **common,
            "unique_id": f"grm_{host}_bridge_rate_limit",
            "default_entity_id": f"sensor.git_repo_monitor_{host}_github_rate_limit",
            "name": "GitHub API remaining",
            "icon": "mdi:speedometer",
            "entity_category": "diagnostic",
            "state_topic": topics.bridge_state,
            "value_template": "{{ value_json.rate_remaining }}",
        },
        topics.discovery_topic("button", "bridge_refresh_all"): {
            **common,
            "unique_id": f"grm_{host}_bridge_refresh_all",
            "default_entity_id": f"button.git_repo_monitor_{host}_refresh_all",
            "name": "Refresh all",
            "icon": "mdi:refresh",
            "command_topic": topics.bridge_cmd,
            "payload_press": "refresh_all",
        },
    }
