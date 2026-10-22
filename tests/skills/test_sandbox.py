"""Tests for jarvis.skills.sandbox — AST validator, circuit breaker, sandbox.

Covers all 5 protection levels:
  Level 1: File isolation
  Level 2: Import whitelists / blacklists
  Level 3: AST validation
  Level 4: Circuit breaker (runtime protection)
  Level 5: User control helpers
"""

from __future__ import annotations

import pytest
from pathlib import Path

from jarvis.skills.sandbox import (
    ASTViolation,
    CircuitBreaker,
    CircuitBreakerState,
    SkillSandbox,
    SkillValidator,
    ALLOWED_IMPORTS,
    BLOCKED_IMPORTS,
    BLOCKED_CALLS,
    SandboxResult,
)


# ── Level 3: AST Validator ───────────────────────────────────────────────

class TestSkillValidator:
    def setup_method(self):
        self.v = SkillValidator()

    def test_safe_code_passes(self):
        code = "import json\nimport re\nx = json.dumps({'a': 1})"
        ok, violations = self.v.validate(code)
        assert ok
        assert violations == []

    def test_blocked_import_os(self):
        ok, violations = self.v.validate("import os")
        assert not ok
        assert any("os" in v.message for v in violations)

    def test_blocked_import_subprocess(self):
        ok, violations = self.v.validate("import subprocess")
        assert not ok
        assert any("subprocess" in v.message for v in violations)

    def test_blocked_import_sys(self):
        ok, violations = self.v.validate("import sys\nsys.exit()")
        assert not ok
        assert any("sys" in v.message for v in violations)

    def test_blocked_import_shutil(self):
        ok, violations = self.v.validate("from shutil import rmtree")
        assert not ok

    def test_blocked_import_socket(self):
        ok, violations = self.v.validate("import socket")
        assert not ok

    def test_blocked_call_eval(self):
        ok, violations = self.v.validate("x = eval('1+1')")
        assert not ok
        assert any("eval" in v.message for v in violations)

    def test_blocked_call_exec(self):
        ok, violations = self.v.validate("exec('print(1)')")
        assert not ok
        assert any("exec" in v.message for v in violations)

    def test_blocked_call_compile(self):
        ok, violations = self.v.validate("c = compile('x=1', '', 'exec')")
        assert not ok

    def test_blocked_call_open(self):
        ok, violations = self.v.validate("f = open('/etc/passwd')")
        assert not ok

    def test_blocked_call_dunder_import(self):
        ok, violations = self.v.validate("m = __import__('os')")
        assert not ok

    def test_blocked_attribute_subclasses(self):
        ok, violations = self.v.validate("x = int.__subclasses__()")
        assert not ok
        assert any("__subclasses__" in v.message for v in violations)

    def test_blocked_attribute_globals(self):
        ok, violations = self.v.validate("x = func.__globals__")
        assert not ok

    def test_syntax_error_detected(self):
        ok, violations = self.v.validate("def f(\n")
        assert not ok
        assert any("SyntaxError" in v.message for v in violations)

    def test_jarvis_internals_blocked(self):
        ok, violations = self.v.validate("from jarvis.core import events")
        assert not ok
        assert any("jarvis internals" in v.message for v in violations)

    def test_allowed_imports_pass(self):
        """All whitelisted imports should pass."""
        for mod in ["json", "re", "datetime", "math", "collections",
                     "typing", "dataclasses", "enum", "logging"]:
            ok, violations = self.v.validate(f"import {mod}")
            assert ok, f"Module '{mod}' should be allowed but was blocked: {violations}"

    def test_unknown_module_blocked(self):
        ok, violations = self.v.validate("import requests")
        assert not ok
        assert any("whitelist" in v.message.lower() for v in violations)

    def test_multiple_violations(self):
        code = "import os\nimport sys\neval('x')\nexec('y')"
        ok, violations = self.v.validate(code)
        assert not ok
        assert len(violations) >= 4

    def test_dotted_blocked_call(self):
        code = "import json\nos.remove('/tmp/x')"
        ok, violations = self.v.validate(code)
        assert not ok


# ── Level 4: Circuit Breaker ─────────────────────────────────────────────

