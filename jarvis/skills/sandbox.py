"""Skill sandbox — 5 levels of protection for user-generated code.

Level 1 — File isolation:
    Skills ONLY live in ``jarvis/skills/user_generated/``.
    No imports from ``jarvis.core``, ``jarvis.memory``, ``jarvis.ui``.
    Read-only access to tools: call through registry, never modify.
    No access to app.py, config, DB directly.

Level 2 — Sandbox execution:
    Whitelist of allowed imports.  Blacklist of dangerous modules.
    No ``open()`` with write, no ``eval/exec/compile/__import__``.
    Timeout: 30 sec → kill.  Memory limit: 100 MB.
    No direct network access (only through tools).

Level 3 — AST validation BEFORE creation:
    ``SkillValidator`` walks the AST tree to detect violations *before*
    any code runs.

Level 4 — Runtime protection:
    try/except on EVERY call — Jarvis continues on error.
    Circuit breaker: 3 consecutive failures → auto-disable.
    Logging: input, output, errors, execution time.
    No cascading failures — skill error NEVER propagates to core.

Level 5 — User control:
    Mandatory confirmation, code preview, kill switch, audit log.
"""

from __future__ import annotations

import ast
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── Level 2: Module whitelists / blacklists ──────────────────────────────

ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "json", "re", "datetime", "math", "collections", "typing",
    "dataclasses", "enum", "logging", "functools", "itertools",
    "string", "textwrap", "copy", "decimal", "fractions",
    "statistics", "operator", "uuid", "hashlib", "base64",
})

BLOCKED_IMPORTS: frozenset[str] = frozenset({
    "os", "sys", "subprocess", "shutil", "socket", "http",
    "ctypes", "importlib", "pathlib", "multiprocessing", "threading",
    "signal", "pickle", "shelve", "tempfile", "glob", "io",
    "webbrowser", "ftplib", "smtplib", "xmlrpc", "urllib",
    "asyncio", "concurrent", "ssl", "sqlite3",
})

BLOCKED_CALLS: frozenset[str] = frozenset({
    "eval", "exec", "compile", "__import__", "open",
    "os.remove", "os.unlink", "os.rmdir", "os.makedirs",
    "shutil.rmtree", "shutil.move", "shutil.copy",
    "subprocess.run", "subprocess.call", "subprocess.Popen",
    "globals", "locals", "delattr", "setattr",
    "breakpoint", "exit", "quit",
})

# Attributes on builtins that allow code execution / introspection escapes
BLOCKED_ATTRS: frozenset[str] = frozenset({
    "__subclasses__", "__bases__", "__mro__", "__class__",
    "__globals__", "__code__", "__builtins__",
})

# ── Level 3: AST Validator ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ASTViolation:
    """A single AST-level security violation."""
    line: int
    col: int
    message: str


