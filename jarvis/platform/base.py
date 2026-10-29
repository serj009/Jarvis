"""Platform-operations contract shared by every OS backend.

This module is *documentation of the contract* that the per-OS backend
modules (``platform/windows.py``, ``platform/darwin.py``,
``platform/linux.py``) implement. It is deliberately dependency-free:

* It must NOT import ``win32``, ``ctypes``, ``winreg``, ``wmi``,
  ``pygetwindow``, ``PySide6`` or any other platform/UI library — importing
  it is always safe on every host and in the test runner.
* It exists so the operations each backend must provide are written down in
  one place, with signatures and docstrings, and so tools can type-hint
  against :class:`PlatformOps`.

The real implementations live in the per-OS modules. ``platform.windows``
implements every operation for real; ``platform.darwin`` and
``platform.linux`` implement the same names but raise
:class:`NotSupportedError` (a subclass of :class:`NotImplementedError`), so
the tool layer's existing ``except NotImplementedError`` handling turns an
unsupported-platform call into a clean ``ToolResult`` error.

Operation groups (matching the three tool files being refactored):

* **startup** — Windows Run-key / Startup-folder management
  (``list_startup`` / ``add_to_startup`` / ``remove_from_startup``).
* **display** — brightness, Night Light and monitor/resolution info
  (``get_brightness`` / ``set_brightness`` / ``toggle_night_mode`` /
  ``display_info``).
* **window** — the low-level Win32 window primitives that are NOT covered
  by the cross-platform ``pygetwindow`` library and therefore have to live
  behind the platform seam (``list_windows_win32`` /
  ``get_screen_metrics``). The ordinary focus/minimize/maximize/move/resize/
  snap operations are performed through ``pygetwindow`` directly in
  ``window_manager`` and are intentionally NOT part of this contract.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


class NotSupportedError(NotImplementedError):
    """Raised when an operation is not available on the current platform.

    Subclasses :class:`NotImplementedError` so existing tool code that does
    ``except NotImplementedError`` keeps working unchanged: a call routed to
    the macOS or Linux backend raises this and the tool reports a clean
    ``ToolResult`` error instead of crashing.
    """

    @staticmethod
    def for_feature(feature: str, platform_name: str) -> NotSupportedError:
        """Build a ``NotSupportedError`` with a clear, consistent message.

        Example: ``NotSupportedError.for_feature("set_brightness", "macOS")``
        -> message ``"set_brightness is not supported on macOS"``.
        """
        return NotSupportedError(f"{feature} is not supported on {platform_name}")


@runtime_checkable
class PlatformOps(Protocol):
    """The set of OS primitives each platform backend must provide.

    Implementations:
      * ``platform.windows`` — real Win32 / winreg / WMI / ctypes calls.
      * ``platform.darwin`` / ``platform.linux`` — every method raises
        :class:`NotSupportedError`.

    All methods raise :class:`OSError` on a genuine OS-API failure (callers
    wrap the message into a ``ToolResult.error``) and
    :class:`NotImplementedError` / :class:`NotSupportedError` when the host
    OS cannot perform the operation at all.
    """

    # -- startup ---------------------------------------------------------

    def list_startup(self) -> list[dict]:
        """Return every auto-start entry as a list of dicts.

        Each dict has keys ``name``, ``command``, ``source`` (e.g.
        ``"HKCU\\Run"``, ``"HKLM\\Run"`` or ``"Startup folder"``) and
        ``removable`` (bool — whether this backend can delete it without
        elevation).
        """
        ...

    def add_to_startup(self, name: str, command: str) -> str:
        """Register ``command`` to run at login under the label ``name``.

        Returns a human-readable confirmation string. Raises ``OSError``
        (e.g. ``winerror`` 5 / access-denied) when the write fails.
        """
        ...

    def remove_from_startup(self, name: str) -> str:
        """Remove the auto-start entry named ``name``.

        Returns a human-readable result string (including the "not found"
        case). Raises ``OSError`` on an OS-API failure other than a missing
        entry.
        """
        ...

    # -- display ---------------------------------------------------------

    def set_brightness(self, level: int) -> str:
        """Set the primary monitor brightness to ``level`` (0-100).

        Returns a confirmation string. Raises ``NotImplementedError`` when
        no supported brightness backend (WMI / DDC-CI) is available.
        """
        ...

    def get_brightness(self) -> int | None:
        """Return the current brightness (0-100), or ``None`` if unknown."""
        ...

    def toggle_night_mode(self, enable: bool) -> str:
        """Enable or disable the OS blue-light / Night Light filter.

        Returns a confirmation string. Raises ``NotImplementedError`` when
        the toggle is unavailable on this host.
        """
        ...

    def display_info(self) -> dict:
        """Return monitor information.

        Shape: ``{"monitor_count": int, "monitors": [ {"name", "adapter",
        "width", "height", "refresh_hz", "bits_per_pixel"} ... ],
        "brightness": int | None}``.
        """
        ...

    # -- window (low-level Win32 primitives only) ------------------------

    def list_windows_win32(self) -> list[dict]:
        """Enumerate visible top-level windows via the OS window manager.

        This is the fallback path used when the cross-platform
        ``pygetwindow`` library is unavailable. Each dict has at least
        ``title`` and ``hwnd``. Raises ``ImportError`` when the native
        binding is missing and ``NotImplementedError`` off the supporting
        platform.
        """
        ...

    def get_screen_metrics(self) -> tuple[int, int]:
        """Return the primary screen ``(width, height)`` in pixels."""
        ...


__all__ = ["NotSupportedError", "PlatformOps"]
