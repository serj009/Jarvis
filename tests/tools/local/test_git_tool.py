"""Tests for jarvis.tools.local.git_tool.

All tests mock subprocess.run to avoid executing real git commands.
Voice patterns are tested via regex matching (no subprocess needed).
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.git_tool import (
    GitArgs,
    GitTool,
    _find_git_root,
    _run_git,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_subprocess(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Return a MagicMock mimicking subprocess.CompletedProcess."""
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


def _match_voice(pattern_regex: str, text: str) -> re.Match | None:
    """Compile and match a voice pattern regex against text."""
    return re.match(pattern_regex, text, re.IGNORECASE)


# ---------------------------------------------------------------------------
# GitArgs validation
# ---------------------------------------------------------------------------

class TestGitArgs:
    def test_valid_status(self):
        args = GitArgs(action="status")
        assert args.action == "status"
        assert args.message == ""
        assert args.branch == ""
        assert args.path == ""

    def test_valid_commit_with_message(self):
        args = GitArgs(action="commit", message="fix bug")
        assert args.action == "commit"
        assert args.message == "fix bug"

    def test_valid_branch(self):
        args = GitArgs(action="branch", branch="feature/new")
        assert args.action == "branch"
        assert args.branch == "feature/new"

    def test_invalid_action_rejected(self):
        with pytest.raises(Exception):
            GitArgs(action="rebase")

    def test_all_actions_accepted(self):
        for action in ("status", "diff", "log", "commit", "push", "pull",
                        "branch", "checkout", "add"):
            args = GitArgs(action=action)
            assert args.action == action


# ---------------------------------------------------------------------------
# _find_git_root
# ---------------------------------------------------------------------------

class TestFindGitRoot:
    def test_finds_git_in_current(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert _find_git_root(str(tmp_path)) == str(tmp_path)

    def test_finds_git_in_parent(self, tmp_path):
        (tmp_path / ".git").mkdir()
        child = tmp_path / "src" / "deep"
        child.mkdir(parents=True)
        assert _find_git_root(str(child)) == str(tmp_path)

    def test_returns_none_when_missing(self, tmp_path):
        import os
        import tempfile
        isolated = tempfile.mkdtemp()
        child = os.path.join(isolated, "no_git_here")
        os.makedirs(child)
        assert _find_git_root(child) is None


# ---------------------------------------------------------------------------
# _run_git low-level helper
# ---------------------------------------------------------------------------

class TestRunGit:
    def test_success(self, tmp_path):
        mock = _mock_subprocess(stdout="ok", returncode=0)
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            res = _run_git(["status"], str(tmp_path))
        assert res["success"] is True
        assert res["stdout"] == "ok"

    def test_failure_nonzero_exit(self, tmp_path):
        mock = _mock_subprocess(stderr="fatal: not a git repo", returncode=128)
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            res = _run_git(["status"], str(tmp_path))
        assert res["success"] is False
        assert "not a git repo" in res["error"]

    def test_timeout(self, tmp_path):
        import subprocess as sp
        with patch(
            "jarvis.tools.local.git_tool.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="git", timeout=30),
        ):
            res = _run_git(["push"], str(tmp_path))
        assert res["success"] is False
        assert "timed out" in res["error"]

    def test_git_not_found(self, tmp_path):
        with patch(
            "jarvis.tools.local.git_tool.subprocess.run",
            side_effect=FileNotFoundError("git"),
        ):
            res = _run_git(["status"], str(tmp_path))
        assert res["success"] is False
        assert "not installed" in res["error"]

    def test_long_output_truncated(self, tmp_path):
        mock = _mock_subprocess(stdout="x" * 8000, returncode=0)
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            res = _run_git(["log"], str(tmp_path))
        assert res["success"] is True
        assert "truncated" in res["stdout"]


# ---------------------------------------------------------------------------
# GitTool.execute — status
# ---------------------------------------------------------------------------

class TestGitToolStatus:
    async def test_status_clean(self, tmp_path):
        mock = _mock_subprocess(stdout="", returncode=0)
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="status", path=str(tmp_path)),
            )
        assert result.success
        assert "clean" in (result.output or "").lower()

    async def test_status_changes(self, tmp_path):
        mock = _mock_subprocess(stdout=" M file.py\n?? new.txt", returncode=0)
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="status", path=str(tmp_path)),
            )
        assert result.success
        assert "file.py" in (result.output or "")

    async def test_status_error(self, tmp_path):
        mock = _mock_subprocess(stderr="fatal: not a git repo", returncode=128)
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="status", path=str(tmp_path)),
            )
        assert not result.success


