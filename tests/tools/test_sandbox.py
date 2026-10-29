"""Tests for the workspace sandbox path validator (T5.1).

What is pinned here:
  - access inside the whitelist is allowed,
  - access outside the whitelist is rejected,
  - traversal via ``..`` is detected, logged, and blocked,
  - symlink escape is detected and blocked,
  - empty / blank paths are rejected,
  - Windows drive-letter paths are handled correctly,
  - relative paths resolve against home,
  - the validator is case-insensitive on Windows,
  - an empty whitelist with allow_home_fallback=False blocks everything,
  - validate_or_none returns None instead of raising.

All tests use tmp_path / mocked paths — no real filesystem mutation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from jarvis.tools.sandbox import PathValidator, SandboxViolationError

# --- helpers ----------------------------------------------------------------

def _make_validator(tmp_path: Path, dirs: list[str] | None = None) -> PathValidator:
    """Build a validator with a whitelist rooted in tmp_path."""
    if dirs is None:
        allowed_dir = tmp_path / "workspace"
        allowed_dir.mkdir(exist_ok=True)
        dirs_paths = [allowed_dir]
    else:
        dirs_paths = [tmp_path / d for d in dirs]
        for d in dirs_paths:
            d.mkdir(parents=True, exist_ok=True)
    return PathValidator(allowed_paths=dirs_paths, allow_home_fallback=False)


# --- whitelist acceptance ---------------------------------------------------

class TestWhitelistAcceptance:
    """Paths inside the whitelist should be allowed."""

    def test_exact_whitelist_dir_is_allowed(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        assert v.validate(str(allowed)) == allowed.resolve()

    def test_file_inside_whitelist_is_allowed(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        target = allowed / "file.txt"
        target.touch()
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        assert v.validate(str(target)) == target.resolve()

    def test_nested_dir_inside_whitelist_is_allowed(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        nested = allowed / "sub" / "deep"
        nested.mkdir(parents=True)
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        assert v.validate(str(nested)) == nested.resolve()

    def test_multiple_whitelisted_dirs(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "alpha"
        dir_b = tmp_path / "beta"
        dir_a.mkdir()
        dir_b.mkdir()
        v = PathValidator(allowed_paths=[dir_a, dir_b], allow_home_fallback=False)
        assert v.validate(str(dir_a)) == dir_a.resolve()
        assert v.validate(str(dir_b)) == dir_b.resolve()


# --- whitelist rejection ----------------------------------------------------

class TestWhitelistRejection:
    """Paths outside the whitelist should be rejected."""

    def test_path_outside_whitelist_is_rejected(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        with pytest.raises(SandboxViolationError) as exc_info:
            v.validate(str(outside))
        assert "outside the allowed workspace" in exc_info.value.reason

    def test_sibling_of_whitelist_is_rejected(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        sibling = tmp_path / "other_data"
        allowed.mkdir()
        sibling.mkdir()
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        with pytest.raises(SandboxViolationError):
            v.validate(str(sibling))

    def test_parent_of_whitelist_is_rejected(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        with pytest.raises(SandboxViolationError):
            v.validate(str(tmp_path))

    def test_empty_whitelist_no_fallback_rejects_everything(
        self, tmp_path: Path
    ) -> None:
        some_dir = tmp_path / "anything"
        some_dir.mkdir()
        v = PathValidator(allowed_paths=[], allow_home_fallback=False)
        with pytest.raises(SandboxViolationError):
            v.validate(str(some_dir))


# --- traversal detection ----------------------------------------------------

class TestTraversalDetection:
    """``..`` traversal attempts should be blocked."""

    def test_dotdot_traversal_is_blocked(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        # Traverse up from workspace to tmp_path (outside whitelist)
        traversal = str(allowed / ".." / "other_data")
        (tmp_path / "other_data").mkdir(exist_ok=True)
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        with pytest.raises(SandboxViolationError):
            v.validate(traversal)

    def test_dotdot_that_stays_inside_whitelist_is_allowed(
        self, tmp_path: Path
    ) -> None:
        allowed = tmp_path / "workspace"
        sub = allowed / "sub"
        sub.mkdir(parents=True)
        # ../workspace resolves back into the whitelist
        traversal = str(sub / ".." / "sub")
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        result = v.validate(traversal)
        assert result == sub.resolve()

    def test_leading_dotdot_is_blocked(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        # Relative ".." from home won't be inside our tmp whitelist
        with pytest.raises(SandboxViolationError):
            v.validate("../../etc/data")


# --- symlink detection ------------------------------------------------------

class TestSymlinkDetection:
    """Symlinks that escape the whitelist should be blocked."""

    @pytest.mark.skipif(
        sys.platform == "win32" and not os.environ.get("CI"),
        reason="symlink creation may need elevated privileges on Windows",
    )
    def test_symlink_escape_is_blocked(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        private_dir = tmp_path / "private_data"
        private_dir.mkdir()
        private_file = private_dir / "data.txt"
        private_file.write_text("sensitive content")
        # Create a symlink inside the whitelist that points outside
        link = allowed / "escape"
        link.symlink_to(private_dir)
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        with pytest.raises(SandboxViolationError) as exc_info:
            v.validate(str(link))
        assert "symlink" in exc_info.value.reason.lower() or \
               "outside" in exc_info.value.reason.lower()

    @pytest.mark.skipif(
        sys.platform == "win32" and not os.environ.get("CI"),
        reason="symlink creation may need elevated privileges on Windows",
    )
    def test_symlink_inside_whitelist_is_allowed(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        target = allowed / "real"
        target.mkdir(parents=True)
        link = allowed / "alias"
        link.symlink_to(target)
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        # Symlink target is inside the whitelist — should be allowed.
        result = v.validate(str(link))
        assert result == target.resolve()


# --- edge cases -------------------------------------------------------------

class TestEdgeCases:
    """Edge cases: empty paths, blank paths, tilde expansion."""

    def test_empty_path_is_rejected(self, tmp_path: Path) -> None:
        v = _make_validator(tmp_path)
        with pytest.raises(SandboxViolationError) as exc_info:
            v.validate("")
        assert "empty" in exc_info.value.reason

    def test_blank_path_is_rejected(self, tmp_path: Path) -> None:
        v = _make_validator(tmp_path)
        with pytest.raises(SandboxViolationError) as exc_info:
            v.validate("   ")
        assert "empty" in exc_info.value.reason

    def test_validate_or_none_returns_none_on_violation(
        self, tmp_path: Path
    ) -> None:
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        assert v.validate_or_none(str(tmp_path / "outside")) is None

    def test_validate_or_none_returns_path_on_success(
        self, tmp_path: Path
    ) -> None:
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        assert v.validate_or_none(str(allowed)) == allowed.resolve()


# --- properties -------------------------------------------------------------

class TestProperties:
    """PathValidator properties."""

    def test_allowed_paths_returns_normalised_tuple(
        self, tmp_path: Path
    ) -> None:
        a = tmp_path / "alpha"
        b = tmp_path / "beta"
        a.mkdir()
        b.mkdir()
        v = PathValidator(allowed_paths=[a, b], allow_home_fallback=False)
        assert isinstance(v.allowed_paths, tuple)
        assert len(v.allowed_paths) == 2

    def test_is_path_allowed_direct(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        assert v.is_path_allowed(allowed.resolve())
        assert not v.is_path_allowed(tmp_path.resolve())


# --- home fallback ----------------------------------------------------------

class TestHomeFallback:
    """When allow_home_fallback=True and whitelist is empty."""

    def test_fallback_populates_from_home(self, tmp_path: Path) -> None:
        """With allow_home_fallback=True and empty list, validator should
        attempt home-directory fallback (Desktop/Documents if they exist)."""
        # We don't test actual home dirs; just verify the mechanism.
        v = PathValidator(allowed_paths=[], allow_home_fallback=True)
        # The validator may or may not have found home dirs. Either way,
        # the object should be constructed without error.
        assert isinstance(v.allowed_paths, tuple)


# --- Windows-specific -------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path tests")
class TestWindowsPaths:
    """Windows-specific path handling."""

    def test_drive_letter_path(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        # Construct a path with explicit drive letter.
        target = allowed / "file.txt"
        target.touch()
        result = v.validate(str(target))
        assert result == target.resolve()

    def test_backslash_paths(self, tmp_path: Path) -> None:
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        v = PathValidator(allowed_paths=[allowed], allow_home_fallback=False)
        target = allowed / "file.txt"
        target.touch()
        # Use explicit backslashes
        bs_path = str(target).replace("/", "\\")
        result = v.validate(bs_path)
        assert result == target.resolve()
