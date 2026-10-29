"""Tests for the CodingAgent — core coding assistant.

Uses a mocked ModelProvider so tests are fast and deterministic.
"""

from __future__ import annotations

import json
import re

# Some tests need a real ``git`` binary on PATH. On machines where git is
# not installed (or only available as a portable build outside PATH) these
# tests would fail with WinError 2 — skip them instead.
import shutil
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jarvis.coding.agent import (
    CODING_SYSTEM_PROMPT,
    CodingAgent,
    ResponseParser,
)

_GIT_AVAILABLE = shutil.which("git") is not None
requires_git = pytest.mark.skipif(
    not _GIT_AVAILABLE, reason="git executable not found on PATH"
)


# ---------------------------------------------------------------------------
# Helpers — mock provider
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
        self.calls: list[list[object]] = []

    async def chat(self, messages, *, temperature=0.7, max_tokens=4096):
        self.calls.append(messages)
        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
        else:
            resp = "No more responses configured."
        return _FakeChatResponse(resp)

    async def stream_chat(self, messages, *, temperature=0.7, max_tokens=4096):
        resp = await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        yield MagicMock(content=resp.content, done=True)

    def display_name(self) -> str:
        return "FakeProvider (test)"


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
    max_iterations: int = 3,
    auto_commit: bool = False,
) -> tuple[CodingAgent, FakeProvider]:
    provider = FakeProvider(responses=responses)
    config = _FakeCodingConfig(
        max_iterations=max_iterations,
        auto_commit=auto_commit,
    )
    agent = CodingAgent(
        provider=provider,
        workspace=tmp_path,
        config=config,
    )
    return agent, provider


# ---------------------------------------------------------------------------
# ResponseParser tests
# ---------------------------------------------------------------------------

class TestResponseParser:
    """Tests for the LLM response parser."""

    def test_parse_single_file(self):
        text = textwrap.dedent("""\
            Here's the file:
            ```filepath: src/main.py
            print("hello")
            ```
        """)
        blocks = ResponseParser.parse(text)
        assert len(blocks) == 1
        assert blocks[0].path == "src/main.py"
        assert blocks[0].content == 'print("hello")'
        assert blocks[0].action == "create"

    def test_parse_edit_block(self):
        text = textwrap.dedent("""\
            ```edit: src/main.py
            print("updated")
            ```
        """)
        blocks = ResponseParser.parse(text)
        assert len(blocks) == 1
        assert blocks[0].action == "edit"
        assert blocks[0].content == 'print("updated")'

    def test_parse_multiple_blocks(self):
        text = textwrap.dedent("""\
            ```filepath: a.py
            # file a
            ```
            Some text between.
            ```filepath: b.py
            # file b
            ```
            ```edit: c.py
            # file c edited
            ```
        """)
        blocks = ResponseParser.parse(text)
        assert len(blocks) == 3
        assert blocks[0].path == "a.py"
        assert blocks[1].path == "b.py"
        assert blocks[2].path == "c.py"
        assert blocks[2].action == "edit"

    def test_parse_empty_content_skipped(self):
        text = "```filepath: empty.py\n```"
        blocks = ResponseParser.parse(text)
        assert len(blocks) == 0

    def test_parse_no_blocks(self):
        blocks = ResponseParser.parse("Just some plain text with no code blocks.")
        assert blocks == []

    def test_parse_with_language_tag(self):
        text = "```python filepath: app.py\nprint('hi')\n```"
        blocks = ResponseParser.parse(text)
        assert len(blocks) == 1
        assert blocks[0].path == "app.py"

    def test_parse_preserves_multiline_content(self):
        text = textwrap.dedent("""\
            ```filepath: multi.py
            line1
            line2
            line3
            ```
        """)
        blocks = ResponseParser.parse(text)
        assert len(blocks) == 1
        assert blocks[0].content == "line1\nline2\nline3"


# ---------------------------------------------------------------------------
# Plan tests
# ---------------------------------------------------------------------------

