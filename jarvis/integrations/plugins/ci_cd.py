"""CI/CD integration — GitHub Actions workflow status, trigger, logs.

Off by default. Shares the same GitHub PAT as the github plugin.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class CiCdIntegration(BaseIntegration):
    name = "github_actions"
    display_name = "GitHub Actions"
    category = "development"
    description = "Workflow status, trigger, logs for GitHub Actions."
    icon = "\u2699\ufe0f"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "GitHub PAT (same as GitHub plugin)", "type": "password"},
    ]
    required_packages = ["PyGithub"]

    auth: object | None = None

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    async def connect(self) -> bool:
        self.connected = bool(self._token())
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("actions_status", "Status of recent workflow runs.",
                {"repo": {"type": "string"}}, required=["repo"]),
            _fn("actions_trigger", "Trigger a workflow dispatch.",
                {"repo": {"type": "string"}, "workflow": {"type": "string"},
                 "ref": {"type": "string", "description": "branch (default main)"}},
                required=["repo", "workflow"]),
            _fn("actions_logs", "Logs from a failed workflow run.",
                {"repo": {"type": "string"}, "run_id": {"type": "integer"}},
                required=["repo", "run_id"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^build\s+status$", "actions_status", 310),
            (r"^\u0441\u0442\u0430\u0442\u0443\u0441\s+\u0431\u0438\u043b\u0434\u0430$", "actions_status", 310),
            (r"^run\s+tests?\s+on\s+github$", "actions_trigger", 320),
            (r"^\u0437\u0430\u043f\u0443\u0441\u0442\u0438\s+\u0442\u0435\u0441\u0442\u044b\s+\u043d\u0430\s+\u0433\u0438\u0442\u0445\u0430\u0431\u0435$", "actions_trigger", 320),
            (r"^why\s+did\s+ci\s+fail$", "actions_logs", 310),
            (r"^\u043f\u043e\u0447\u0435\u043c\u0443\s+ci\s+\u0443\u043f\u0430\u043b$", "actions_logs", 310),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        token = self._token()
        if token is None:
            return "GitHub Actions is not connected, sir."
        try:
            from github import Github
            g = Github(token)
        except Exception as e:
            return str(e)
        try:
            if tool_name == "actions_status":
                return self._status(g, args)
            if tool_name == "actions_trigger":
                return self._trigger(g, args)
            if tool_name == "actions_logs":
                return self._logs(g, args)
        except Exception as e:
            log.warning("[ci_cd] %s failed", tool_name, exc_info=True)
            return f"CI/CD error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    def _status(self, g, args: dict) -> str:
        repo = g.get_repo(args["repo"])
        runs = list(repo.get_workflow_runs()[:5])
        if not runs:
            return f"No workflow runs in {args['repo']}, sir."
        lines = [f"  \u2022 {r.name}: {r.conclusion or r.status} ({r.head_branch})"
                 for r in runs]
        return f"Recent runs in {args['repo']}, sir:\n" + "\n".join(lines)

    def _trigger(self, g, args: dict) -> str:
        repo = g.get_repo(args["repo"])
        wf = repo.get_workflow(args["workflow"])
        ref = args.get("ref", "main")
        wf.create_dispatch(ref=ref)
        return f"Triggered '{args['workflow']}' on {ref}, sir."

    def _logs(self, g, args: dict) -> str:
        repo = g.get_repo(args["repo"])
        run = repo.get_workflow_run(int(args["run_id"]))
        jobs = list(run.jobs())
        if not jobs:
            return f"No jobs found for run {args['run_id']}, sir."
        lines = []
        for job in jobs[:5]:
            steps = [s for s in job.steps if s.conclusion == "failure"]
            for s in steps[:3]:
                lines.append(f"  \u2022 {job.name}/{s.name}: {s.conclusion}")
        if not lines:
            return f"Run {args['run_id']} — no failed steps found, sir."
        return f"Failed steps in run {args['run_id']}, sir:\n" + "\n".join(lines)


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