# ---------------------------------------------------------------------------
# GitTool.execute — diff
# ---------------------------------------------------------------------------

class TestGitToolDiff:
    async def test_diff_no_changes(self, tmp_path):
        mock = _mock_subprocess(stdout="", returncode=0)
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="diff", path=str(tmp_path)),
            )
        assert result.success
        assert "no changes" in (result.output or "").lower()

    async def test_diff_with_changes(self, tmp_path):
        mock = _mock_subprocess(
            stdout=" file.py | 3 +++\n 1 file changed, 3 insertions(+)",
            returncode=0,
        )
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="diff", path=str(tmp_path)),
            )
        assert result.success
        assert "file.py" in (result.output or "")


# ---------------------------------------------------------------------------
# GitTool.execute — log
# ---------------------------------------------------------------------------

class TestGitToolLog:
    async def test_log_output(self, tmp_path):
        mock = _mock_subprocess(
            stdout="abc1234 fix bug\ndef5678 init commit",
            returncode=0,
        )
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="log", path=str(tmp_path)),
            )
        assert result.success
        assert "fix bug" in (result.output or "")

    async def test_log_no_commits(self, tmp_path):
        mock = _mock_subprocess(stdout="", returncode=0)
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="log", path=str(tmp_path)),
            )
        assert result.success
        assert "no commits" in (result.output or "").lower()


# ---------------------------------------------------------------------------
# GitTool.execute — add
# ---------------------------------------------------------------------------

class TestGitToolAdd:
    async def test_add_success(self, tmp_path):
        mock = _mock_subprocess(stdout="", returncode=0)
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="add", path=str(tmp_path)),
            )
        assert result.success
        assert "staged" in (result.output or "").lower()


# ---------------------------------------------------------------------------
# GitTool.execute — commit
# ---------------------------------------------------------------------------

class TestGitToolCommit:
    async def test_commit_success(self, tmp_path):
        add_mock = _mock_subprocess(returncode=0)
        commit_mock = _mock_subprocess(
            stdout="[main abc1234] fix bug\n 3 files changed, 15 insertions(+)",
            returncode=0,
        )
        with patch(
            "jarvis.tools.local.git_tool.subprocess.run",
            side_effect=[add_mock, commit_mock],
        ):
            result = await GitTool().execute(
                GitArgs(action="commit", message="fix bug", path=str(tmp_path)),
            )
        assert result.success
        assert "fix bug" in (result.output or "")

    async def test_commit_empty_message_rejected(self, tmp_path):
        result = await GitTool().execute(
            GitArgs(action="commit", message="", path=str(tmp_path)),
        )
        assert not result.success
        assert "message" in (result.error or "").lower()

    async def test_commit_whitespace_message_rejected(self, tmp_path):
        result = await GitTool().execute(
            GitArgs(action="commit", message="   ", path=str(tmp_path)),
        )
        assert not result.success

    async def test_commit_add_fails(self, tmp_path):
        add_mock = _mock_subprocess(stderr="error: could not add", returncode=1)
        with patch(
            "jarvis.tools.local.git_tool.subprocess.run",
            return_value=add_mock,
        ):
            result = await GitTool().execute(
                GitArgs(action="commit", message="msg", path=str(tmp_path)),
            )
        assert not result.success
        assert "add" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# GitTool.execute — push
# ---------------------------------------------------------------------------

class TestGitToolPush:
    async def test_push_success(self, tmp_path):
        mock = _mock_subprocess(
            stdout="",
            stderr="Everything up-to-date",
            returncode=0,
        )
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="push", path=str(tmp_path)),
            )
        assert result.success

    async def test_push_failure(self, tmp_path):
        mock = _mock_subprocess(stderr="rejected", returncode=1)
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="push", path=str(tmp_path)),
            )
        assert not result.success


