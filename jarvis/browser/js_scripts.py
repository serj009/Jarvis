"""Allowlisted JavaScript snippets for browser_controller.execute_js (Task 13).

Defense-in-depth: execute_js is internal-only (never an LLM tool) and is
only ever called with one of these fixed, code-authored scripts. Centralising
them here as the SINGLE source of truth means:

  * callers (media_control, content_reader) reference JS_SCRIPTS[...] instead
    of duplicating string literals, so the allowlist and its use cannot drift;
  * execute_js rejects any script not in ALLOWED_JS (exact match), so even a
    future bug that passes attacker-influenced JS is refused at the seam;
  * an AST guard test (tests/test_architecture_rules.py) forbids any
    execute_script / execute_js / execute_async_script call in jarvis/** whose
    first argument is not a literal / JS_SCRIPTS[...] reference — no f-strings,
    concatenation, or variables.

No Selenium import here — pure data, importable from anywhere.
"""

from __future__ import annotations

# name -> exact JavaScript source. The ONLY scripts execute_js will run.
JS_SCRIPTS: dict[str, str] = {
    # content_reader: read the page title.
    "read_title": "return document.title;",
    # media_control: control the first <video>/<audio> element.
    "media_pause": "var m=document.querySelector('video,audio'); if(m) m.pause();",
    "media_play": "var m=document.querySelector('video,audio'); if(m) m.play();",
    "media_toggle": (
        "var m=document.querySelector('video,audio');"
        "if(m){m.paused?m.play():m.pause();return !m.paused;}"
    ),
    "media_fullscreen": (
        "var v=document.querySelector('video'); if(v) v.requestFullscreen();"
    ),
    "media_exit_fullscreen": "document.exitFullscreen();",
    "media_skip_forward": (
        "var m=document.querySelector('video,audio');"
        "if(m) m.currentTime+=10;"
    ),
    "media_skip_backward": (
        "var m=document.querySelector('video,audio');"
        "if(m) m.currentTime-=10;"
    ),
}

# Exact-match allowlist checked by execute_js.
ALLOWED_JS: frozenset[str] = frozenset(JS_SCRIPTS.values())
