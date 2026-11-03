"""jarvis.dev.page_probe: shows what text "remember this page" would read."""

from __future__ import annotations

from unittest.mock import patch

from jarvis.dev.page_probe import main

_HTML = (
    "<html><body><div id='global_header'><a>Магазин</a></div>"
    "<div class='guide'><div class='subSectionTitle'>Крылья феникса</div>"
    "<div class='subSectionDesc'>покупаем 10 видов</div></div></body></html>"
)


def test_writes_raw_full_and_main_text(tmp_path, capsys):
    with patch(
        "jarvis.tools.local.content_reader._httpx_fetch",
        return_value={"success": True, "html": _HTML, "url": "https://x"},
    ):
        assert main(["https://x", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "page_probe.html").read_text(encoding="utf-8") == _HTML
    assert "Магазин" in (tmp_path / "page_probe_full.txt").read_text(encoding="utf-8")
    main_text = (tmp_path / "page_probe_main.txt").read_text(encoding="utf-8")
    assert "## Крылья феникса" in main_text
    assert "Магазин" not in main_text
    assert "sent to model: 1 part(s)" in capsys.readouterr().out


def test_fetch_failure(tmp_path):
    with patch(
        "jarvis.tools.local.content_reader._httpx_fetch",
        return_value={"success": False, "error": "No internet connection."},
    ):
        assert main(["https://x", "--out", str(tmp_path)]) == 1


def test_no_url_and_no_browser_page(tmp_path):
    with patch("jarvis.platform.windows.active_browser_url", return_value=None):
        assert main(["--out", str(tmp_path)]) == 2
