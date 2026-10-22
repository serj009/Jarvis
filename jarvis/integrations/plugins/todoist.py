"""Todoist integration — today tasks, add, complete, projects + proactive.

Off by default.  Todoist REST API v2 over httpx.  get_proactive_data()
reports overdue tasks for the ProactiveEngine.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class TodoistIntegration(BaseIntegration):
    name = "todoist"
    display_name = "Todoist"
    category = "productivity"
    description = "Manage Todoist tasks: today, add, complete, projects."
    icon = "\u2705"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "Todoist API Token", "type": "password"},
    ]
    required_packages: list[str] = []

    auth: object | None = None

    _BASE = "https://api.todoist.com/rest/v2"

    # -- auth helpers ----------------------------------------------------

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}"}

    async def connect(self) -> bool:
        self.connected = bool(self._token())
        if not self.connected:
            log.info("[todoist] no token yet — add it in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("todoist_today", "List tasks due today.", {}),
            _fn("todoist_add", "Add a new task.",
                {"content": {"type": "string"},
                 "due_string": {"type": "string", "description": "e.g. 'today', 'tomorrow'"}},
                required=["content"]),
            _fn("todoist_complete", "Complete a task by ID.",
                {"task_id": {"type": "string"}}, required=["task_id"]),
            _fn("todoist_projects", "List all projects.", {}),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^my\s+tasks\s+(?:for\s+)?today$", "todoist_today", 300),
            (r"^мои\s+задачи\s+(?:на\s+)?сегодня$", "todoist_today", 300),
            (r"^мої\s+завдання\s+(?:на\s+)?сьогодні$", "todoist_today", 300),
            (r"^add\s+task\s+(.+)$", "todoist_add", 320),
            (r"^добавь\s+задачу\s+(.+)$", "todoist_add", 320),
            (r"^додай\s+завдання\s+(.+)$", "todoist_add", 320),
            (r"^complete\s+task\s+(.+)$", "todoist_complete", 320),
            (r"^заверши\s+задачу\s+(.+)$", "todoist_complete", 320),
            (r"^todoist\s+projects$", "todoist_projects", 300),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._token():
            return "Todoist is not connected, sir."
        import httpx  # lazy

        try:
            if tool_name == "todoist_today":
                return await self._today()
            if tool_name == "todoist_add":
                return await self._add(args)
            if tool_name == "todoist_complete":
                return await self._complete(args.get("task_id", ""))
            if tool_name == "todoist_projects":
                return await self._projects()
        except Exception as e:
            log.warning("[todoist] %s failed", tool_name, exc_info=True)
            return f"Todoist error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- proactive -------------------------------------------------------

    def get_proactive_data(self) -> list[dict] | None:
        """Overdue tasks → ProactiveEngine."""
        if not self.connected or not self._token():
            return None
        try:
            import httpx

            r = httpx.get(f"{self._BASE}/tasks",
                          headers=self._headers(),
                          params={"filter": "overdue"})
            r.raise_for_status()
            tasks = r.json()
            if not tasks:
                return None
            return [{"type": "overdue_tasks", "count": len(tasks),
                     "tasks": [t.get("content", "") for t in tasks[:5]]}]
        except Exception:
            log.warning("[todoist] proactive fetch failed", exc_info=True)
            return None

    # -- internals -------------------------------------------------------

    async def _today(self) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/tasks",
                            headers=self._headers(),
                            params={"filter": "today"})
            r.raise_for_status()
        tasks = r.json()
        if not tasks:
            return "No tasks for today, sir."
        lines = [f"  \u2022 {t.get('content', '?')} (id:{t.get('id', '?')})"
                 for t in tasks[:15]]
        return f"{len(tasks)} task(s) today, sir:\n" + "\n".join(lines)

    async def _add(self, args: dict) -> str:
        import httpx

        body = {"content": args["content"]}
        if args.get("due_string"):
            body["due_string"] = args["due_string"]
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{self._BASE}/tasks",
                             headers=self._headers(), json=body)
            r.raise_for_status()
        return f"Task '{args['content']}' added, sir."

    async def _complete(self, task_id: str) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.post(f"{self._BASE}/tasks/{task_id}/close",
                             headers=self._headers())
            r.raise_for_status()
        return "Task completed, sir."

    async def _projects(self) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{self._BASE}/projects",
                            headers=self._headers())
            r.raise_for_status()
        projects = r.json()
        if not projects:
            return "No projects, sir."
        lines = [f"  \u2022 {p.get('name', '?')} (id:{p.get('id', '?')})"
                 for p in projects[:15]]
        return f"{len(projects)} project(s), sir:\n" + "\n".join(lines)


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