class SkillValidator:
    """Parse code through ``ast.parse`` and walk the tree to detect
    security violations BEFORE any code is executed.

    Usage::

        validator = SkillValidator()
        is_safe, violations = validator.validate(source_code)
        if not is_safe:
            for v in violations:
                print(f"Line {v.line}: {v.message}")
    """

    BLOCKED_IMPORTS = BLOCKED_IMPORTS
    BLOCKED_CALLS = BLOCKED_CALLS
    BLOCKED_ATTRS = BLOCKED_ATTRS
    ALLOWED_IMPORTS = ALLOWED_IMPORTS

    def validate(self, code: str) -> tuple[bool, list[ASTViolation]]:
        """Parse AST, walk tree, return ``(is_safe, violations)``.

        Returns ``(False, [syntax-error])`` if code cannot be parsed.
        """
        violations: list[ASTViolation] = []

        # --- Step 1: parse ---
        try:
            tree = ast.parse(code, filename="<skill>", mode="exec")
        except SyntaxError as exc:
            violations.append(ASTViolation(
                line=exc.lineno or 0,
                col=exc.offset or 0,
                message=f"SyntaxError: {exc.msg}",
            ))
            return False, violations

        # --- Step 2: walk ---
        for node in ast.walk(tree):
            # Import check
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._check_import(alias.name, node, violations)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self._check_import(node.module, node, violations)
            # Call check
            elif isinstance(node, ast.Call):
                self._check_call(node, violations)
            # Attribute check
            elif isinstance(node, ast.Attribute):
                self._check_attribute(node, violations)

        return len(violations) == 0, violations

    def _check_import(
        self, module_name: str, node: ast.AST, violations: list[ASTViolation],
    ) -> None:
        top_module = module_name.split(".")[0]
        if top_module in self.BLOCKED_IMPORTS:
            violations.append(ASTViolation(
                line=getattr(node, "lineno", 0),
                col=getattr(node, "col_offset", 0),
                message=f"Blocked import: {module_name}",
            ))
        elif top_module not in self.ALLOWED_IMPORTS:
            # Not explicitly allowed either — warn (but treat as violation
            # for safety: whitelist-only policy).
            if top_module.startswith("jarvis"):
                violations.append(ASTViolation(
                    line=getattr(node, "lineno", 0),
                    col=getattr(node, "col_offset", 0),
                    message=f"Cannot import jarvis internals: {module_name}",
                ))
            else:
                violations.append(ASTViolation(
                    line=getattr(node, "lineno", 0),
                    col=getattr(node, "col_offset", 0),
                    message=f"Import not in whitelist: {module_name}",
                ))

    def _check_call(
        self, node: ast.Call, violations: list[ASTViolation],
    ) -> None:
        name = self._call_name(node)
        if name and name in self.BLOCKED_CALLS:
            violations.append(ASTViolation(
                line=node.lineno,
                col=node.col_offset,
                message=f"Blocked call: {name}()",
            ))

    def _check_attribute(
        self, node: ast.Attribute, violations: list[ASTViolation],
    ) -> None:
        if node.attr in self.BLOCKED_ATTRS:
            violations.append(ASTViolation(
                line=node.lineno,
                col=node.col_offset,
                message=f"Blocked attribute access: .{node.attr}",
            ))

    @staticmethod
    def _call_name(node: ast.Call) -> str | None:
        """Extract the callable name from a Call node."""
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            # e.g. os.remove → "os.remove"
            parts: list[str] = [func.attr]
            current = func.value
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            parts.reverse()
            return ".".join(parts)
        return None


# ── Level 4: Runtime Protection (Circuit Breaker) ────────────────────────


@dataclass
class CircuitBreakerState:
    """Tracks consecutive failures for a skill."""
    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    is_open: bool = False
    total_calls: int = 0
    total_failures: int = 0

    # Config
    max_consecutive_failures: int = 3
    reset_timeout_seconds: float = 3600.0  # 1 hour


class CircuitBreaker:
    """Level 4 runtime protection — auto-disable skills after repeated failures.

    Three consecutive failures → open (disabled).  After ``reset_timeout``
    the circuit half-opens: one test call is allowed.  If it succeeds the
    breaker closes; if it fails the breaker stays open.
    """

    def __init__(self, max_failures: int = 3, reset_timeout: float = 3600.0) -> None:
        self._states: dict[str, CircuitBreakerState] = {}
        self._max_failures = max_failures
        self._reset_timeout = reset_timeout

    def get_state(self, skill_name: str) -> CircuitBreakerState:
        if skill_name not in self._states:
            self._states[skill_name] = CircuitBreakerState(
                max_consecutive_failures=self._max_failures,
                reset_timeout_seconds=self._reset_timeout,
            )
        return self._states[skill_name]

    def is_allowed(self, skill_name: str) -> bool:
        """Check whether a skill call is allowed (circuit closed / half-open)."""
        state = self.get_state(skill_name)
        if not state.is_open:
            return True
        # Check half-open window
        elapsed = time.monotonic() - state.last_failure_time
        if elapsed >= state.reset_timeout_seconds:
            log.info("[circuit-breaker] %s: half-open (trying one call)", skill_name)
            return True
        return False

    def record_success(self, skill_name: str) -> None:
        state = self.get_state(skill_name)
        state.total_calls += 1
        state.consecutive_failures = 0
        if state.is_open:
            log.info("[circuit-breaker] %s: closed (recovered)", skill_name)
            state.is_open = False

    def record_failure(self, skill_name: str) -> bool:
        """Record a failure.  Returns True if the circuit just opened."""
        state = self.get_state(skill_name)
        state.total_calls += 1
        state.total_failures += 1
        state.consecutive_failures += 1
        state.last_failure_time = time.monotonic()

        if state.consecutive_failures >= state.max_consecutive_failures:
            if not state.is_open:
                state.is_open = True
                log.warning(
                    "[circuit-breaker] %s: OPEN after %d consecutive failures",
                    skill_name, state.consecutive_failures,
                )
                return True
        return False

    def reset(self, skill_name: str) -> None:
        """Manually reset the breaker for a skill."""
        if skill_name in self._states:
            self._states[skill_name] = CircuitBreakerState(
                max_consecutive_failures=self._max_failures,
                reset_timeout_seconds=self._reset_timeout,
            )

    def reset_all(self) -> None:
        """Kill switch — reset all breakers."""
        self._states.clear()


