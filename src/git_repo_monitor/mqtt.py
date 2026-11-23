"""MQTT publishing helpers."""

from __future__ import annotations

import json
import logging
import ssl
from typing import Any

import aiomqtt

from .config import MqttConfig

_LOGGER = logging.getLogger(__name__)


def make_client(config: MqttConfig, availability_topic: str, identifier: str) -> aiomqtt.Client:
    will = aiomqtt.Will(availability_topic, payload="offline", qos=1, retain=True)
    tls = ssl.create_default_context() if config.tls else None
    return aiomqtt.Client(
        hostname=config.host,
        port=config.port,
        username=config.username,
        password=config.password,
        identifier=identifier,
        will=will,
        tls_context=tls,
    )


class Publisher:
    """Wraps the current MQTT client; publishing while disconnected is a no-op."""

    def __init__(self) -> None:
        self._client: aiomqtt.Client | None = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    def attach(self, client: aiomqtt.Client) -> None:
        self._client = client

    def detach(self) -> None:
        self._client = None

    async def publish(self, topic: str, payload: Any, retain: bool = True) -> bool:
        if self._client is None:
            return False
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, separators=(",", ":"), default=str)
        try:
            await self._client.publish(topic, payload, qos=1, retain=retain)
        except aiomqtt.MqttError as err:
            _LOGGER.warning("MQTT publish to %s failed: %s", topic, err)
            return False
        return True