# ---------------------------------------------------------------------------
# GitTool.execute — pull
# ---------------------------------------------------------------------------

class TestGitToolPull:
    async def test_pull_success(self, tmp_path):
        mock = _mock_subprocess(stdout="Already up to date.", returncode=0)
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="pull", path=str(tmp_path)),
            )
        assert result.success
        assert "up to date" in (result.output or "").lower()


# ---------------------------------------------------------------------------
# GitTool.execute — branch
# ---------------------------------------------------------------------------

class TestGitToolBranch:
    async def test_branch_create(self, tmp_path):
        mock = _mock_subprocess(
            stderr="Switched to a new branch 'feat/x'",
            returncode=0,
        )
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="branch", branch="feat/x", path=str(tmp_path)),
            )
        assert result.success
        assert "feat/x" in (result.output or "")

    async def test_branch_empty_name(self, tmp_path):
        result = await GitTool().execute(
            GitArgs(action="branch", branch="", path=str(tmp_path)),
        )
        assert not result.success
        assert "name" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# GitTool.execute — checkout
# ---------------------------------------------------------------------------

class TestGitToolCheckout:
    async def test_checkout_success(self, tmp_path):
        mock = _mock_subprocess(
            stderr="Switched to branch 'main'",
            returncode=0,
        )
        with patch("jarvis.tools.local.git_tool.subprocess.run", return_value=mock):
            result = await GitTool().execute(
                GitArgs(action="checkout", branch="main", path=str(tmp_path)),
            )
        assert result.success
        assert "main" in (result.output or "")

    async def test_checkout_empty_branch(self, tmp_path):
        result = await GitTool().execute(
            GitArgs(action="checkout", branch="", path=str(tmp_path)),
        )
        assert not result.success


# ---------------------------------------------------------------------------
# Auto-detect .git (no path given)
# ---------------------------------------------------------------------------

class TestAutoDetect:
    async def test_no_git_found(self):
        with patch(
            "jarvis.tools.local.git_tool._find_git_root",
            return_value=None,
        ):
            result = await GitTool().execute(GitArgs(action="status"))
        assert not result.success
        assert "no git repository" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

class TestToolMeta:
    def test_name(self):
        assert GitTool.name == "git_tool"

    def test_requires_confirmation_false(self):
        """Per-action confirmation is via risk.py, not the class flag."""
        assert GitTool().requires_confirmation is False

    def test_has_voice_patterns(self):
        assert len(GitTool.voice_patterns) > 0

    def test_args_schema(self):
        assert GitTool.args_schema is GitArgs

    def test_confirmation_summary_commit(self):
        tool = GitTool()
        args = GitArgs(action="commit", message="fix bug")
        summary = tool.confirmation_summary(args)
        assert "fix bug" in summary

    def test_confirmation_summary_push(self):
        tool = GitTool()
        args = GitArgs(action="push")
        summary = tool.confirmation_summary(args)
        assert "push" in summary.lower()


# ---------------------------------------------------------------------------
# Voice patterns — EN
# ---------------------------------------------------------------------------

class TestVoicePatternsEN:
    def _patterns(self):
        return GitTool.voice_patterns

    def test_git_status(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "git status", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "status"}
                return
        pytest.fail("No pattern matched 'git status'")

    def test_git_diff(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "git diff", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "diff"}
                return
        pytest.fail("No pattern matched 'git diff'")

    def test_git_log(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "git log", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "log"}
                return
        pytest.fail("No pattern matched 'git log'")

    def test_commit_with_message(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "commit with message fix the bug", re.IGNORECASE)
            if m:
                result = vp.args(m)
                assert result["action"] == "commit"
                assert "fix the bug" in result["message"]
                return
        pytest.fail("No pattern matched 'commit with message fix the bug'")

    def test_git_push(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "git push", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "push"}
                return
        pytest.fail("No pattern matched 'git push'")

    def test_git_pull(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "git pull", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "pull"}
                return
        pytest.fail("No pattern matched 'git pull'")

    def test_create_branch(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "create branch feature/x", re.IGNORECASE)
            if m:
                result = vp.args(m)
                assert result["action"] == "branch"
                assert result["branch"] == "feature/x"
                return
        pytest.fail("No pattern matched 'create branch feature/x'")

    def test_switch_to_branch(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "switch to branch main", re.IGNORECASE)
            if m:
                result = vp.args(m)
                assert result["action"] == "checkout"
                assert result["branch"] == "main"
                return
        pytest.fail("No pattern matched 'switch to branch main'")

    def test_checkout_branch(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "checkout develop", re.IGNORECASE)
            if m:
                result = vp.args(m)
                assert result["action"] == "checkout"
                assert result["branch"] == "develop"
                return
        pytest.fail("No pattern matched 'checkout develop'")


