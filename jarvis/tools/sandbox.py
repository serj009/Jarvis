"""Workspace sandbox — path validation with whitelist enforcement (T5.1).

Defence-in-depth path gating for any tool that touches the filesystem.
Every path a tool receives is untrusted user / LLM input; this module
resolves, normalises, and validates it against a configurable whitelist
before any I/O happens.

Design decisions:

- Validate-then-return: ``validate()`` returns a resolved ``Path`` only
  when every check passes; it raises ``SandboxViolationError`` on any
  failure. Tools never see a path the sandbox rejected.

- Fail-closed: an empty whitelist means *nothing* is accessible. The
  default config seeds ``~/Desktop`` and ``~/Documents`` so a fresh
  install has a reasonable surface, but tools cannot reach the rest of
  the filesystem until the user adds paths.

- Symlink resolution: the resolved (real) path is tested against the
  whitelist, not the user-supplied path. A symlink at ``~/safe/escape``
  that points to ``C:\\Windows\\System32`` is rejected because the real
  path is outside the whitelist, regardless of where the symlink lives.

- ``..`` traversal: after resolving, the path is checked for whitelist
  membership. A path like ``~/Desktop/../../etc/passwd`` resolves to
  ``C:\\etc\\passwd`` (or equivalent), which is outside the whitelist
  and is rejected. The *attempt* is logged at WARNING so the operator
  can see traversal probes.

- Windows-compatible: drive letters, backslashes, UNC paths, and case-
  insensitive comparison (``Path.resolve()`` on Windows already lower-
  cases drive letters; we normalise the whitelist the same way).

- Thread-safe: ``PathValidator`` is stateless beyond its frozen config;
  the logger it writes to is thread-safe by design.

Integration:
    from jarvis.tools.sandbox import PathValidator, SandboxViolationError
    validator = PathValidator(allowed_paths=[Path.home() / "Desktop"])
    resolved = validator.validate("~/Desktop/report.txt")  # ok
    validator.validate("C:/Windows/System32/cmd.exe")       # raises
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class SandboxViolationError(Exception):
    """Raised when a path access is blocked by the sandbox.

    Carries the original (unsanitised) path and a human-readable reason
    so the tool can return a meaningful ToolResult.error without leaking
    resolved-path internals to the LLM."""

    def __init__(self, raw_path: str, reason: str) -> None:
        self.raw_path = raw_path
        self.reason = reason
        super().__init__(f"sandbox blocked {raw_path!r}: {reason}")


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    """A validated, resolved filesystem path.

    Carries both the resolved absolute path and whether it passed
    whitelist validation, so callers have proof the check ran."""

    path: Path
    allowed: bool


def _normalise(p: Path) -> Path:
    """Normalise a path for comparison.

    On Windows, ``Path.resolve()`` already produces a canonical form with
    a capitalised drive letter. We call ``resolve()`` to collapse ``..``
    and follow symlinks, then return the result. On case-insensitive
    filesystems this is sufficient for equality checks because both sides
    of the comparison go through the same normalisation."""
    return p.resolve()


class PathValidator:
    """Validates filesystem paths against a whitelist.

    Constructed once with the allowed directories (from config). Each
    ``validate()`` call resolves the input path, checks every defence
    layer, and either returns the resolved ``Path`` or raises
    ``SandboxViolationError``.

    The validator is intentionally stateless beyond the frozen whitelist
    so it can be shared across tools and threads without locking."""

    def __init__(
        self,
        allowed_paths: list[Path] | None = None,
        *,
        allow_home_fallback: bool = True,
    ) -> None:
        raw = allowed_paths if allowed_paths is not None else []
        # Normalise the whitelist entries once at construction so every
        # validate() call compares like with like.
        self._allowed: tuple[Path, ...] = tuple(
            _normalise(p) for p in raw
        )
        # When True AND the whitelist is empty, fall back to ~/Desktop
        # and ~/Documents so a fresh install isn't fully locked out.
        if allow_home_fallback and not self._allowed:
            home = Path.home()
            fallback = [home / "Desktop", home / "Documents"]
            self._allowed = tuple(
                _normalise(p) for p in fallback if p.exists()
            )
            if self._allowed:
                log.info(
                    "sandbox: whitelist empty, falling back to home dirs: %s",
                    [str(p) for p in self._allowed],
                )

    @property
    def allowed_paths(self) -> tuple[Path, ...]:
        """The normalised whitelist. Read-only; change via config + rebuild."""
        return self._allowed

    def is_path_allowed(self, resolved: Path) -> bool:
        """Check whether ``resolved`` falls inside any whitelisted directory.

        Uses ``Path.is_relative_to()`` (Python 3.9+) for containment.
        An exact match (the whitelisted directory itself) is allowed."""
        for allowed in self._allowed:
            try:
                if resolved == allowed or resolved.is_relative_to(allowed):
                    return True
            except (ValueError, TypeError):
                continue
        return False

    def validate(self, raw: str) -> Path:
        """Resolve, validate, and return the path — or raise.

        Checks applied in order:
        1. Expand ``~`` and make absolute.
        2. Resolve symlinks and ``..`` to a canonical path.
        3. Detect traversal attempts (``..`` in the original input).
        4. Detect symlink escapes (resolved path differs from expanded).
        5. Whitelist containment check.

        On any failure, ``SandboxViolationError`` is raised and the
        attempt is logged at WARNING for operational visibility.

        Returns the resolved ``Path`` on success.
        """
        if not raw or not raw.strip():
            raise SandboxViolationError(raw, "empty path")

        # Step 1: expand ~ and make absolute.
        expanded = Path(os.path.expanduser(raw))
        if not expanded.is_absolute():
            expanded = (Path.home() / expanded).resolve()
        else:
            expanded = expanded.resolve()

        # Step 2: resolve symlinks and .. to canonical form.
        try:
            resolved = _normalise(expanded)
        except (OSError, ValueError) as e:
            log.warning(
                "sandbox: path resolution failed for %r: %s", raw, e,
            )
            raise SandboxViolationError(raw, f"path resolution failed: {e}")

        # Step 3: detect traversal attempts in the raw input.
        # We normalise separators for cross-platform traversal detection.
        normalised_raw = raw.replace("\\", "/")
        if "/.." in normalised_raw or normalised_raw.startswith(".."):
            log.warning(
                "sandbox: traversal attempt detected in %r (resolved to %s)",
                raw,
                resolved,
            )
            # Still proceed to the whitelist check — the traversal may
            # resolve to an allowed path, but we log the attempt.

        # Step 4: detect symlink escape.
        # If the expanded path (before resolve) and the resolved path
        # differ in their parent whitelist membership, a symlink led
        # outside the allowed zone.
        try:
            # On Windows, lstat may fail on nonexistent paths; that's
            # fine — the path can't be a symlink if it doesn't exist.
            pre_resolve = Path(os.path.expanduser(raw))
            if not pre_resolve.is_absolute():
                pre_resolve = Path.home() / pre_resolve
            if pre_resolve.exists() and pre_resolve.is_symlink():
                if not self.is_path_allowed(resolved):
                    log.warning(
                        "sandbox: symlink escape blocked: %r -> %s "
                        "(target outside whitelist)",
                        raw,
                        resolved,
                    )
                    raise SandboxViolationError(
                        raw,
                        "symlink target is outside the allowed workspace",
                    )
        except SandboxViolationError:
            raise
        except Exception:
            # Best-effort symlink check; don't let a stat failure block
            # the path if it passes the whitelist check below.
            pass

        # Step 5: whitelist containment.
        if not self.is_path_allowed(resolved):
            log.warning(
                "sandbox: access denied for %r (resolved: %s) — "
                "not in whitelist %s",
                raw,
                resolved,
                [str(p) for p in self._allowed],
            )
            raise SandboxViolationError(
                raw,
                "path is outside the allowed workspace directories",
            )

        log.debug("sandbox: access allowed for %r -> %s", raw, resolved)
        return resolved

    def validate_or_none(self, raw: str) -> Path | None:
        """Like ``validate()`` but returns None instead of raising.

        Convenience for callers that want to check feasibility without
        try/except (e.g. UI path suggestions)."""
        try:
            return self.validate(raw)
        except SandboxViolationError:
            return None
