"""requirements.lock must match the runtime dependencies in pyproject.toml.

The lock was generated on 2026-10-15; a day later pyproject gained
argon2-cffi and a cryptography<44 cap without a regenerated lock. A build
installed from the lock then had no argon2-cffi (the password vault refuses
to work without it) and cryptography 50, outside the declared range.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*(?:;\s*([^\\]+?))?\s*\\?$")


def _direct_requirements() -> list[Requirement]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return [Requirement(spec) for spec in data["project"]["dependencies"]]


def _locked() -> dict[str, list[tuple[Version, str | None]]]:
    """name -> [(version, marker)]; universal locks may pin a package twice
    under different markers (e.g. by Python version)."""
    pins: dict[str, list[tuple[Version, str | None]]] = {}
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line)
        if match:
            name, version, marker = match.groups()
            pins.setdefault(canonicalize_name(name), []).append((Version(version), marker))
    return pins


@pytest.mark.parametrize(
    "requirement", _direct_requirements(), ids=lambda r: r.name
)
def test_every_direct_dependency_is_locked_within_its_range(requirement):
    pins = _locked().get(canonicalize_name(requirement.name))
    assert pins, (
        f"{requirement.name} is in pyproject.toml but not in requirements.lock: "
        "regenerate the lock (see its header)"
    )
    for version, _marker in pins:
        assert requirement.specifier.contains(version, prereleases=True), (
            f"requirements.lock pins {requirement.name}=={version}, outside "
            f"pyproject's {requirement.specifier}: regenerate the lock"
        )


def test_lock_parser_reads_the_file():
    """Guard for the test itself: a format change must not make it vacuous."""
    pins = _locked()
    assert len(pins) > 50
    assert canonicalize_name("pydantic") in pins
