"""Persist the last-used reply language across restarts (Task 7.3).

On boot JARVIS used to always start on the English voice and only switch
after the first non-English utterance — a 2-3 s reload mid-reply. Recording
the last language the assistant actually spoke lets boot pre-select that
voice instead.

Storage: ``data_path("state", "last_language.json")`` written atomically via
jarvis.core.fileio. The file is advisory, never authoritative: a missing,
unreadable, or stale value is NOT an error — we fall back to the default and
log at DEBUG. The stored language is only honoured when it is still one of
the currently enabled languages (the voice registry's assignments).
"""

from __future__ import annotations

import json
import logging

from jarvis.core.fileio import atomic_write_json
from jarvis.paths import data_path

log = logging.getLogger(__name__)

_STATE_PARTS = ("state", "last_language.json")


def load_last_language(
    enabled_languages: set[str] | frozenset[str],
    *,
    default: str,
) -> str:
    """Return the persisted last language if it is still enabled, else default.

    A missing/corrupt file or a language no longer in *enabled_languages*
    yields *default* with a DEBUG line — never raises.
    """
    path = data_path(*_STATE_PARTS)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.debug("last_language: no state file, using default %r", default)
        return default
    except OSError as exc:
        log.debug("last_language: unreadable (%s), using default %r", exc, default)
        return default
    try:
        data = json.loads(raw)
        lang = data["language"]
    except (ValueError, TypeError, KeyError) as exc:
        log.debug("last_language: corrupt (%s), using default %r", exc, default)
        return default
    if not isinstance(lang, str) or lang not in enabled_languages:
        log.debug(
            "last_language: %r not in enabled %s, using default %r",
            lang, sorted(enabled_languages), default,
        )
        return default
    log.debug("last_language: restored %r", lang)
    return lang


def save_last_language(language: str) -> None:
    """Persist *language* as the last-used reply language (best-effort).

    Callers should only invoke this when the language actually changed, to
    avoid a disk write on every turn. Failures are swallowed at DEBUG — this
    is advisory state and must never break a voice turn.
    """
    try:
        path = data_path(*_STATE_PARTS, create=True)
        atomic_write_json(path, {"language": language}, ensure_ascii=False)
        log.debug("last_language: saved %r", language)
    except Exception as exc:
        log.debug("last_language: save failed (%s)", exc)