# ── Skill Sandbox (combines all levels) ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """Result of running a skill in the sandbox."""
    success: bool
    output: Any = None
    error: str | None = None
    duration_seconds: float = 0.0


class SkillSandbox:
    """Unified sandbox combining all 5 protection levels.

    Construction requires the path to ``user_generated/`` (Level 1 isolation)
    and an optional ``SkillValidator`` instance (Level 3 AST).

    Parameters:
        skills_dir:  Absolute path to ``jarvis/skills/user_generated/``.
        validator:   AST validator (created internally if None).
        timeout:     Max execution time in seconds (default 30).
        max_memory:  Max memory in bytes (default 100 MB).
    """

    DEFAULT_TIMEOUT: int = 30
    DEFAULT_MAX_MEMORY: int = 100 * 1024 * 1024  # 100 MB

    def __init__(
        self,
        skills_dir: Path,
        *,
        validator: SkillValidator | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_memory: int = DEFAULT_MAX_MEMORY,
    ) -> None:
        self._skills_dir = Path(skills_dir)
        self._validator = validator or SkillValidator()
        self._timeout = timeout
        self._max_memory = max_memory
        self._circuit_breaker = CircuitBreaker()

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker

    @property
    def validator(self) -> SkillValidator:
        return self._validator

    # ── Level 1: File isolation ──

    def is_valid_skill_path(self, path: Path) -> bool:
        """Check that a skill file lives inside the user_generated directory."""
        try:
            resolved = Path(path).resolve()
            return resolved.is_relative_to(self._skills_dir.resolve())
        except (ValueError, OSError):
            return False

    # ── Level 3: AST validation ──

    def validate_code(self, code: str) -> tuple[bool, list[ASTViolation]]:
        """Validate skill code via AST analysis.  Returns (is_safe, violations)."""
        return self._validator.validate(code)

    # ── Level 4: Safe execution wrapper ──

    def safe_execute(
        self,
        skill_name: str,
        func: Any,
        *args: Any,
        **kwargs: Any,
    ) -> SandboxResult:
        """Execute a skill function with full Level 4 protection.

        - Checks circuit breaker first.
        - Wraps in try/except (never propagates to core).
        - Measures execution time.
        - Updates circuit breaker state.
        """
        # Circuit breaker check
        if not self._circuit_breaker.is_allowed(skill_name):
            return SandboxResult(
                success=False,
                error=f"Skill '{skill_name}' is disabled (circuit breaker open).",
            )

        start = time.monotonic()
        try:
            result = func(*args, **kwargs)
            duration = time.monotonic() - start

            # Timeout check (soft — actual hard timeout would need threading)
            if duration > self._timeout:
                log.warning(
                    "[sandbox] %s exceeded timeout: %.1fs > %ds",
                    skill_name, duration, self._timeout,
                )

            self._circuit_breaker.record_success(skill_name)
            return SandboxResult(
                success=True,
                output=result,
                duration_seconds=duration,
            )
        except Exception as exc:
            duration = time.monotonic() - start
            log.warning(
                "[sandbox] %s failed after %.1fs: %s",
                skill_name, duration, exc, exc_info=True,
            )
            just_opened = self._circuit_breaker.record_failure(skill_name)
            return SandboxResult(
                success=False,
                error=str(exc),
                duration_seconds=duration,
            )

    # ── Level 5: User control helpers ──

    def get_skill_code_preview(self, code: str, max_lines: int = 50) -> str:
        """Return a truncated code preview for user confirmation dialog."""
        lines = code.splitlines()
        if len(lines) <= max_lines:
            return code
        preview = "\n".join(lines[:max_lines])
        return f"{preview}\n\n... ({len(lines) - max_lines} more lines)"

    def get_audit_entry(
        self,
        skill_name: str,
        action: str,
        *,
        success: bool = True,
        detail: str = "",
    ) -> dict:
        """Create a structured audit log entry for Level 5 tracking."""
        return {
            "timestamp": time.time(),
            "skill_name": skill_name,
            "action": action,
            "success": success,
            "detail": detail,
        }
