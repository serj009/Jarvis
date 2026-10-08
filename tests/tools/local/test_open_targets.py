"""App-first / website-fallback planning for known open targets."""

from __future__ import annotations

import pytest

from jarvis.tools.local.open_targets import (
    DEFAULT_TARGETS,
    _compact,
    _normalize,
    parse_open_command,
    plan_open,
)


def _installed(*keys: str):
    keys_set = set(keys)
    return lambda key: key in keys_set


@pytest.mark.parametrize(
    "text,installed,kind,value,mode",
    [
        # app-first standard
        ("Открой Steam.", ("steam",), "app", "steam", "auto"),
        ("Відкрий стім", ("steam",), "app", "steam", "auto"),
        ("Открой телеграм", ("telegram desktop",), "app", "telegram desktop", "auto"),
        # app missing -> website
        ("Открой YouTube.", (), "url", "https://www.youtube.com", "auto"),
        ("Открой телеграм", (), "url", "https://web.telegram.org", "auto"),
        ("Запусти приложение телеграм", (), "url", "https://web.telegram.org", "app"),
        # browser forces the website even when the app is installed
        ("Открой стим в браузере", ("steam",), "url", "https://store.steampowered.com", "web"),
        ("Відкрий стім у браузері", ("steam",), "url", "https://store.steampowered.com", "web"),
        ("open steam website", ("steam",), "url", "https://store.steampowered.com", "web"),
        ("Открой сайт ютуба", (), "url", "https://www.youtube.com", "web"),
        # Whisper variants seen in live logs
        ("Открою YouTube в браузере", (), "url", "https://www.youtube.com", "web"),
        ("Открои GitHub", (), "url", "https://github.com", "auto"),
        ("Открой гит хаб", (), "url", "https://github.com", "auto"),
        ("Джарвис, открой ютуб, пожалуйста", (), "url", "https://www.youtube.com", "auto"),
    ],
)
def test_plan(text, installed, kind, value, mode):
    plan = plan_open(text, _installed(*installed))
    assert plan is not None
    assert (plan.kind, plan.value, plan.mode) == (kind, value, mode)


def test_google_never_launches_chrome():
    """Exact app keys only: fuzzy matching would map "google" to Chrome."""
    plan = plan_open("Открой гугл", _installed("google chrome", "chrome"))
    assert plan is not None
    assert (plan.kind, plan.value) == ("url", "https://www.google.com")


@pytest.mark.parametrize(
    "text",
    [
        "Открой блокнот",            # not a known target -> LLM / open_app
        "Сколько будет два плюс два",
        "Открой",
        "Открой в браузере",
        "Найди в интернете погоду",
        "YouTube",                   # no verb
    ],
)
def test_not_a_known_target(text):
    assert plan_open(text, _installed("steam")) is None
    assert parse_open_command(text) is None


def test_default_targets_are_consistent():
    seen: dict[str, str] = {}
    for target in DEFAULT_TARGETS:
        assert target.url or target.apps, target.key
        if target.url:
            assert target.url.startswith("https://"), target.key
        for name in target.names:
            compact = _compact(_normalize(name))
            assert compact, (target.key, name)
            assert compact not in seen, f"{name!r}: {target.key} vs {seen.get(compact)}"
            seen[compact] = target.key
        for app in target.apps:
            assert app == app.lower() and app.isascii(), (target.key, app)
