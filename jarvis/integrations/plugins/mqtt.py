"""MQTT integration — publish, subscribe, status.

Off by default.  Uses the ``paho-mqtt`` library.  The broker URL and
credentials are stored as api_key fields.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class MqttIntegration(BaseIntegration):
    name = "mqtt"
    display_name = "MQTT"
    category = "smart_home"
    description = "Publish and subscribe to MQTT topics."
    icon = "\U0001f4e1"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "MQTT Broker URL (e.g. mqtt://host:1883)", "type": "text"},
        {"name": "mqtt_user", "label": "MQTT Username (optional)", "type": "text"},
        {"name": "mqtt_password", "label": "MQTT Password (optional)", "type": "password"},
    ]
    required_packages = ["paho-mqtt"]

    auth: object | None = None
    _client = None

    # -- auth helpers ----------------------------------------------------

    def _broker_url(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    def _mqtt_user(self) -> str | None:
        if self.auth and hasattr(self.auth, "get_field"):
            return self.auth.get_field(self.name, "mqtt_user")
        return None

    def _mqtt_password(self) -> str | None:
        if self.auth and hasattr(self.auth, "get_field"):
            return self.auth.get_field(self.name, "mqtt_password")
        return None

    async def connect(self) -> bool:
        self.connected = bool(self._broker_url())
        if not self.connected:
            log.info("[mqtt] no broker URL yet — add in Settings")
        return self.connected

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._broker_url())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("mqtt_publish", "Publish a message to an MQTT topic.",
                {"topic": {"type": "string"},
                 "payload": {"type": "string"}},
                required=["topic", "payload"]),
            _fn("mqtt_subscribe", "Subscribe to an MQTT topic (one-shot read).",
                {"topic": {"type": "string"}}, required=["topic"]),
            _fn("mqtt_status", "Check MQTT connection status.", {}),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^publish\s+(?:to\s+)?mqtt\s+(.+)$", "mqtt_publish", 300),
            (r"^отправь\s+в\s+mqtt\s+(.+)$", "mqtt_publish", 300),
            (r"^відправ\s+в\s+mqtt\s+(.+)$", "mqtt_publish", 300),
            (r"^mqtt\s+status$", "mqtt_status", 300),
            (r"^subscribe\s+mqtt\s+(.+)$", "mqtt_subscribe", 300),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._broker_url():
            return "MQTT is not connected, sir."

        try:
            if tool_name == "mqtt_publish":
                return self._publish(args)
            if tool_name == "mqtt_subscribe":
                return self._subscribe(args.get("topic", "#"))
            if tool_name == "mqtt_status":
                return self._status()
        except Exception as e:
            log.warning("[mqtt] %s failed", tool_name, exc_info=True)
            return f"MQTT error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    def _get_client(self):
        if self._client is None:
            import paho.mqtt.client as mqtt  # noqa: E402  (lazy)

            self._client = mqtt.Client()
            user = self._mqtt_user()
            pwd = self._mqtt_password()
            if user:
                self._client.username_pw_set(user, pwd)
            url = self._broker_url() or ""
            host = url.replace("mqtt://", "").replace("tcp://", "")
            port = 1883
            if ":" in host:
                host, port_str = host.rsplit(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    pass
            self._client.connect(host, port, 60)
        return self._client

    def _publish(self, args: dict) -> str:
        client = self._get_client()
        client.publish(args["topic"], args["payload"])
        return f"Published to {args['topic']}, sir."

    def _subscribe(self, topic: str) -> str:
        import time

        client = self._get_client()
        messages: list[str] = []

        def on_msg(_c, _ud, msg):
            messages.append(f"{msg.topic}: {msg.payload.decode('utf-8', 'replace')}")

        client.on_message = on_msg
        client.subscribe(topic)
        client.loop_start()
        time.sleep(2)
        client.loop_stop()
        client.unsubscribe(topic)
        if not messages:
            return f"No messages on '{topic}' (2s window), sir."
        return f"Received on {topic}, sir:\n" + "\n".join(messages[:10])

    def _status(self) -> str:
        if self._client and self._client.is_connected():
            return f"Connected to {self._broker_url()}, sir."
        return f"Not actively connected to {self._broker_url()}, sir."


def _fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required or [],
            },
        },
    }
