"""Tests for jarvis.tools.local.smart_form_fill — SmartFormFillTool.

Fully mocked: no real Selenium, no real profile.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.smart_form_fill import (
    SmartFormFillTool,
    _fill_via_selenium,
    _load_profile,
    _match_field,
)
from jarvis.tools.registry import EmptyArgs

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

FAKE_PROFILE = {
    "name": "Serhii",
    "email": "serhii@example.com",
    "phone": "+48123456789",
    "city": "Zielona Góra",
    "address": "ul. Testowa 1",
    "country": "Poland",
}


def _make_elem(
    name: str = "",
    elem_id: str = "",
    placeholder: str = "",
    input_type: str = "text",
    aria_label: str = "",
) -> MagicMock:
    """Build a fake Selenium WebElement."""
    elem = MagicMock()
    attrs = {
        "name": name,
        "id": elem_id,
        "placeholder": placeholder,
        "type": input_type,
        "aria-label": aria_label,
    }
    elem.get_attribute.side_effect = lambda k: attrs.get(k, "")
    return elem


def _make_driver(elements: list[MagicMock]) -> MagicMock:
    driver = MagicMock()
    driver.find_elements.return_value = elements
    # Label lookup always fails (no labels).
    driver.find_element.side_effect = Exception("no label")
    return driver


def _make_browser(driver: MagicMock | None = None) -> MagicMock:
    bc = MagicMock()
    bc.driver_active = driver is not None
    bc._driver = driver
    return bc


# ---------------------------------------------------------------------------
# Unit tests — _match_field
# ---------------------------------------------------------------------------

def test_match_name_field():
    attrs = {"name": "first_name", "id": "", "placeholder": "", "aria-label": "", "label": ""}
    result = _match_field(attrs, FAKE_PROFILE)
    assert result is not None
    key, val = result
    assert key == "name"
    assert val == "Serhii"


def test_match_email_field():
    attrs = {"name": "email", "id": "", "placeholder": "", "aria-label": "", "label": ""}
    result = _match_field(attrs, FAKE_PROFILE)
    assert result is not None
    assert result[0] == "email"


def test_match_phone_field():
    attrs = {"name": "", "id": "phone", "placeholder": "Телефон", "aria-label": "", "label": ""}
    result = _match_field(attrs, FAKE_PROFILE)
    assert result is not None
    assert result[0] == "phone"


def test_match_city_field():
    attrs = {"name": "city", "id": "", "placeholder": "", "aria-label": "", "label": "City"}
    result = _match_field(attrs, FAKE_PROFILE)
    assert result is not None
    assert result[0] == "city"


def test_no_match_unknown_field():
    attrs = {"name": "captcha", "id": "", "placeholder": "", "aria-label": "", "label": ""}
    result = _match_field(attrs, FAKE_PROFILE)
    assert result is None


def test_no_match_empty_profile():
    attrs = {"name": "email", "id": "", "placeholder": "", "aria-label": "", "label": ""}
    result = _match_field(attrs, {})
    assert result is None


def test_match_russian_label():
    attrs = {"name": "", "id": "", "placeholder": "Ваше имя", "aria-label": "", "label": ""}
    result = _match_field(attrs, FAKE_PROFILE)
    assert result is not None
    assert result[0] == "name"


def test_match_address_field():
    attrs = {"name": "address", "id": "", "placeholder": "", "aria-label": "", "label": "Address"}
    result = _match_field(attrs, FAKE_PROFILE)
    assert result is not None
    assert result[0] == "address"


# ---------------------------------------------------------------------------
# Unit test — _fill_via_selenium
# ---------------------------------------------------------------------------

@patch("jarvis.tools.local.smart_form_fill._load_profile", return_value=FAKE_PROFILE)
def test_fill_via_selenium_matches_fields(mock_profile):
    elements = [
        _make_elem(name="first_name"),
        _make_elem(name="email"),
        _make_elem(name="unknown_field"),
        _make_elem(name="submit", input_type="submit"),
    ]
    driver = _make_driver(elements)
    filled, total, labels = _fill_via_selenium(driver)
    assert filled == 2  # first_name + email
    assert total == 4
    assert len(labels) == 2


@patch("jarvis.tools.local.smart_form_fill._load_profile", return_value={})
def test_fill_via_selenium_empty_profile(mock_profile):
    elements = [_make_elem(name="email")]
    driver = _make_driver(elements)
    filled, total, labels = _fill_via_selenium(driver)
    assert filled == 0


@patch("jarvis.tools.local.smart_form_fill._load_profile", return_value=FAKE_PROFILE)
def test_fill_skips_hidden_submit(mock_profile):
    elements = [
        _make_elem(name="hidden_field", input_type="hidden"),
        _make_elem(name="submit_btn", input_type="submit"),
        _make_elem(name="radio_choice", input_type="radio"),
        _make_elem(name="email"),
    ]
    driver = _make_driver(elements)
    filled, total, labels = _fill_via_selenium(driver)
    assert filled == 1  # only email


# ---------------------------------------------------------------------------
# Tool-level async tests
# ---------------------------------------------------------------------------

async def test_fill_form_no_browser():
    tool = SmartFormFillTool(browser_controller=None)
    result = await tool.execute(EmptyArgs())
    assert not result.success
    assert "selenium" in (result.error or "").lower() or "browser" in (result.error or "").lower()


async def test_fill_form_inactive_browser():
    bc = _make_browser(driver=None)
    tool = SmartFormFillTool(browser_controller=bc)
    result = await tool.execute(EmptyArgs())
    assert not result.success


@patch("jarvis.tools.local.smart_form_fill._load_profile", return_value=FAKE_PROFILE)
async def test_fill_form_success(mock_profile):
    elements = [
        _make_elem(name="first_name"),
        _make_elem(name="email"),
        _make_elem(name="phone"),
    ]
    driver = _make_driver(elements)
    bc = _make_browser(driver)
    tool = SmartFormFillTool(browser_controller=bc)
    result = await tool.execute(EmptyArgs())
    assert result.success
    assert "3" in (result.output or "") or "Filled" in (result.output or "")


@patch("jarvis.tools.local.smart_form_fill._load_profile", return_value=FAKE_PROFILE)
async def test_fill_form_no_matches(mock_profile):
    elements = [
        _make_elem(name="captcha"),
        _make_elem(name="token", input_type="hidden"),
    ]
    driver = _make_driver(elements)
    bc = _make_browser(driver)
    tool = SmartFormFillTool(browser_controller=bc)
    result = await tool.execute(EmptyArgs())
    assert result.success  # success but 0 filled
    assert "could not match" in (result.output or "").lower()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

def test_requires_confirmation_false():
    assert SmartFormFillTool().requires_confirmation is False


def test_has_voice_patterns():
    patterns = SmartFormFillTool.voice_patterns
    assert len(patterns) >= 9  # 3 EN + 3 RU + 3 UK
