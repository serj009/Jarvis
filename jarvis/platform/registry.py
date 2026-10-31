"""Read-only Windows registry access behind a narrow interface (Task 8c).

Why this module exists
----------------------
Launcher-game discovery (GOG, and later others) needs to read a few HKLM
subkeys. Registry access is Windows-specific and is therefore confined to
``jarvis/platform/`` by the architecture rule
(tests/test_architecture_rules.py). Rather than scatter ``winreg`` calls,
this module exposes a tiny, read-only ``RegistryReader`` that game sources
depend on through an interface, so tests inject a fake registry and run on
any OS.

Security / safety
-----------------
* READ ONLY. There is no SetValue / CreateKey / DeleteKey here, by design.
* Values are returned as plain data (str / int / bytes as winreg yields
  them). Nothing is executed. Callers treat every value as untrusted.
* Any access error (key missing, permission denied, value absent, non-
  Windows) yields an EMPTY result and a WARNING — never an exception out.
* ``import winreg`` happens inside the methods, under a Windows guard, so
  importing this module on Linux/macOS (CI, dev) never fails.
"""

from __future__ import annotations

import logging
import sys
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)

# Hive identifiers as plain strings so callers (and the fake reader in
# tests) never need the winreg module. Mapped to winreg constants inside
# WindowsRegistryReader only.
HKLM = "HKLM"
HKCU = "HKCU"
HKCR = "HKCR"


@runtime_checkable
class RegistryReader(Protocol):
    """Read-only registry interface. Implementations must never raise for
    a missing/inaccessible key — they return an empty list / dict."""

    def read_subkeys(self, hive: str, path: str) -> list[str]:
        """Names of the immediate subkeys of ``hive\\path`` (empty on error)."""
        ...

    def read_values(self, hive: str, path: str) -> dict[str, object]:
        """Value-name -> value mapping for ``hive\\path`` (empty on error)."""
        ...


class WindowsRegistryReader:
    """The real reader. Windows-only at call time; safe to import anywhere."""

    def _winreg(self) -> object:
        import winreg

        return winreg

    def _hive_const(self, hive: str) -> int | None:
        if sys.platform != "win32":
            return None
        winreg = self._winreg()
        mapping = {
            HKLM: winreg.HKEY_LOCAL_MACHINE,
            HKCU: winreg.HKEY_CURRENT_USER,
            HKCR: winreg.HKEY_CLASSES_ROOT,
        }
        return mapping.get(hive)

    def read_subkeys(self, hive: str, path: str) -> list[str]:
        if sys.platform != "win32":
            return []
        winreg = self._winreg()
        root = self._hive_const(hive)
        if root is None:
            log.warning("registry: unknown hive %r", hive)
            return []
        subkeys: list[str] = []
        try:
            # KEY_READ | KEY_WOW64_64KEY so a 64-bit launcher's keys are
            # visible even if Python were 32-bit; WOW6432Node paths are
            # passed explicitly by callers when they want the 32-bit view.
            with winreg.OpenKey(
                root, path, 0, winreg.KEY_READ
            ) as key:
                i = 0
                while True:
                    try:
                        subkeys.append(winreg.EnumKey(key, i))
                    except OSError:
                        break
                    i += 1
        except OSError as e:
            log.warning("registry: cannot read subkeys of %s\\%s: %s", hive, path, e)
            return []
        return subkeys

    def read_values(self, hive: str, path: str) -> dict[str, object]:
        if sys.platform != "win32":
            return {}
        winreg = self._winreg()
        root = self._hive_const(hive)
        if root is None:
            log.warning("registry: unknown hive %r", hive)
            return {}
        values: dict[str, object] = {}
        try:
            with winreg.OpenKey(
                root, path, 0, winreg.KEY_READ
            ) as key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    values[name] = value
                    i += 1
        except OSError as e:
            log.warning("registry: cannot read values of %s\\%s: %s", hive, path, e)
            return {}
        return values
