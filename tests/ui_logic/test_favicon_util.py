"""Tests for jarvis.ui.favicon_util — pure, Qt-free favicon helpers (Task 9a).

No PySide6 import, so these run in the headless .test_venv. The Qt network/
widget code in research_panel.py is verified at home.
"""

from __future__ import annotations

import pytest

from jarvis.ui.favicon_util import (
    FAVICON_SIZE_CAP,
    avatar_color,
    avatar_letter,
    exceeds_size,
    favicon_url,
    is_valid_favicon_domain,
    normalize_domain,
)

# --- normalize_domain -------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("example.com", "example.com"),
        ("https://example.com/path?q=1", "example.com"),
        ("http://Example.COM:8443/x", "example.com"),
        ("sub.example.co.uk", "sub.example.co.uk"),
        ("example.com/some/path", "example.com"),
        ("  Example.Com  ", "example.com"),
        ("", ""),
    ],
)
def test_normalize_domain(raw, expected):
    assert normalize_domain(raw) == expected


# --- is_valid_favicon_domain ------------------------------------------------


@pytest.mark.parametrize(
    "domain",
    ["example.com", "sub.example.co.uk", "a.b.c.example.org", "xn--80ak6aa92e.com"],
)
def test_valid_domains(domain):
    assert is_valid_favicon_domain(domain)


@pytest.mark.parametrize(
    "bad",
    [
        "", "example", "ex_ample.com", "not a domain",
        "javascript:alert(1)", "example..com", "-example.com",
        "example.com/path", "http://example.com", "exa mple.com",
    ],
)
def test_invalid_domains(bad):
    assert not is_valid_favicon_domain(bad)


# --- favicon_url ------------------------------------------------------------


def test_favicon_url_off_is_none():
    assert favicon_url("example.com", "off") is None


def test_favicon_url_direct():
    assert favicon_url("example.com", "direct") == "https://example.com/favicon.ico"


def test_favicon_url_google():
    assert (
        favicon_url("example.com", "google")
        == "https://www.google.com/s2/favicons?domain=example.com&sz=32"
    )


def test_favicon_url_unknown_mode_is_none():
    # An unknown / corrupt mode must behave like "off" (no network).
    assert favicon_url("example.com", "banana") is None
    assert favicon_url("example.com", "") is None


def test_favicon_url_normalizes_input():
    assert favicon_url("https://Example.COM/x", "direct") == "https://example.com/favicon.ico"


def test_favicon_url_rejects_bad_domain():
    assert favicon_url("not a domain", "direct") is None
    assert favicon_url("javascript:alert(1)", "google") is None
    assert favicon_url("", "direct") is None


def test_favicon_url_is_always_https():
    for mode in ("direct", "google"):
        url = favicon_url("example.com", mode)
        assert url is not None and url.startswith("https://")


# --- avatar_letter ----------------------------------------------------------


@pytest.mark.parametrize(
    "domain, letter",
    [
        ("example.com", "E"),
        ("github.com", "G"),
        ("123shop.net", "1"),
        ("", "?"),
        ("https://wikipedia.org", "W"),
    ],
)
def test_avatar_letter(domain, letter):
    assert avatar_letter(domain) == letter


# --- avatar_color -----------------------------------------------------------


def test_avatar_color_is_deterministic():
    # Same domain -> same color across calls (and runs: stable hash).
    assert avatar_color("example.com") == avatar_color("example.com")
    assert avatar_color("https://Example.COM/x") == avatar_color("example.com")


def test_avatar_color_is_hex():
    c = avatar_color("github.com")
    assert c.startswith("#") and len(c) == 7


def test_avatar_color_varies_across_domains():
    colors = {avatar_color(d) for d in
              ("a.com", "b.com", "c.com", "d.com", "e.com", "f.com")}
    # Not all identical — the hash spreads across the palette.
    assert len(colors) > 1


# --- exceeds_size -----------------------------------------------------------


def test_exceeds_size():
    assert not exceeds_size(0)
    assert not exceeds_size(1000)
    assert not exceeds_size(FAVICON_SIZE_CAP)
    assert exceeds_size(FAVICON_SIZE_CAP + 1)
    assert exceeds_size(10 * 1024 * 1024)


def test_exceeds_size_custom_cap():
    assert exceeds_size(101, cap=100)
    assert not exceeds_size(100, cap=100)


# --- config field -----------------------------------------------------------


def test_research_favicons_default_is_off():
    from jarvis.core.config import ResearchConfig

    assert ResearchConfig().favicons == "off"


@pytest.mark.parametrize("value", ["off", "direct", "google"])
def test_research_favicons_accepts_valid(value):
    from jarvis.core.config import ResearchConfig

    assert ResearchConfig(favicons=value).favicons == value


@pytest.mark.parametrize("bad", ["GOOGLE", "", "banana", None, 123])
def test_research_favicons_invalid_falls_back_to_off(bad):
    """A corrupt/old config value must not crash startup — it falls back."""
    from jarvis.core.config import ResearchConfig

    assert ResearchConfig(favicons=bad).favicons == "off"