# ---------------------------------------------------------------------------
# Voice patterns — RU
# ---------------------------------------------------------------------------

class TestVoicePatternsRU:
    def _patterns(self):
        return GitTool.voice_patterns

    def test_git_status_ru(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "гит статус", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "status"}
                return
        pytest.fail("No pattern matched 'гит статус'")

    def test_status_ru_short(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "статус", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "status"}
                return
        pytest.fail("No pattern matched 'статус'")

    def test_diff_ru(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "что изменилось", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "diff"}
                return
        pytest.fail("No pattern matched 'что изменилось'")

    def test_log_ru(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "покажи последние коммиты", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "log"}
                return
        pytest.fail("No pattern matched 'покажи последние коммиты'")

    def test_commit_ru(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "закоммить с сообщением фикс бага", re.IGNORECASE)
            if m:
                result = vp.args(m)
                assert result["action"] == "commit"
                assert "фикс бага" in result["message"]
                return
        pytest.fail("No pattern matched RU commit")

    def test_push_ru(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "запушь", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "push"}
                return
        pytest.fail("No pattern matched 'запушь'")

    def test_pull_ru(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "пулл", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "pull"}
                return
        pytest.fail("No pattern matched 'пулл'")

    def test_create_branch_ru(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "создай ветку feature/y", re.IGNORECASE)
            if m:
                result = vp.args(m)
                assert result["action"] == "branch"
                assert result["branch"] == "feature/y"
                return
        pytest.fail("No pattern matched 'создай ветку feature/y'")

    def test_checkout_ru(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "переключись на ветку main", re.IGNORECASE)
            if m:
                result = vp.args(m)
                assert result["action"] == "checkout"
                assert result["branch"] == "main"
                return
        pytest.fail("No pattern matched 'переключись на ветку main'")


# ---------------------------------------------------------------------------
# Voice patterns — UK
# ---------------------------------------------------------------------------

class TestVoicePatternsUK:
    def _patterns(self):
        return GitTool.voice_patterns

    def test_status_uk(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "гіт статус", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "status"}
                return
        pytest.fail("No pattern matched 'гіт статус'")

    def test_diff_uk(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "що змінилось", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "diff"}
                return
        pytest.fail("No pattern matched 'що змінилось'")

    def test_commit_uk(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "закомміть з повідомленням оновлення", re.IGNORECASE)
            if m:
                result = vp.args(m)
                assert result["action"] == "commit"
                assert "оновлення" in result["message"]
                return
        pytest.fail("No pattern matched UK commit")

    def test_push_uk(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "запуш", re.IGNORECASE)
            if m:
                assert vp.args(m) == {"action": "push"}
                return
        pytest.fail("No pattern matched 'запуш'")

    def test_branch_uk(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "створи гілку fix/bug", re.IGNORECASE)
            if m:
                result = vp.args(m)
                assert result["action"] == "branch"
                assert result["branch"] == "fix/bug"
                return
        pytest.fail("No pattern matched 'створи гілку fix/bug'")

    def test_checkout_uk(self):
        for vp in self._patterns():
            m = re.match(vp.regex, "переключись на гілку develop", re.IGNORECASE)
            if m:
                result = vp.args(m)
                assert result["action"] == "checkout"
                assert result["branch"] == "develop"
                return
        pytest.fail("No pattern matched 'переключись на гілку develop'")
