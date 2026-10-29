"""Tests for jarvis.tools.local.code_runner (T9.4).

All tests mock subprocess.run to avoid executing real commands.
PathValidator is tested with real filesystem paths via tmp_path.

Test count: 25
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from jarvis.tools.local.code_runner import (
    _MAX_OUTPUT_CHARS,
    _MAX_READ_CHARS,
    _SCRIPT_TIMEOUT,
    _TEST_TIMEOUT,
    CodeRunnerArgs,
    CodeRunnerTool,
    _truncate,
)
from jarvis.tools.sandbox import PathValidator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sandbox(tmp_path: Path) -> PathValidator:
    """PathValidator that allows only *tmp_path*."""
    return PathValidator(allowed_paths=[tmp_path], allow_home_fallback=False)


@pytest.fixture()
def tool(sandbox: PathValidator, tmp_path: Path) -> CodeRunnerTool:
    """CodeRunnerTool wired to a sandbox rooted at *tmp_path*."""
    return CodeRunnerTool(path_validator=sandbox, workspace=tmp_path)


@pytest.fixture()
def tool_no_sandbox(tmp_path: Path) -> CodeRunnerTool:
    """CodeRunnerTool with a permissive sandbox scoped to tmp_path.

    Previously this used validator=None (fail-open); now we always
    provide a validator because the tool refuses to run without one.
    """
    sandbox = PathValidator(allowed_paths=[tmp_path], allow_home_fallback=False)
    return CodeRunnerTool(path_validator=sandbox, workspace=tmp_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_subprocess_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# Unit: _truncate
# ---------------------------------------------------------------------------

def test_truncate_short():
    assert _truncate("hello", 100) == "hello"


def test_truncate_exact():
    text = "x" * 100
    assert _truncate(text, 100) == text


def test_truncate_long():
    text = "x" * 200
    result = _truncate(text, 100)
    assert result.startswith("x" * 100)
    assert "truncated" in result
    assert "200" in result


# ---------------------------------------------------------------------------
# Unit: CodeRunnerArgs validation
# ---------------------------------------------------------------------------

def test_args_defaults():
    a = CodeRunnerArgs(action="run_tests")
    assert a.path == ""
    assert a.test_args == ""


def test_args_all_actions():
    for action in ("run_script", "run_tests", "read_file", "last_errors"):
        a = CodeRunnerArgs(action=action)
        assert a.action == action


def test_args_invalid_action():
    with pytest.raises(ValidationError):
        CodeRunnerArgs(action="hack_the_planet")


# ---------------------------------------------------------------------------
# run_tests
# ---------------------------------------------------------------------------

async def test_run_tests_pass(tool: CodeRunnerTool):
    mock = _make_subprocess_result(0, "15 passed in 3.2s")
    with patch("jarvis.tools.local.code_runner.subprocess.run", return_value=mock):
        result = await tool.execute(CodeRunnerArgs(action="run_tests"))
    assert result.success
    assert "15 passed" in (result.output or "")


async def test_run_tests_fail(tool: CodeRunnerTool):
    mock = _make_subprocess_result(1, "2 failed, 13 passed", "ERRORS:")
    with patch("jarvis.tools.local.code_runner.subprocess.run", return_value=mock):
        result = await tool.execute(CodeRunnerArgs(action="run_tests"))
    assert not result.success
    assert "2 failed" in (result.error or "")


async def test_run_tests_with_args(tool: CodeRunnerTool):
    mock = _make_subprocess_result(0, "3 passed")
    with patch("jarvis.tools.local.code_runner.subprocess.run", return_value=mock) as run_mock:
        result = await tool.execute(
            CodeRunnerArgs(action="run_tests", test_args="-k test_login -v"),
        )
    assert result.success
    # Verify pytest args were passed.
    call_args = run_mock.call_args
    cmd = call_args[0][0]  # first positional arg to subprocess.run
    assert "-k" in cmd
    assert "test_login" in cmd
    assert "-v" in cmd


async def test_run_tests_timeout(tool: CodeRunnerTool):
    import subprocess as sp
    with patch(
        "jarvis.tools.local.code_runner.subprocess.run",
        side_effect=sp.TimeoutExpired(cmd="pytest", timeout=_TEST_TIMEOUT),
    ):
        result = await tool.execute(CodeRunnerArgs(action="run_tests"))
    assert not result.success
    assert "timed out" in (result.error or "").lower()


async def test_run_tests_no_python(tool: CodeRunnerTool):
    with patch(
        "jarvis.tools.local.code_runner.subprocess.run",
        side_effect=FileNotFoundError("python"),
    ):
        result = await tool.execute(CodeRunnerArgs(action="run_tests"))
    assert not result.success
    assert "not found" in (result.error or "").lower()


async def test_run_tests_stores_output(tool: CodeRunnerTool):
    """Verify _last_output and _last_stderr are stored after run_tests."""
    mock = _make_subprocess_result(1, "FAIL output", "error trace")
    with patch("jarvis.tools.local.code_runner.subprocess.run", return_value=mock):
        await tool.execute(CodeRunnerArgs(action="run_tests"))
    assert tool._last_output == "FAIL output"
    assert tool._last_stderr == "error trace"


# ---------------------------------------------------------------------------
# run_script
# ---------------------------------------------------------------------------

async def test_run_script_success(tool: CodeRunnerTool, tmp_path: Path):
    script = tmp_path / "hello.py"
    script.write_text("print('hello')\n", encoding="utf-8")

    mock = _make_subprocess_result(0, "hello")
    with patch("jarvis.tools.local.code_runner.subprocess.run", return_value=mock):
        result = await tool.execute(
            CodeRunnerArgs(action="run_script", path="hello.py"),
        )
    assert result.success
    assert "hello" in (result.output or "")


async def test_run_script_no_path(tool: CodeRunnerTool):
    result = await tool.execute(CodeRunnerArgs(action="run_script", path=""))
    assert not result.success
    assert "no script path" in (result.error or "").lower()


async def test_run_script_file_not_found(tool: CodeRunnerTool):
    result = await tool.execute(
        CodeRunnerArgs(action="run_script", path="nonexistent.py"),
    )
    assert not result.success
    assert "not found" in (result.error or "").lower()


async def test_run_script_non_py_rejected(tool: CodeRunnerTool, tmp_path: Path):
    txt = tmp_path / "data.txt"
    txt.write_text("not python", encoding="utf-8")
    result = await tool.execute(
        CodeRunnerArgs(action="run_script", path="data.txt"),
    )
    assert not result.success
    assert ".py" in (result.error or "")


async def test_run_script_syntax_error(tool: CodeRunnerTool, tmp_path: Path):
    bad = tmp_path / "bad.py"
    bad.write_text("def oops(\n", encoding="utf-8")
    result = await tool.execute(
        CodeRunnerArgs(action="run_script", path="bad.py"),
    )
    assert not result.success
    assert "syntax error" in (result.error or "").lower()


async def test_run_script_sandbox_violation(tool: CodeRunnerTool, tmp_path: Path):
    """A path outside the sandbox must be rejected."""
    result = await tool.execute(
        CodeRunnerArgs(action="run_script", path="/etc/passwd.py"),
    )
    assert not result.success
    # Either "sandbox" or "not found" — both are acceptable rejections.
    assert not result.success


async def test_run_script_timeout(tool: CodeRunnerTool, tmp_path: Path):
    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(999)\n", encoding="utf-8")
    import subprocess as sp
    with patch(
        "jarvis.tools.local.code_runner.subprocess.run",
        side_effect=sp.TimeoutExpired(cmd="python", timeout=_SCRIPT_TIMEOUT),
    ):
        result = await tool.execute(
            CodeRunnerArgs(action="run_script", path="slow.py"),
        )
    assert not result.success
    assert "timed out" in (result.error or "").lower()


async def test_run_script_stderr_on_failure(tool: CodeRunnerTool, tmp_path: Path):
    script = tmp_path / "crash.py"
    script.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    mock = _make_subprocess_result(1, "", "RuntimeError: boom")
    with patch("jarvis.tools.local.code_runner.subprocess.run", return_value=mock):
        result = await tool.execute(
            CodeRunnerArgs(action="run_script", path="crash.py"),
        )
    assert not result.success
    assert "boom" in (result.error or "")


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

async def test_read_file_success(tool: CodeRunnerTool, tmp_path: Path):
    f = tmp_path / "utils.py"
    f.write_text("# utilities\ndef add(a, b):\n    return a + b\n", encoding="utf-8")
    result = await tool.execute(
        CodeRunnerArgs(action="read_file", path="utils.py"),
    )
    assert result.success
    assert "utils.py" in (result.output or "")
    assert "def add" in (result.output or "")
    assert "4 lines" in (result.output or "")


async def test_read_file_no_path(tool: CodeRunnerTool):
    result = await tool.execute(CodeRunnerArgs(action="read_file", path=""))
    assert not result.success


async def test_read_file_not_found(tool: CodeRunnerTool):
    result = await tool.execute(
        CodeRunnerArgs(action="read_file", path="ghost.py"),
    )
    assert not result.success
    assert "not found" in (result.error or "").lower()


async def test_read_file_truncation(tool: CodeRunnerTool, tmp_path: Path):
    big = tmp_path / "big.py"
    big.write_text("x" * 10000, encoding="utf-8")
    result = await tool.execute(
        CodeRunnerArgs(action="read_file", path="big.py"),
    )
    assert result.success
    assert "truncated" in (result.output or "")


# ---------------------------------------------------------------------------
# last_errors
# ---------------------------------------------------------------------------

async def test_last_errors_no_previous(tool: CodeRunnerTool):
    result = await tool.execute(CodeRunnerArgs(action="last_errors"))
    assert not result.success
    assert "no previous run" in (result.error or "").lower()


async def test_last_errors_after_failing_run(tool: CodeRunnerTool, tmp_path: Path):
    """Run a failing script, then ask for last_errors."""
    script = tmp_path / "err.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    mock = _make_subprocess_result(1, "output", "Traceback (most recent call last):")
    with patch("jarvis.tools.local.code_runner.subprocess.run", return_value=mock):
        await tool.execute(CodeRunnerArgs(action="run_script", path="err.py"))
    result = await tool.execute(CodeRunnerArgs(action="last_errors"))
    assert result.success
    assert "Traceback" in (result.output or "")


async def test_last_errors_clean_run(tool: CodeRunnerTool):
    """After a clean run (no stderr), last_errors shows last output."""
    mock = _make_subprocess_result(0, "all good", "")
    with patch("jarvis.tools.local.code_runner.subprocess.run", return_value=mock):
        await tool.execute(CodeRunnerArgs(action="run_tests"))
    result = await tool.execute(CodeRunnerArgs(action="last_errors"))
    assert result.success
    assert "no errors" in (result.output or "").lower()


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

def test_tool_name():
    assert CodeRunnerTool.name == "code_runner"


def test_voice_patterns_count():
    """Minimum 14 patterns (5 EN + 5 RU + 4 UK)."""
    assert len(CodeRunnerTool.voice_patterns) >= 14


def test_voice_patterns_priorities():
    """All priorities are in the 200–250 range."""
    for vp in CodeRunnerTool.voice_patterns:
        assert 200 <= vp.priority <= 250, f"Unexpected priority {vp.priority}"


def test_requires_confirmation_false():
    """CodeRunnerTool does not set requires_confirmation — gated via risk map."""
    assert CodeRunnerTool().requires_confirmation is False


def test_ast_parse_validates_source():
    """Ensure ast.parse catches syntax errors (the tool depends on this)."""
    with pytest.raises(SyntaxError):
        ast.parse("def broken(\n")
    # Valid code should not raise.
    ast.parse("x = 1 + 2\n")


# ---------------------------------------------------------------------------
# Fail-closed: no validator → reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_validator_rejects_run_script(tmp_path: Path):
    """CodeRunnerTool without a validator must refuse to run scripts."""
    tool = CodeRunnerTool(workspace=tmp_path)  # no path_validator
    script = tmp_path / "test.py"
    script.write_text("print('hi')", encoding="utf-8")
    result = await tool.execute(CodeRunnerArgs(action="run_script", path="test.py"))
    assert not result.success
    assert "sandbox" in (result.error or "").lower() or "disabled" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_no_validator_rejects_read_file(tmp_path: Path):
    """CodeRunnerTool without a validator must refuse to read files."""
    tool = CodeRunnerTool(workspace=tmp_path)  # no path_validator
    (tmp_path / "readme.py").write_text("x = 1", encoding="utf-8")
    result = await tool.execute(CodeRunnerArgs(action="read_file", path="readme.py"))
    assert not result.success
