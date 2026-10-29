"""Tests for voice-based confirmation of destructive CodingAgent actions.

Covers:
  - Positive / negative response matching (EN, RU, UK)
  - Timeout → False (safe default)
  - Ambiguous first answer → retry
  - Second attempt positive / negative
  - Language-specific spoken questions
  - CodingAgent integration: skips overwrite on reject
  - CodingAgent integration: skips git commit on reject
  - CodingAgent integration: proceeds on confirm
  - Backward compatibility: confirm=None → no confirmation needed
"""

from __future__ import annotations

import asyncio
import json

# Tests that shell out to a real ``git`` binary are skipped when git is not
# on PATH (e.g. portable git installs) — otherwise they fail with WinError 2.
import shutil
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jarvis.coding.voice_confirm import (
    _NEGATIVE,
    _POSITIVE,
    VoiceConfirm,
    is_negative,
    is_positive,
)

_GIT_AVAILABLE = shutil.which("git") is not None
requires_git = pytest.mark.skipif(
    not _GIT_AVAILABLE, reason="git executable not found on PATH"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vc(
    *,
    listen_responses: list[str | None] | None = None,
    speak_fn: AsyncMock | None = None,
    listen_fn: AsyncMock | None = None,
) -> tuple[VoiceConfirm, AsyncMock, AsyncMock]:
    """Create a VoiceConfirm with mocked speak/listen."""
    if speak_fn is None:
        speak_fn = AsyncMock()
    if listen_fn is None:
        listen_fn = AsyncMock()
        if listen_responses is not None:
            listen_fn.side_effect = listen_responses
    return VoiceConfirm(speak_fn, listen_fn), speak_fn, listen_fn


# ---------------------------------------------------------------------------
# Positive / negative pattern matching
# ---------------------------------------------------------------------------

class TestPatternMatching:
    """Test the _POSITIVE and _NEGATIVE regex patterns."""

    @pytest.mark.parametrize(
        "word",
        [
            "yes", "Yeah", "YEP", "sure", "ok", "okay",
            "confirm", "go ahead",
            # Russian
            "да", "ок", "подтверждаю", "давай", "конечно", "хорошо",
            "подтверди",
            # Ukrainian
            "так", "підтверджую", "добре", "гаразд", "підтверди",
        ],
    )
    def test_positive_responses(self, word: str) -> None:
        assert is_positive(word), f"{word!r} should be positive"

    @pytest.mark.parametrize(
        "word",
        [
            "no", "Nope", "CANCEL", "stop", "don't", "abort",
            # Russian
            "нет", "отмена", "стоп", "не надо", "не нужно", "отмени",
            # Ukrainian
            "ні", "скасуй", "не треба", "зупини", "скасувати",
        ],
    )
    def test_negative_responses(self, word: str) -> None:
        assert is_negative(word), f"{word!r} should be negative"

    @pytest.mark.parametrize("word", ["maybe", "hmm", "что?", "repeat"])
    def test_ambiguous_is_neither(self, word: str) -> None:
        assert not is_positive(word)
        assert not is_negative(word)


# ---------------------------------------------------------------------------
# VoiceConfirm unit tests
# ---------------------------------------------------------------------------

class TestVoiceConfirm:
    """Tests for VoiceConfirm.confirm()."""

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self) -> None:
        """When listen returns None (timeout), result is False."""
        vc, speak, listen = _make_vc(listen_responses=[None])
        result = await vc.confirm("delete old_config.py")
        assert result is False
        speak.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_positive_first_try(self) -> None:
        vc, speak, listen = _make_vc(listen_responses=["yes"])
        result = await vc.confirm("delete old_config.py")
        assert result is True
        speak.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_negative_first_try(self) -> None:
        vc, speak, listen = _make_vc(listen_responses=["no"])
        result = await vc.confirm("delete old_config.py")
        assert result is False
        speak.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_positive_ru(self) -> None:
        vc, _, _ = _make_vc(listen_responses=["да"])
        assert await vc.confirm("удалить файл", language="ru") is True

    @pytest.mark.asyncio
    async def test_negative_uk(self) -> None:
        vc, _, _ = _make_vc(listen_responses=["ні"])
        assert await vc.confirm("видалити файл", language="uk") is False

    @pytest.mark.asyncio
    async def test_ambiguous_asks_again(self) -> None:
        """If first response is ambiguous, VoiceConfirm asks again."""
        vc, speak, listen = _make_vc(listen_responses=["maybe", "yes"])
        result = await vc.confirm("overwrite main.py")
        assert result is True
        # speak called twice: original question + retry prompt
        assert speak.await_count == 2

    @pytest.mark.asyncio
    async def test_second_attempt_positive(self) -> None:
        vc, _, _ = _make_vc(listen_responses=["hmm", "да"])
        assert await vc.confirm("git push") is True

    @pytest.mark.asyncio
    async def test_second_attempt_negative(self) -> None:
        vc, _, _ = _make_vc(listen_responses=["what?", "no"])
        assert await vc.confirm("git push") is False

    @pytest.mark.asyncio
    async def test_second_attempt_timeout(self) -> None:
        """Timeout on retry → False."""
        vc, _, _ = _make_vc(listen_responses=["hmm", None])
        assert await vc.confirm("delete file") is False

    @pytest.mark.asyncio
    async def test_second_attempt_ambiguous(self) -> None:
        """Ambiguous on both attempts → False."""
        vc, _, _ = _make_vc(listen_responses=["what?", "umm"])
        assert await vc.confirm("delete file") is False

    # -- language tests ----------------------------------------------------

    @pytest.mark.asyncio
    async def test_confirm_speaks_english(self) -> None:
        vc, speak, _ = _make_vc(listen_responses=["yes"])
        await vc.confirm("delete old_config.py", language="en")
        question = speak.call_args_list[0][0][0]
        assert "May I proceed" in question
        assert "delete old_config.py" in question

    @pytest.mark.asyncio
    async def test_confirm_speaks_russian(self) -> None:
        vc, speak, _ = _make_vc(listen_responses=["да"])
        await vc.confirm("удалить old_config.py", language="ru")
        question = speak.call_args_list[0][0][0]
        assert "Подтвердите" in question
        assert "удалить old_config.py" in question

    @pytest.mark.asyncio
    async def test_confirm_speaks_ukrainian(self) -> None:
        vc, speak, _ = _make_vc(listen_responses=["так"])
        await vc.confirm("видалити old_config.py", language="uk")
        question = speak.call_args_list[0][0][0]
        assert "Підтвердіть" in question
        assert "видалити old_config.py" in question

    @pytest.mark.asyncio
    async def test_retry_prompt_english(self) -> None:
        vc, speak, _ = _make_vc(listen_responses=["hmm", "yes"])
        await vc.confirm("delete file", language="en")
        retry = speak.call_args_list[1][0][0]
        assert "yes" in retry.lower() and "no" in retry.lower()

    @pytest.mark.asyncio
    async def test_retry_prompt_russian(self) -> None:
        vc, speak, _ = _make_vc(listen_responses=["что?", "да"])
        await vc.confirm("удалить файл", language="ru")
        retry = speak.call_args_list[1][0][0]
        assert "да" in retry and "нет" in retry

    @pytest.mark.asyncio
    async def test_retry_prompt_ukrainian(self) -> None:
        vc, speak, _ = _make_vc(listen_responses=["що?", "так"])
        await vc.confirm("видалити файл", language="uk")
        retry = speak.call_args_list[1][0][0]
        assert "так" in retry and "ні" in retry

    # -- enabled / disabled ------------------------------------------------

    @pytest.mark.asyncio
    async def test_disabled_returns_true(self) -> None:
        """When speak_fn or listen_fn is None, confirm() returns True."""
        vc = VoiceConfirm(speak_fn=None, listen_fn=None)
        assert vc.enabled is False
        assert await vc.confirm("delete everything") is True

    @pytest.mark.asyncio
    async def test_enabled_property(self) -> None:
        vc, _, _ = _make_vc(listen_responses=["yes"])
        assert vc.enabled is True

    @pytest.mark.asyncio
    async def test_disabled_speak_only(self) -> None:
        vc = VoiceConfirm(speak_fn=AsyncMock(), listen_fn=None)
        assert vc.enabled is False
        assert await vc.confirm("delete everything") is True

    @pytest.mark.asyncio
    async def test_disabled_listen_only(self) -> None:
        vc = VoiceConfirm(speak_fn=None, listen_fn=AsyncMock())
        assert vc.enabled is False
        assert await vc.confirm("delete everything") is True


