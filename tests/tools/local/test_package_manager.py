"""Tests for jarvis.tools.local.package_manager.

All tests mock subprocess.run (winget) to avoid real installations.
"""

from __future__ import annotations

from unittest.mock import patch


from jarvis.tools.local.package_manager import (
    PackageInstallArgs,
    PackageInstallTool,
    PackageSearchArgs,
    PackageSearchTool,
    PackageUninstallArgs,
    PackageUninstallTool,
)

# --- PackageSearchTool -----------------------------------------------------

async def test_search_success():
    with patch("jarvis.tools.local.package_manager._run_winget") as mock_wg:
        mock_wg.return_value = {
            "success": True,
            "output": (
                "Name        Id               Version\n"
                "---\n"
                "VLC         VideoLAN.VLC     3.0.20"
            ),
            "stderr": "",
            "return_code": 0,
        }
        result = await PackageSearchTool().execute(
            PackageSearchArgs(query="vlc"),
        )
    assert result.success
    assert "VLC" in (result.output or "")


async def test_search_no_results():
    with patch("jarvis.tools.local.package_manager._run_winget") as mock_wg:
        mock_wg.return_value = {
            "success": True,
            "output": "No package found matching input criteria.",
            "stderr": "",
            "return_code": 0,
        }
        result = await PackageSearchTool().execute(
            PackageSearchArgs(query="asdfnonexistent"),
        )
    assert result.success
    assert "no packages" in (result.output or "").lower()


async def test_search_winget_not_found():
    with patch("jarvis.tools.local.package_manager._run_winget") as mock_wg:
        mock_wg.return_value = {
            "success": False,
            "error": "winget not found. Windows 10/11 required.",
        }
        result = await PackageSearchTool().execute(
            PackageSearchArgs(query="vlc"),
        )
    assert not result.success
    assert "winget" in (result.error or "").lower()


# --- PackageInstallTool ----------------------------------------------------

async def test_install_success():
    with patch("jarvis.tools.local.package_manager._run_winget") as mock_wg:
        mock_wg.return_value = {
            "success": True,
            "output": "Successfully installed",
            "stderr": "",
            "return_code": 0,
        }
        result = await PackageInstallTool().execute(
            PackageInstallArgs(package="VideoLAN.VLC"),
        )
    assert result.success
    assert "installed" in (result.output or "").lower()


async def test_install_failure():
    with patch("jarvis.tools.local.package_manager._run_winget") as mock_wg:
        mock_wg.return_value = {
            "success": False,
            "error": "No applicable installer found.",
            "stderr": "No applicable installer found.",
            "return_code": 1,
        }
        result = await PackageInstallTool().execute(
            PackageInstallArgs(package="nonexistent.pkg"),
        )
    assert not result.success


# --- PackageUninstallTool --------------------------------------------------

async def test_uninstall_success():
    with patch("jarvis.tools.local.package_manager._run_winget") as mock_wg:
        mock_wg.return_value = {
            "success": True,
            "output": "Successfully uninstalled",
            "stderr": "",
            "return_code": 0,
        }
        result = await PackageUninstallTool().execute(
            PackageUninstallArgs(package="VideoLAN.VLC"),
        )
    assert result.success
    assert "uninstalled" in (result.output or "").lower()


def test_requires_confirmation_false():
    """Install/uninstall are gated via TOOL_RISK_MAP = DESTRUCTIVE."""
    assert PackageSearchTool().requires_confirmation is False
    assert PackageInstallTool().requires_confirmation is False
    assert PackageUninstallTool().requires_confirmation is False