class TestCircuitBreaker:
    def setup_method(self):
        self.cb = CircuitBreaker(max_failures=3, reset_timeout=3600.0)

    def test_initially_allowed(self):
        assert self.cb.is_allowed("test_skill")

    def test_success_keeps_closed(self):
        self.cb.record_success("test_skill")
        assert self.cb.is_allowed("test_skill")

    def test_single_failure_still_allowed(self):
        self.cb.record_failure("test_skill")
        assert self.cb.is_allowed("test_skill")

    def test_three_failures_opens_circuit(self):
        self.cb.record_failure("test_skill")
        self.cb.record_failure("test_skill")
        opened = self.cb.record_failure("test_skill")
        assert opened is True
        assert not self.cb.is_allowed("test_skill")

    def test_success_resets_consecutive(self):
        self.cb.record_failure("test_skill")
        self.cb.record_failure("test_skill")
        self.cb.record_success("test_skill")
        # Only 2 consecutive, then reset — next 2 failures should not open
        self.cb.record_failure("test_skill")
        self.cb.record_failure("test_skill")
        assert self.cb.is_allowed("test_skill")

    def test_reset_clears_state(self):
        for _ in range(3):
            self.cb.record_failure("test_skill")
        assert not self.cb.is_allowed("test_skill")
        self.cb.reset("test_skill")
        assert self.cb.is_allowed("test_skill")

    def test_reset_all(self):
        for _ in range(3):
            self.cb.record_failure("s1")
            self.cb.record_failure("s2")
        assert not self.cb.is_allowed("s1")
        assert not self.cb.is_allowed("s2")
        self.cb.reset_all()
        assert self.cb.is_allowed("s1")
        assert self.cb.is_allowed("s2")

    def test_state_tracking(self):
        state = self.cb.get_state("test_skill")
        assert state.total_calls == 0
        self.cb.record_success("test_skill")
        self.cb.record_failure("test_skill")
        state = self.cb.get_state("test_skill")
        assert state.total_calls == 2
        assert state.total_failures == 1

    def test_success_closes_open_circuit(self):
        for _ in range(3):
            self.cb.record_failure("s")
        assert not self.cb.is_allowed("s")
        # Simulate half-open by resetting state manually
        state = self.cb.get_state("s")
        state.last_failure_time = 0.0  # pretend it happened long ago
        assert self.cb.is_allowed("s")
        self.cb.record_success("s")
        assert self.cb.is_allowed("s")
        assert not self.cb.get_state("s").is_open


# ── SkillSandbox integration ────────────────────────────────────────────

class TestSkillSandbox:
    def setup_method(self):
        self.sandbox = SkillSandbox(Path("/tmp/test_skills"))

    def test_valid_skill_path(self):
        assert self.sandbox.is_valid_skill_path(Path("/tmp/test_skills/my_skill.py"))

    def test_invalid_skill_path_outside(self):
        assert not self.sandbox.is_valid_skill_path(Path("/etc/my_skill.py"))

    def test_validate_safe_code(self):
        ok, v = self.sandbox.validate_code("import json\nx = json.dumps({})")
        assert ok

    def test_validate_unsafe_code(self):
        ok, v = self.sandbox.validate_code("import os\nos.remove('x')")
        assert not ok

    def test_safe_execute_success(self):
        result = self.sandbox.safe_execute("test", lambda: 42)
        assert result.success
        assert result.output == 42
        assert result.duration_seconds >= 0

    def test_safe_execute_failure(self):
        def fail():
            raise ValueError("boom")
        result = self.sandbox.safe_execute("test", fail)
        assert not result.success
        assert "boom" in result.error

    def test_safe_execute_circuit_breaker_blocks(self):
        def fail():
            raise RuntimeError("fail")
        for _ in range(3):
            self.sandbox.safe_execute("bad_skill", fail)
        result = self.sandbox.safe_execute("bad_skill", lambda: 1)
        assert not result.success
        assert "circuit breaker" in result.error.lower()

    def test_code_preview_truncation(self):
        code = "\n".join(f"line_{i}" for i in range(100))
        preview = self.sandbox.get_skill_code_preview(code, max_lines=10)
        assert "... (90 more lines)" in preview

    def test_audit_entry(self):
        entry = self.sandbox.get_audit_entry("s1", "create", success=True)
        assert entry["skill_name"] == "s1"
        assert entry["action"] == "create"
        assert entry["success"] is True
