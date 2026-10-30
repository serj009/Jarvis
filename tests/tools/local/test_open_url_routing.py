"""Task 8a: 'open site X' / 'open X.tld' route to open_url, not file_manager.

Pure pattern/arg-builder checks (no browser, no sounddevice): compile each
tool's voice_patterns and confirm which tool claims an utterance and what
URL it builds. Covers the live bug where "открой сайт uakino.best" was
swallowed by file_manager's extension pattern and sent to a web search.
"""

from __future__ import annotations

import re

from jarvis.tools.local.file_manager import FileManagerTool
from jarvis.tools.local.open_url import OpenUrlTool, _looks_like_domain


def _first_match(tool, utterance: str):
    """Return (priority, args) for the lowest-priority (= winning) voice
    pattern of *tool* that matches *utterance*, or None."""
    best = None
    for vp in tool.voice_patterns:
        m = re.match(vp.regex, utterance)
        if m is None:
            continue
        if best is None or vp.priority < best[0]:
            best = (vp.priority, vp.args(m))
    return best


# --- _looks_like_domain ------------------------------------------------

def test_domain_detection_true_for_real_domains() -> None:
    assert _looks_like_domain("uakino.best")
    assert _looks_like_domain("example.com")
    assert _looks_like_domain("example.co.uk")
    assert _looks_like_domain("sub.domain.io")


def test_domain_detection_false_for_queries_and_files() -> None:
    assert not _looks_like_domain("лучшие фильмы 2024")
    assert not _looks_like_domain("hotel california")
    assert not _looks_like_domain("report.docx")  # docx is not a TLD
    assert not _looks_like_domain("justoneword")


# --- "открой сайт X" builds the right URL ------------------------------

def test_open_site_domain_opens_directly() -> None:
    got = _first_match(OpenUrlTool(), "открой сайт uakino.best")
    assert got is not None
    assert got[1] == {"url": "https://uakino.best"}


def test_open_site_query_falls_back_to_search() -> None:
    got = _first_match(OpenUrlTool(), "открой сайт лучшие фильмы")
    assert got is not None
    assert got[1]["url"].startswith("https://www.google.com/search?q=")


def test_open_site_uk_domain() -> None:
    got = _first_match(OpenUrlTool(), "відкрий сайт example.com")
    assert got is not None
    assert got[1] == {"url": "https://example.com"}


# --- "открой X.tld" without the word "сайт" ----------------------------

def test_open_bare_domain_without_site_word() -> None:
    for utt in ("открой uakino.best", "open example.com", "відкрий test.org"):
        got = _first_match(OpenUrlTool(), utt)
        assert got is not None, utt
        assert got[1]["url"].startswith("https://"), utt
        assert "google.com/search" not in got[1]["url"], utt


# --- file_manager no longer steals domains -----------------------------

def test_file_manager_ignores_domain_token() -> None:
    """The extension patterns must NOT match a domain like uakino.best."""
    assert _first_match(FileManagerTool(), "открой uakino.best") is None
    assert _first_match(FileManagerTool(), "открой сайт uakino.best") is None
    assert _first_match(FileManagerTool(), "open example.com") is None


def test_file_manager_still_matches_real_files() -> None:
    """Real file extensions must still route to file_manager."""
    got = _first_match(FileManagerTool(), "открой report.docx")
    assert got is not None
    assert got[1]["action"] == "open"
    assert got[1]["path"] == "report.docx"
    got_en = _first_match(FileManagerTool(), "open my_photo.jpg")
    assert got_en is not None
    assert got_en[1]["path"] == "my_photo.jpg"


def test_file_wins_over_open_url_by_priority() -> None:
    """For a real file, file_manager (250) must beat open_url's bare-domain
    pattern (450) — the file is not a domain anyway, but assert the
    precedence explicitly so a future reorder can't silently regress it."""
    fm = _first_match(FileManagerTool(), "открой report.docx")
    ou = _first_match(OpenUrlTool(), "открой report.docx")
    assert fm is not None and fm[0] == 250
    # open_url's bare-domain pattern does match 'report.docx' structurally,
    # but its priority (450) is higher than file_manager's (250), so
    # file_manager wins the first-match-wins router.
    if ou is not None:
        assert fm[0] < ou[0]