# ---------------------------------------------------------------------------
# CodingAgent integration tests
# ---------------------------------------------------------------------------

class _FakeChatResponse:
    """Mimics ``ChatResponse`` from model_provider."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.finish_reason = "stop"
        self.model = "fake-model"
        self.usage: dict[str, int] = {}


class FakeProvider:
    """A mock ModelProvider that returns pre-programmed responses."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or [])
        self._call_index = 0

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096):
        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
        else:
            resp = "No more responses configured."
        return _FakeChatResponse(resp)


class _FakeCodingConfig:
    """Mimics ``CodingConfig`` fields used by CodingAgent."""

    def __init__(
        self,
        *,
        max_iterations: int = 3,
        auto_commit: bool = False,
        default_workspace: str = "",
        provider: str = "main",
    ) -> None:
        self.max_iterations = max_iterations
        self.auto_commit = auto_commit
        self.default_workspace = default_workspace
        self.provider = provider


def _make_agent(
    tmp_path: Path,
    responses: list[str] | None = None,
    *,
    confirm: VoiceConfirm | None = None,
    auto_commit: bool = False,
):
    """Build a CodingAgent with a FakeProvider and optional VoiceConfirm."""
    from jarvis.coding.agent import CodingAgent

    provider = FakeProvider(responses=responses)
    config = _FakeCodingConfig(auto_commit=auto_commit)
    agent = CodingAgent(
        provider=provider,
        workspace=tmp_path,
        config=config,
        confirm=confirm,
    )
    return agent, provider


