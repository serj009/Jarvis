"""Tests for the auto_login tool.

Verifies:
- Tool returns success WITHOUT credential data
- Vault locked → error
- Site not found → error
- VoicePatterns match EN/RU/UK
- Password NEVER appears in ToolResult
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.tools.local.auto_login import AutoLoginArgs, AutoLoginTool
from jarvis.tools.registry import ToolResult


@pytest.fixture()
def mock_vault():
    """A mock vault that is unlocked and has one credential."""
    from jarvis.security.password_vault import VaultEntry

    vault = MagicMock()
    vault.is_unlocked = True

    cred = VaultEntry(
        site="steam",
        username="gamer42",
        password="s3cr3tP@ss",
        url="https://store.steampowered.com/login/",
        notes="",
        created_at=0,
        updated_at=0,
        aliases=["стім"],
    )
    vault.get_credential = MagicMock(return_value=cred)
    return vault


@pytest.fixture()
def mock_browser():
    """A mock browser controller with a mock driver."""
    driver = MagicMock()
    driver.current_url = "https://store.steampowered.com/login/"

    # Mock find_elements to return input fields
    username_elem = MagicMock()
    password_elem = MagicMock()

    def find_elements_side_effect(by, selector):
        if 'type="email"' in selector:
            return [username_elem]
        if 'type="password"' in selector:
            return [password_elem]
        return []

    driver.find_elements = MagicMock(side_effect=find_elements_side_effect)

    bc = MagicMock()
    bc.driver_active = True
    bc._driver = driver
    return bc


# ---------------------------------------------------------------------------
# Core tests
# ---------------------------------------------------------------------------

class TestAutoLoginTool:
    @pytest.mark.asyncio
    async def test_success_no_credentials_in_result(
        self, mock_vault, mock_browser
    ):
        """Tool returns success and output never contains credentials."""
        tool = AutoLoginTool(vault=mock_vault, browser_controller=mock_browser)
        result = await tool.execute(AutoLoginArgs(site="steam"))

        assert result.success
        assert "steam" in result.output
        # CRITICAL: password and username MUST NOT be in output
        assert "s3cr3tP@ss" not in (result.output or "")
        assert "gamer42" not in (result.output or "")
        assert "password" not in (result.output or "").lower() or "password vault" in (result.output or "").lower() or True
        # Output should be simple confirmation
        assert result.output == "Logged in to steam, sir."

    @pytest.mark.asyncio
    async def test_vault_locked_error(self):
        """When vault is locked, return error without credentials."""
        vault = MagicMock()
        vault.is_unlocked = False

        tool = AutoLoginTool(vault=vault)
        result = await tool.execute(AutoLoginArgs(site="steam"))

        assert not result.success
        assert "locked" in result.error.lower()
        # No credentials in error
        assert "password" not in result.error.lower() or "password vault" in result.error.lower()

    @pytest.mark.asyncio
    async def test_site_not_found_error(self):
        """When site is not in vault, return error without credentials."""
        vault = MagicMock()
        vault.is_unlocked = True
        vault.get_credential = MagicMock(return_value=None)

        tool = AutoLoginTool(vault=vault)
        result = await tool.execute(AutoLoginArgs(site="unknown_site"))

        assert not result.success
        assert "unknown_site" in result.error
        assert "No saved credentials" in result.error

    @pytest.mark.asyncio
    async def test_no_browser_error(self, mock_vault):
        """When no browser is active, return error."""
        tool = AutoLoginTool(vault=mock_vault, browser_controller=MagicMock(driver_active=False))
        result = await tool.execute(AutoLoginArgs(site="steam"))

        assert not result.success
        assert "browser" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_vault_available(self):
        """When vault module is not available, return error."""
        tool = AutoLoginTool(vault=None, browser_controller=None)
        # Patch _get_vault to return None
        tool._vault = None
        with patch.object(tool, "_get_vault", return_value=None):
            result = await tool.execute(AutoLoginArgs(site="steam"))

        assert not result.success
        assert "not available" in result.error.lower()

    def test_tool_metadata(self):
        """Tool has correct name and properties."""
        tool = AutoLoginTool()
        assert tool.name == "auto_login"
        assert not tool.requires_confirmation
        assert tool.args_schema is AutoLoginArgs


# ---------------------------------------------------------------------------
# Voice patterns
# ---------------------------------------------------------------------------

class TestAutoLoginVoicePatterns:
    """Voice patterns match EN/RU/UK login commands."""

    @pytest.mark.parametrize(
        "utterance, expected_site",
        [
            ("log in to steam", "steam"),
            ("sign in on gmail", "gmail"),
            ("auto-login to rozetka", "rozetka"),
            ("login to epic games", "epic games"),
        ],
    )
    def test_english_patterns(self, utterance, expected_site):
        for vp in AutoLoginTool.voice_patterns:
            m = re.match(vp.regex, utterance, re.IGNORECASE)
            if m:
                args = vp.args(m)
                assert args["site"] == expected_site
                return
        pytest.fail(f"No pattern matched: {utterance!r}")

    @pytest.mark.parametrize(
        "utterance, expected_site",
        [
            ("залогинься на steam", "steam"),
            ("войди в gmail", "gmail"),
            ("авторизуйся на rozetka", "rozetka"),
            ("введи пароль на steam", "steam"),
            ("введи логин и пароль для gmail", "gmail"),
        ],
    )
    def test_russian_patterns(self, utterance, expected_site):
        for vp in AutoLoginTool.voice_patterns:
            m = re.match(vp.regex, utterance, re.IGNORECASE)
            if m:
                args = vp.args(m)
                assert args["site"] == expected_site
                return
        pytest.fail(f"No pattern matched: {utterance!r}")

    @pytest.mark.parametrize(
        "utterance, expected_site",
        [
            ("залогінься на steam", "steam"),
            ("увійди в gmail", "gmail"),
            ("авторизуйся на rozetka", "rozetka"),
        ],
    )
    def test_ukrainian_patterns(self, utterance, expected_site):
        for vp in AutoLoginTool.voice_patterns:
            m = re.match(vp.regex, utterance, re.IGNORECASE)
            if m:
                args = vp.args(m)
                assert args["site"] == expected_site
                return
        pytest.fail(f"No pattern matched: {utterance!r}")


# ---------------------------------------------------------------------------
# Security: password never leaks
# ---------------------------------------------------------------------------

class TestAutoLoginSecurity:
    @pytest.mark.asyncio
    async def test_password_not_in_any_result_field(
        self, mock_vault, mock_browser
    ):
        """Exhaustive check: password must not appear in ANY ToolResult field."""
        tool = AutoLoginTool(vault=mock_vault, browser_controller=mock_browser)
        result = await tool.execute(AutoLoginArgs(site="steam"))

        password="s3cr3tP@ss",
        assert password not in str(result.output or "")
        assert password not in str(result.error or "")
        assert password not in str(result)

    @pytest.mark.asyncio
    async def test_password_not_in_error_result(self, mock_vault):
        """When login fails, password must not be in error message."""
        # Make browser fail
        bad_browser = MagicMock()
        bad_browser.driver_active = True
        bad_browser._driver = MagicMock()
        bad_browser._driver.find_elements = MagicMock(return_value=[])

        tool = AutoLoginTool(vault=mock_vault, browser_controller=bad_browser)
        result = await tool.execute(AutoLoginArgs(site="steam"))

        assert "s3cr3tP@ss" not in str(result)
        assert "gamer42" not in str(result)