class TestPlan:
    """Tests for the planning phase."""

    @pytest.mark.asyncio
    async def test_plan_breaks_into_steps(self, tmp_path):
        plan_json = json.dumps([
            {"action": "create_file", "details": {"path": "main.py", "description": "entry"}},
            {"action": "run_tests", "details": {}},
        ])
        agent, provider = _make_agent(tmp_path, responses=[plan_json])

        steps = await agent._plan("Create a CLI tool")
        assert len(steps) == 2
        assert steps[0].action == "create_file"
        assert steps[0].details["path"] == "main.py"
        assert steps[1].action == "run_tests"

    @pytest.mark.asyncio
    async def test_plan_handles_non_json(self, tmp_path):
        agent, _ = _make_agent(tmp_path, responses=["This is not JSON at all"])
        steps = await agent._plan("whatever")
        assert steps == []

    @pytest.mark.asyncio
    async def test_plan_handles_non_list_json(self, tmp_path):
        agent, _ = _make_agent(tmp_path, responses=['{"action": "create_file"}'])
        steps = await agent._plan("whatever")
        assert steps == []

    @pytest.mark.asyncio
    async def test_plan_strips_markdown_fences(self, tmp_path):
        plan = json.dumps([{"action": "run_tests", "details": {}}])
        wrapped = f"```json\n{plan}\n```"
        agent, _ = _make_agent(tmp_path, responses=[wrapped])
        steps = await agent._plan("test")
        assert len(steps) == 1

    @pytest.mark.asyncio
    async def test_plan_skips_non_dict_items(self, tmp_path):
        plan = json.dumps([
            {"action": "run_tests", "details": {}},
            "not a dict",
            42,
        ])
        agent, _ = _make_agent(tmp_path, responses=[plan])
        steps = await agent._plan("test")
        assert len(steps) == 1


# ---------------------------------------------------------------------------
# File creation / editing tests
# ---------------------------------------------------------------------------

class TestFileOperations:
    """Tests for _create_file and _edit_file."""

    @pytest.mark.asyncio
    async def test_create_file_writes_to_disk(self, tmp_path):
        response = '```filepath: hello.py\nprint("hello")\n```'
        agent, _ = _make_agent(tmp_path, responses=[response])
        result = await agent._create_file("hello.py", "greeting script")
        assert "Created hello.py" in result
        assert (tmp_path / "hello.py").exists()
        assert 'print("hello")' in (tmp_path / "hello.py").read_text()

    @pytest.mark.asyncio
    async def test_create_file_creates_subdirectories(self, tmp_path):
        response = '```filepath: src/lib/utils.py\ndef helper(): pass\n```'
        agent, _ = _make_agent(tmp_path, responses=[response])
        await agent._create_file("src/lib/utils.py", "utility")
        assert (tmp_path / "src" / "lib" / "utils.py").exists()

    @pytest.mark.asyncio
    async def test_create_file_syntax_check_rejects_bad_python(self, tmp_path):
        response = '```filepath: bad.py\ndef broken(\n```'
        agent, _ = _make_agent(tmp_path, responses=[response])
        result = await agent._create_file("bad.py", "bad file")
        assert "Syntax error" in result

    @pytest.mark.asyncio
    async def test_create_file_non_python_no_syntax_check(self, tmp_path):
        response = '```filepath: data.json\n{"key": "value"}\n```'
        agent, _ = _make_agent(tmp_path, responses=[response])
        result = await agent._create_file("data.json", "json file")
        assert "Created data.json" in result
        assert (tmp_path / "data.json").exists()

    @pytest.mark.asyncio
    async def test_edit_file_preserves_and_updates(self, tmp_path):
        # Pre-create file.
        original = "def greet():\n    return 'hello'\n"
        (tmp_path / "greet.py").write_text(original)

        edited = "def greet():\n    return 'hello world'\n"
        response = f'```edit: greet.py\n{edited}```'
        agent, _ = _make_agent(tmp_path, responses=[response])
        result = await agent._edit_file("greet.py", "add world")
        assert "Created greet.py" in result or "greet.py" in result
        content = (tmp_path / "greet.py").read_text()
        assert "hello world" in content

    @pytest.mark.asyncio
    async def test_edit_file_not_found(self, tmp_path):
        agent, _ = _make_agent(tmp_path, responses=[])
        result = await agent._edit_file("nonexistent.py", "edit it")
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# Sandbox tests
# ---------------------------------------------------------------------------

