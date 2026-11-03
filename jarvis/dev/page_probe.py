"""Page probe: what text would "remember this page" read? (dev tool)

    python -m jarvis.dev.page_probe "https://steamcommunity.com/sharedfiles/filedetails/?id=..."
    python -m jarvis.dev.page_probe            # the page open in your browser

Downloads the page the way save_page_knowledge does and writes three files
into the current folder, so you can see what the model gets:

    page_probe.html       the raw HTML
    page_probe_full.txt   the whole page text (what was sent before cleaning)
    page_probe_main.txt   the main content after cleaning (what is sent now)

and prints their sizes plus the parts the extractor would send. It does not
call the LLM and does not save anything to Jarvis' memory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What text would \"remember this page\" read?")
    parser.add_argument("url", nargs="?", default="", help="page URL (default: browser tab)")
    parser.add_argument("--out", default=".", help="folder for the output files")
    args = parser.parse_args(argv)

    from jarvis.browser.readable import extract_main_text
    from jarvis.memory.page_extractor import split_into_chunks
    from jarvis.tools.local.content_reader import _extract_readable, _httpx_fetch

    url = args.url
    if not url:
        from jarvis.platform.windows import active_browser_url

        url = active_browser_url() or ""
        if not url:
            print("No URL given and no browser page found.", file=sys.stderr)
            return 2
        print(f"browser page: {url}")

    fetched = _httpx_fetch(url, timeout=20.0)
    if not fetched.get("success"):
        print(f"fetch failed: {fetched.get('error')}", file=sys.stderr)
        return 1
    html = fetched["html"]
    full = _extract_readable(html, max_chars=1_000_000)
    main_text = extract_main_text(html, max_chars=1_000_000)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "page_probe.html").write_text(html, encoding="utf-8")
    (out / "page_probe_full.txt").write_text(full, encoding="utf-8")
    (out / "page_probe_main.txt").write_text(main_text, encoding="utf-8")

    parts = split_into_chunks(main_text)
    print(f"html:          {len(html):>8} chars")
    print(f"full text:     {len(full):>8} chars, {len(full.splitlines())} lines")
    print(f"main content:  {len(main_text):>8} chars, {len(main_text.splitlines())} lines")
    print(f"sent to model: {len(parts)} part(s)")
    print(f"files written to {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
