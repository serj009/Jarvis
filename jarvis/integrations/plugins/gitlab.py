"""GitLab integration — projects, issues, MRs, pipelines.

Off by default.  Uses python-gitlab SDK.  Personal Access Token as api_key.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class GitLabIntegration(BaseIntegration):
    name = "gitlab"
    display_name = "GitLab"
    category = "development"
    description = "Browse GitLab projects, issues, merge requests, and pipelines."
    icon = "\U0001f99a"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "GitLab Personal Access Token", "type": "password"},
        {"name": "gitlab_url", "label": "GitLab URL (e.g. https://gitlab.com)",
         "type": "text"},
    ]
    required_packages = ["python-gitlab"]

    auth: object | None = None

    # -- auth helpers ----------------------------------------------------

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    def _url(self) -> str:
        if self.auth and hasattr(self.auth, "get_field"):
            u = self.auth.get_field(self.name, "gitlab_url")
            if u:
                return u
        return "https://gitlab.com"

    async def connect(self) -> bool:
        self.connected = bool(self._token())
        if not self.connected:
            log.info("[gitlab] no token yet — add it in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("gitlab_projects", "List your GitLab projects.", {}),
            _fn("gitlab_issues", "List issues for a project.",
                {"project_id": {"type": "string"}}, required=["project_id"]),
            _fn("gitlab_create_issue", "Create a GitLab issue.",
                {"project_id": {"type": "string"},
                 "title": {"type": "string"},
                 "description": {"type": "string"}},
                required=["project_id", "title"]),
            _fn("gitlab_mrs", "List merge requests for a project.",
                {"project_id": {"type": "string"}}, required=["project_id"]),
            _fn("gitlab_pipelines", "List recent pipelines for a project.",
                {"project_id": {"type": "string"}}, required=["project_id"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^gitlab\s+projects$", "gitlab_projects", 300),
            (r"^проекты\s+gitlab$", "gitlab_projects", 300),
            (r"^проєкти\s+gitlab$", "gitlab_projects", 300),
            (r"^gitlab\s+issues\s+(.+)$", "gitlab_issues", 310),
            (r"^gitlab\s+merge\s+requests?\s+(.+)$", "gitlab_mrs", 310),
            (r"^mr\s+в\s+gitlab\s+(.+)$", "gitlab_mrs", 310),
            (r"^create\s+gitlab\s+issue\s+(.+)$", "gitlab_create_issue", 320),
            (r"^gitlab\s+pipelines?\s+(.+)$", "gitlab_pipelines", 310),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._token():
            return "GitLab is not connected, sir."
        try:
            gl = _make_gitlab(self._url(), self._token())
        except Exception as e:
            return str(e)

        try:
            if tool_name == "gitlab_projects":
                return self._projects(gl)
            if tool_name == "gitlab_issues":
                return self._issues(gl, args.get("project_id", ""))
            if tool_name == "gitlab_create_issue":
                return self._create_issue(gl, args)
            if tool_name == "gitlab_mrs":
                return self._mrs(gl, args.get("project_id", ""))
            if tool_name == "gitlab_pipelines":
                return self._pipelines(gl, args.get("project_id", ""))
        except Exception as e:
            log.warning("[gitlab] %s failed", tool_name, exc_info=True)
            return f"GitLab error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    def _projects(self, gl) -> str:
        projects = gl.projects.list(membership=True, per_page=15)
        if not projects:
            return "No projects, sir."
        lines = [f"  \u2022 {p.name} (id:{p.id})" for p in projects[:15]]
        return f"Your GitLab projects, sir:\n" + "\n".join(lines)

    def _issues(self, gl, project_id: str) -> str:
        proj = gl.projects.get(project_id)
        issues = proj.issues.list(state="opened", per_page=15)
        if not issues:
            return "No open issues, sir."
        lines = [f"  \u2022 #{i.iid} {i.title}" for i in issues[:15]]
        return f"{len(issues)} open issue(s), sir:\n" + "\n".join(lines)

    def _create_issue(self, gl, args: dict) -> str:
        proj = gl.projects.get(args["project_id"])
        issue = proj.issues.create({
            "title": args["title"],
            "description": args.get("description", ""),
        })
        return f"Issue #{issue.iid} '{args['title']}' created, sir."

    def _mrs(self, gl, project_id: str) -> str:
        proj = gl.projects.get(project_id)
        mrs = proj.mergerequests.list(state="opened", per_page=15)
        if not mrs:
            return "No open merge requests, sir."
        lines = [f"  \u2022 !{mr.iid} {mr.title}" for mr in mrs[:15]]
        return f"{len(mrs)} open MR(s), sir:\n" + "\n".join(lines)

    def _pipelines(self, gl, project_id: str) -> str:
        proj = gl.projects.get(project_id)
        pipes = proj.pipelines.list(per_page=10)
        if not pipes:
            return "No pipelines, sir."
        lines = [f"  \u2022 #{p.id} {p.status} ({p.ref})" for p in pipes[:10]]
        return f"Recent pipelines, sir:\n" + "\n".join(lines)


def _make_gitlab(url: str, token: str):
    """Build a python-gitlab client lazily."""
    import gitlab as _gl  # noqa: E402

    return _gl.Gitlab(url, private_token=token)


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