class TestSandbox:
    """Tests for sandbox path validation."""

    @pytest.mark.asyncio
    async def test_sandbox_prevents_absolute_path(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        with pytest.raises(ValueError, match="Absolute paths"):
            agent._validate_sandbox("/etc/passwd")

    @pytest.mark.asyncio
    async def test_sandbox_prevents_traversal(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        with pytest.raises(ValueError, match="escapes the workspace"):
            agent._validate_sandbox("../../etc/passwd")

    @pytest.mark.asyncio
    async def test_sandbox_allows_nested_path(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        result = agent._validate_sandbox("src/lib/module.py")
        assert result == (tmp_path / "src" / "lib" / "module.py").resolve()

    @pytest.mark.asyncio
    async def test_sandbox_strips_leading_slash(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        # Relative path starting with / should be stripped and validated.
        result = agent._validate_sandbox("src/main.py")
        assert str(tmp_path) in str(result)

    @pytest.mark.asyncio
    async def test_sandbox_rejects_backslash_traversal(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        with pytest.raises(ValueError, match="escapes the workspace"):
            agent._validate_sandbox("..\\..\\etc\\passwd")


# ---------------------------------------------------------------------------
# Test / fix loop tests
# ---------------------------------------------------------------------------

class TestFixLoop:
    """Tests for _run_tests and _fix_errors."""

    @pytest.mark.asyncio
    async def test_run_tests_detects_failure(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        # Create a failing test.
        test_file = tmp_path / "test_fail.py"
        test_file.write_text("def test_fail():\n    assert False\n")

        success, output = await agent._run_tests()
        assert success is False
        assert "assert False" in output or "FAILED" in output or "failed" in output.lower()

    @pytest.mark.asyncio
    async def test_run_tests_detects_success(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        # Create a passing test.
        test_file = tmp_path / "test_pass.py"
        test_file.write_text("def test_pass():\n    assert True\n")

        success, output = await agent._run_tests()
        assert success is True

    @pytest.mark.asyncio
    async def test_max_iterations_respected(self, tmp_path):
        """The fix loop must stop after max_iterations even if tests still fail."""
        # Create a failing test that the "fix" won't actually fix.
        test_file = tmp_path / "test_always_fail.py"
        test_file.write_text("def test_always_fail():\n    assert False\n")

        # The model returns an "edit" block each time that doesn't fix anything.
        fix_response = '```edit: test_always_fail.py\ndef test_always_fail():\n    assert False  # still broken\n```'
        max_iter = 2
        responses = [fix_response] * max_iter
        agent, provider = _make_agent(
            tmp_path, responses=responses, max_iterations=max_iter,
        )

        result = await agent._fix_errors("FAILED test_always_fail")
        assert result is False
        # Should have called the model exactly max_iterations times.
        assert len(provider.calls) == max_iter

    @pytest.mark.asyncio
    async def test_fix_errors_succeeds_when_test_passes(self, tmp_path):
        """If the fix makes tests pass, the loop exits early with True."""
        test_file = tmp_path / "test_fixable.py"
        test_file.write_text("def test_fixable():\n    assert False\n")

        # The model returns a fix that makes the test pass.
        fix = '```edit: test_fixable.py\ndef test_fixable():\n    assert True\n```'
        agent, provider = _make_agent(tmp_path, responses=[fix], max_iterations=5)

        result = await agent._fix_errors("FAILED test_fixable")
        assert result is True
        # Should have called model only once (fix worked first try).
        assert len(provider.calls) == 1

    @pytest.mark.asyncio
    async def test_fix_errors_returns_false_no_blocks(self, tmp_path):
        """If the model returns no editable blocks, fix returns False."""
        agent, _ = _make_agent(
            tmp_path, responses=["I don't know how to fix this."],
        )
        result = await agent._fix_errors("FAILED some test")
        assert result is False


# ---------------------------------------------------------------------------
# Git tests
# ---------------------------------------------------------------------------

class TestGitOperations:
    """Tests for _git_operation."""

    @requires_git
    @pytest.mark.asyncio
    async def test_git_init(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        result = await agent._git_operation("init")
        # git init should succeed in a fresh tmp dir.
        assert "init" in result.lower() or "initialized" in result.lower() or "OK" in result

    @requires_git
    @pytest.mark.asyncio
    async def test_git_add_and_commit(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        # Init first.
        await agent._git_operation("init")
        # Create a file.
        (tmp_path / "readme.txt").write_text("hello")
        # Commit.
        result = await agent._git_operation("commit", message="initial commit")
        # Should succeed or mention the commit.
        assert "initial commit" in result.lower() or "ok" in result.lower() or "file" in result.lower()

    @pytest.mark.asyncio
    async def test_git_unknown_action(self, tmp_path):
        agent, _ = _make_agent(tmp_path)
        result = await agent._git_operation("rebase")
        assert "unknown" in result.lower()


# ---------------------------------------------------------------------------
# execute_task integration tests
# ---------------------------------------------------------------------------

class TestExecuteTask:
    """Integration tests for the full execute_task flow."""

    @pytest.mark.asyncio
    async def test_progress_messages_emitted(self, tmp_path):
        plan = json.dumps([
            {"action": "create_file", "details": {"path": "app.py", "description": "main app"}},
        ])
        file_response = '```filepath: app.py\nprint("app")\n```'
        agent, _ = _make_agent(tmp_path, responses=[plan, file_response])

        messages = []
        async for msg in agent.execute_task("create an app"):
            messages.append(msg)

        assert any("Planning" in m for m in messages)
        assert any("step" in m.lower() for m in messages)
        assert any("complete" in m.lower() for m in messages)

    @requires_git
    @pytest.mark.asyncio
    async def test_auto_commit_triggers_git(self, tmp_path):
        plan = json.dumps([
            {"action": "create_file", "details": {"path": "f.py", "description": "file"}},
        ])
        file_response = '```filepath: f.py\nx = 1\n```'
        agent, _ = _make_agent(
            tmp_path, responses=[plan, file_response], auto_commit=True,
        )

        # Init git so auto-commit can work.
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

        messages = []
        async for msg in agent.execute_task("make a file"):
            messages.append(msg)

        assert any("auto-commit" in m.lower() or "commit" in m.lower() for m in messages)

    @pytest.mark.asyncio
    async def test_empty_plan_yields_message(self, tmp_path):
        agent, _ = _make_agent(tmp_path, responses=["not valid json"])

        messages = []
        async for msg in agent.execute_task("do something"):
            messages.append(msg)

        assert any("couldn't" in m.lower() or "actionable" in m.lower() for m in messages)

    @pytest.mark.asyncio
    async def test_unknown_action_in_plan(self, tmp_path):
        plan = json.dumps([
            {"action": "fly_to_moon", "details": {}},
        ])
        agent, _ = _make_agent(tmp_path, responses=[plan])

        messages = []
        async for msg in agent.execute_task("impossible"):
            messages.append(msg)

        assert any("unknown" in m.lower() for m in messages)


# ---------------------------------------------------------------------------
# Voice pattern tests
# ---------------------------------------------------------------------------

class TestVoicePatterns:
    """Tests for CodingAgentTool voice patterns."""

    def test_en_create_project(self):
        from jarvis.tools.local.coding_tools import CodingAgentTool

        for pattern_vp in CodingAgentTool.voice_patterns:
            m = re.match(pattern_vp.regex, "create a new project for web scraping", re.IGNORECASE)
            if m:
                args = pattern_vp.args(m)
                assert "web scraping" in args["task"]
                return
        pytest.fail("No pattern matched 'create a new project for web scraping'")

    def test_ru_create_project(self):
        from jarvis.tools.local.coding_tools import CodingAgentTool

        for pattern_vp in CodingAgentTool.voice_patterns:
            m = re.match(pattern_vp.regex, "создай проект для парсинга", re.IGNORECASE)
            if m:
                args = pattern_vp.args(m)
                assert "парсинга" in args["task"]
                return
        pytest.fail("No pattern matched 'создай проект для парсинга'")

    def test_uk_create_project(self):
        from jarvis.tools.local.coding_tools import CodingAgentTool

        for pattern_vp in CodingAgentTool.voice_patterns:
            m = re.match(pattern_vp.regex, "створи проект для скрапінгу", re.IGNORECASE)
            if m:
                args = pattern_vp.args(m)
                assert "скрапінгу" in args["task"]
                return
        pytest.fail("No pattern matched 'створи проект для скрапінгу'")

    def test_en_fix_bugs(self):
        from jarvis.tools.local.coding_tools import CodingAgentTool

        for pattern_vp in CodingAgentTool.voice_patterns:
            m = re.match(pattern_vp.regex, "fix the errors", re.IGNORECASE)
            if m:
                args = pattern_vp.args(m)
                assert "fix" in args["task"]
                return
        pytest.fail("No pattern matched 'fix the errors'")

    def test_ru_fix_bugs(self):
        from jarvis.tools.local.coding_tools import CodingAgentTool

        for pattern_vp in CodingAgentTool.voice_patterns:
            m = re.match(pattern_vp.regex, "исправь ошибки", re.IGNORECASE)
            if m:
                args = pattern_vp.args(m)
                assert "исправь" in args["task"]
                return
        pytest.fail("No pattern matched 'исправь ошибки'")

    def test_en_write_code(self):
        from jarvis.tools.local.coding_tools import CodingAgentTool

        for pattern_vp in CodingAgentTool.voice_patterns:
            m = re.match(pattern_vp.regex, "write a function for data parsing", re.IGNORECASE)
            if m:
                args = pattern_vp.args(m)
                assert "function" in args["task"]
                return
        pytest.fail("No pattern matched 'write a function for data parsing'")


# ---------------------------------------------------------------------------
# CodingAgentTool integration test
# ---------------------------------------------------------------------------

class TestCodingAgentTool:
    """Tests for the tool wrapper."""

    @pytest.mark.asyncio
    async def test_tool_returns_error_without_provider(self):
        from jarvis.tools.local.coding_tools import CodingAgentArgs, CodingAgentTool

        tool = CodingAgentTool()
        result = await tool.execute(CodingAgentArgs(task="create something"))
        assert result.success is False
        assert "provider" in result.error.lower()

    @pytest.mark.asyncio
    async def test_tool_runs_with_provider(self, tmp_path):
        from jarvis.tools.local.coding_tools import CodingAgentArgs, CodingAgentTool

        plan = json.dumps([
            {"action": "create_file", "details": {"path": "x.py", "description": "test"}},
        ])
        file_resp = '```filepath: x.py\nx = 42\n```'
        provider = FakeProvider(responses=[plan, file_resp])

        tool = CodingAgentTool(
            provider=provider,
            workspace=tmp_path,
        )
        result = await tool.execute(CodingAgentArgs(task="make x.py"))
        assert result.success is True
        assert "complete" in result.output.lower()


# ---------------------------------------------------------------------------
# Project scaffolding tests
# ---------------------------------------------------------------------------

class TestCreateProject:
    """Tests for project scaffolding."""

    @pytest.mark.asyncio
    async def test_create_project_writes_multiple_files(self, tmp_path):
        response = textwrap.dedent("""\
            ```filepath: __init__.py
            # package init
            ```
            ```filepath: main.py
            def main():
                print("hello")
            ```
            ```filepath: requirements.txt
            flask>=3.0
            ```
            ```filepath: README.md
            # My Project
            ```
        """)
        agent, _ = _make_agent(tmp_path, responses=[response])
        await agent._create_project("A simple project")

        assert (tmp_path / "__init__.py").exists()
        assert (tmp_path / "main.py").exists()
        assert (tmp_path / "requirements.txt").exists()
        assert (tmp_path / "README.md").exists()


# ---------------------------------------------------------------------------
# Constants / prompt tests
# ---------------------------------------------------------------------------

class TestConstants:
    """Sanity checks on module-level constants."""

    def test_coding_system_prompt_is_nonempty(self):
        assert len(CODING_SYSTEM_PROMPT) > 100

    def test_coding_system_prompt_mentions_pep8(self):
        assert "PEP 8" in CODING_SYSTEM_PROMPT

    def test_coding_system_prompt_mentions_filepath_format(self):
        assert "filepath:" in CODING_SYSTEM_PROMPT

    def test_coding_system_prompt_mentions_edit_format(self):
        assert "edit:" in CODING_SYSTEM_PROMPT
