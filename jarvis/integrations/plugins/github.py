"""GitHub integration — repos, issues, PRs, notifications.

Off by default. Uses a Personal Access Token. The heavy ``PyGithub``
import is lazy (inside execute paths) so registry discovery works with
the package absent.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class GitHubIntegration(BaseIntegration):
    name = "github"
    display_name = "GitHub"
    category = "development"
    description = "Repos, issues, PRs, notifications, CI status."
    icon = "\U0001f4bb"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "Personal Access Token", "type": "password"},
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
            _fn("github_repos", "List your repositories.", {}),
            _fn("github_issues", "List open issues for a repo.",
                {"repo": {"type": "string", "description": "owner/repo"}},
                required=["repo"]),
            _fn("github_create_issue", "Create a new issue.",
                {"repo": {"type": "string"}, "title": {"type": "string"},
                 "body": {"type": "string"}},
                required=["repo", "title"]),
            _fn("github_pr_list", "List open pull requests.",
                {"repo": {"type": "string"}}, required=["repo"]),
            _fn("github_pr_status", "Get status of a specific PR.",
                {"repo": {"type": "string"}, "number": {"type": "integer"}},
                required=["repo", "number"]),
            _fn("github_notifications", "List unread notifications.", {}),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^show\s+my\s+repos$", "github_repos", 300),
            (
                r"^\u043f\u043e\u043a\u0430\u0436\u0438\s+\u043c\u043e\u0438\s+\u0440\u0435\u043f\u043e$",
                "github_repos",
                300,
            ),
            (r"^create\s+issue\s+in\s+(.+)$", "github_create_issue", 320),
            (
                r"^\u0441\u043e\u0437\u0434\u0430\u0439\s+issue\s+\u0432\s+(.+)$",
                "github_create_issue",
                320,
            ),
            (r"^any\s+open\s+prs?$", "github_pr_list", 310),
            (
                r"^\u0435\u0441\u0442\u044c\s+\u043e\u0442\u043a\u0440\u044b\u0442\u044b\u0435\s+pr$",
                "github_pr_list",
                310,
            ),
            (r"^github\s+notifications$", "github_notifications", 300),
            (
                r"^\u0443\u0432\u0435\u0434\u043e\u043c\u043b\u0435\u043d\u0438\u044f\s+\u0433\u0438\u0442\u0445\u0430\u0431$",
                "github_notifications",
                300,
            ),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        token = self._token()
        if token is None:
            return "GitHub is not connected, sir."
        try:
            from github import Github  # noqa: E402 (lazy)
            g = Github(token)
        except Exception as e:
            return str(e)
        try:
            if tool_name == "github_repos":
                return self._repos(g)
            if tool_name == "github_issues":
                return self._issues(g, args)
            if tool_name == "github_create_issue":
                return self._create_issue(g, args)
            if tool_name == "github_pr_list":
                return self._pr_list(g, args)
            if tool_name == "github_pr_status":
                return self._pr_status(g, args)
            if tool_name == "github_notifications":
                return self._notifications(g)
        except Exception as e:
            log.warning("[github] %s failed", tool_name, exc_info=True)
            return f"GitHub error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    def get_proactive_data(self) -> list[dict] | None:
        if not self.connected:
            return None
        token = self._token()
        if not token:
            return None
        try:
            from github import Github
            g = Github(token)
            notifs = list(g.get_user().get_notifications()[:10])
            if not notifs:
                return None
            review_requests = [n for n in notifs if n.reason == "review_requested"]
            items = []
            if review_requests:
                items.append({"type": "github_review_requests",
                              "count": len(review_requests)})
            return items or None
        except Exception:
            return None

    # -- internals -------------------------------------------------------

    def _repos(self, g) -> str:
        repos = list(g.get_user().get_repos(sort="updated")[:15])
        if not repos:
            return "No repositories found, sir."
        lines = [f"  \u2022 {r.full_name}" for r in repos]
        return "Your repos, sir:\n" + "\n".join(lines)

    def _issues(self, g, args: dict) -> str:
        repo = g.get_repo(args["repo"])
        issues = list(repo.get_issues(state="open")[:10])
        if not issues:
            return f"No open issues in {args['repo']}, sir."
        lines = [f"  \u2022 #{i.number}: {i.title}" for i in issues]
        return f"Open issues in {args['repo']}, sir:\n" + "\n".join(lines)

    def _create_issue(self, g, args: dict) -> str:
        repo = g.get_repo(args["repo"])
        issue = repo.create_issue(title=args["title"],
                                   body=args.get("body", ""))
        return f"Created issue #{issue.number} in {args['repo']}, sir."

    def _pr_list(self, g, args: dict) -> str:
        repo = g.get_repo(args["repo"])
        prs = list(repo.get_pulls(state="open")[:10])
        if not prs:
            return f"No open PRs in {args['repo']}, sir."
        lines = [f"  \u2022 #{p.number}: {p.title}" for p in prs]
        return f"Open PRs in {args['repo']}, sir:\n" + "\n".join(lines)

    def _pr_status(self, g, args: dict) -> str:
        repo = g.get_repo(args["repo"])
        pr = repo.get_pull(int(args["number"]))
        return (f"PR #{pr.number} '{pr.title}': {pr.state}, "
                f"mergeable={pr.mergeable}, sir.")

    def _notifications(self, g) -> str:
        notifs = list(g.get_user().get_notifications()[:10])
        if not notifs:
            return "No unread notifications, sir."
        lines = [f"  \u2022 [{n.reason}] {n.subject.title}" for n in notifs]
        return "GitHub notifications, sir:\n" + "\n".join(lines)


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
