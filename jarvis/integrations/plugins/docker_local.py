"""Docker local integration — containers, images, logs.

Off by default. Connects to the local Docker socket (no auth needed).
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class DockerIntegration(BaseIntegration):
    name = "docker"
    display_name = "Docker"
    category = "development"
    description = "Manage local Docker containers, images, and logs."
    icon = "\U0001f433"

    auth_type = "none"
    auth_fields = []
    required_packages = ["docker"]

    _client = None

    async def connect(self) -> bool:
        try:
            import docker  # noqa: E402
            self._client = docker.from_env()
            self._client.ping()
            self.connected = True
        except Exception:
            self.connected = False
        return self.connected

    async def disconnect(self) -> None:
        self._client = None
        self.connected = False

    async def health_check(self) -> bool:
        if self._client is None:
            return False
        try:
            self._client.ping()
            return True
        except Exception:
            return False

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("docker_ps", "List running containers.", {}),
            _fn("docker_start", "Start a container.",
                {"name": {"type": "string"}}, required=["name"]),
            _fn("docker_stop", "Stop a container.",
                {"name": {"type": "string"}}, required=["name"]),
            _fn("docker_logs", "Show container logs.",
                {"name": {"type": "string"}, "tail": {"type": "integer"}},
                required=["name"]),
            _fn("docker_images", "List local Docker images.", {}),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^show\s+containers$", "docker_ps", 300),
            (
                r"^\u043f\u043e\u043a\u0430\u0436\u0438\s+\u043a\u043e\u043d\u0442\u0435\u0439\u043d\u0435\u0440\u044b$",
                "docker_ps",
                300,
            ),
            (r"^stop\s+(.+)$", "docker_stop", 310),
            (r"^\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u0438\s+(.+)$", "docker_stop", 310),
            (r"^start\s+container\s+(.+)$", "docker_start", 310),
            (
                r"^\u0437\u0430\u043f\u0443\u0441\u0442\u0438\s+\u043a\u043e\u043d\u0442\u0435\u0439\u043d\u0435\u0440\s+(.+)$",
                "docker_start",
                310,
            ),
            (r"^docker\s+logs?\s+(.+)$", "docker_logs", 310),
            (
                r"^\u043b\u043e\u0433\u0438\s+\u043a\u043e\u043d\u0442\u0435\u0439\u043d\u0435\u0440\u0430\s+(.+)$",
                "docker_logs",
                310,
            ),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if self._client is None:
            return "Docker is not connected, sir."
        try:
            if tool_name == "docker_ps":
                return self._ps()
            if tool_name == "docker_start":
                return self._start(args)
            if tool_name == "docker_stop":
                return self._stop(args)
            if tool_name == "docker_logs":
                return self._docker_logs(args)
            if tool_name == "docker_images":
                return self._images()
        except Exception as e:
            log.warning("[docker] %s failed", tool_name, exc_info=True)
            return f"Docker error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    def _ps(self) -> str:
        containers = self._client.containers.list()
        if not containers:
            return "No running containers, sir."
        lines = [f"  \u2022 {c.name}: {c.status} ({c.short_id})" for c in containers]
        return "Running containers, sir:\n" + "\n".join(lines)

    def _start(self, args: dict) -> str:
        c = self._client.containers.get(args["name"])
        c.start()
        return f"Started '{args['name']}', sir."

    def _stop(self, args: dict) -> str:
        c = self._client.containers.get(args["name"])
        c.stop()
        return f"Stopped '{args['name']}', sir."

    def _docker_logs(self, args: dict) -> str:
        c = self._client.containers.get(args["name"])
        tail = args.get("tail", 30)
        logs = c.logs(tail=tail).decode("utf-8", "replace")
        return f"Logs for '{args['name']}', sir:\n{logs[:3000]}"

    def _images(self) -> str:
        images = self._client.images.list()
        if not images:
            return "No local images, sir."
        lines = []
        for img in images[:20]:
            tags = ", ".join(img.tags) if img.tags else img.short_id
            lines.append(f"  \u2022 {tags}")
        return "Local images, sir:\n" + "\n".join(lines)


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