class TestAgentVoiceConfirmIntegration:
    """Test that CodingAgent respects VoiceConfirm for destructive actions."""

    @pytest.mark.asyncio
    async def test_agent_skips_overwrite_on_reject(self, tmp_path: Path) -> None:
        """When a file exists and user says 'no', _create_file skips."""
        # Pre-create the file.
        (tmp_path / "config.py").write_text("original = True\n")

        vc, speak, listen = _make_vc(listen_responses=["no"])
        agent, _ = _make_agent(
            tmp_path,
            responses=['```filepath: config.py\noriginal = False\n```'],
            confirm=vc,
        )
        result = await agent._create_file("config.py", "overwrite config")
        assert "Skipped" in result
        assert "not confirmed" in result
        # File content should be unchanged.
        assert (tmp_path / "config.py").read_text() == "original = True\n"

    @pytest.mark.asyncio
    async def test_agent_proceeds_overwrite_on_confirm(self, tmp_path: Path) -> None:
        """When a file exists and user says 'yes', _create_file proceeds."""
        (tmp_path / "config.py").write_text("original = True\n")

        vc, _, _ = _make_vc(listen_responses=["yes"])
        agent, _ = _make_agent(
            tmp_path,
            responses=['```filepath: config.py\noriginal = False\n```'],
            confirm=vc,
        )
        result = await agent._create_file("config.py", "overwrite config")
        assert "Created config.py" in result
        assert "False" in (tmp_path / "config.py").read_text()

    @pytest.mark.asyncio
    async def test_agent_no_confirm_new_file(self, tmp_path: Path) -> None:
        """Creating a NEW file does not trigger confirmation."""
        vc, speak, _ = _make_vc(listen_responses=["yes"])
        agent, _ = _make_agent(
            tmp_path,
            responses=['```filepath: brand_new.py\nx = 1\n```'],
            confirm=vc,
        )
        result = await agent._create_file("brand_new.py", "new file")
        assert "Created brand_new.py" in result
        # speak should NOT have been called (no overwrite).
        speak.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_agent_skips_commit_on_reject(self, tmp_path: Path) -> None:
        """git commit is skipped when user says 'no'."""
        vc, _, _ = _make_vc(listen_responses=["нет"])
        agent, _ = _make_agent(tmp_path, confirm=vc)
        result = await agent._git_operation("commit", message="test commit")
        assert "Skipped" in result
        assert "not confirmed" in result

    @pytest.mark.asyncio
    async def test_agent_skips_push_on_reject(self, tmp_path: Path) -> None:
        """git push is skipped when user says 'ні'."""
        vc, _, _ = _make_vc(listen_responses=["ні"])
        agent, _ = _make_agent(tmp_path, confirm=vc)
        result = await agent._git_operation("push")
        assert "Skipped" in result

    @requires_git
    @pytest.mark.asyncio
    async def test_agent_proceeds_commit_on_confirm(self, tmp_path: Path) -> None:
        """git commit proceeds when user confirms."""
        import subprocess

        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path), capture_output=True,
        )
        (tmp_path / "readme.txt").write_text("hello")

        vc, _, _ = _make_vc(listen_responses=["да"])
        agent, _ = _make_agent(tmp_path, confirm=vc)
        result = await agent._git_operation("commit", message="initial")
        # Should proceed — not skipped.
        assert "Skipped" not in result

    @pytest.mark.asyncio
    async def test_agent_git_init_no_confirm(self, tmp_path: Path) -> None:
        """git init does NOT require confirmation."""
        vc, speak, _ = _make_vc(listen_responses=["yes"])
        agent, _ = _make_agent(tmp_path, confirm=vc)
        result = await agent._git_operation("init")
        # speak should not be called for init.
        speak.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_agent_run_command_rejected(self, tmp_path: Path) -> None:
        """Arbitrary shell command is skipped when user says 'no'."""
        vc, _, _ = _make_vc(listen_responses=["cancel"])
        agent, _ = _make_agent(tmp_path, confirm=vc)
        stdout, stderr, rc = await agent._run_command("rm -rf /tmp/test")
        assert rc == 1
        assert "not confirmed" in stderr.lower()

    @pytest.mark.asyncio
    async def test_agent_run_command_proceeds(self, tmp_path: Path) -> None:
        """Shell command proceeds when user confirms."""
        vc, _, _ = _make_vc(listen_responses=["yes"])
        agent, _ = _make_agent(tmp_path, confirm=vc)
        stdout, stderr, rc = await agent._run_command("echo hello")
        # echo should succeed.
        assert rc == 0 or "not confirmed" not in stderr

    @requires_git
    @pytest.mark.asyncio
    async def test_agent_no_confirm_means_no_blocking(self, tmp_path: Path) -> None:
        """When confirm=None, no confirmation is asked — backward compat."""
        agent, _ = _make_agent(tmp_path, confirm=None)
        # git init should work without any confirmation prompt.
        result = await agent._git_operation("init")
        assert "init" in result.lower() or "OK" in result

    @pytest.mark.asyncio
    async def test_agent_confirm_timeout_blocks_overwrite(
        self, tmp_path: Path,
    ) -> None:
        """Timeout (None from listen) → file overwrite is blocked."""
        (tmp_path / "app.py").write_text("x = 1\n")

        vc, _, _ = _make_vc(listen_responses=[None])
        agent, _ = _make_agent(
            tmp_path,
            responses=['```filepath: app.py\nx = 2\n```'],
            confirm=vc,
        )
        result = await agent._create_file("app.py", "update")
        assert "Skipped" in result
        assert (tmp_path / "app.py").read_text() == "x = 1\n"
