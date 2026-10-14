"""Tests for jarvis.tools.local.app_aliases — multilingual alias dictionary."""

from __future__ import annotations

import pytest

from jarvis.tools.local.app_aliases import APP_ALIASES, resolve_app_alias


# ── Structural invariants of the dictionary ────────────────────────────


class TestAliasDictStructure:
    """Validate the alias dictionary itself (keys, values, coverage)."""

    def test_all_keys_lowercase(self):
        """Every alias key must already be lowercase (lookup lowercases the
        input, so an upper-case key would silently never match)."""
        bad = [k for k in APP_ALIASES if k != k.lower()]
        assert bad == [], f"Keys not lowercase: {bad}"

    def test_all_keys_stripped(self):
        """No leading / trailing whitespace in keys."""
        bad = [k for k in APP_ALIASES if k != k.strip()]
        assert bad == [], f"Keys with whitespace: {bad}"

    def test_all_values_non_empty(self):
        """Every alias must resolve to a non-empty launch target."""
        bad = [k for k, v in APP_ALIASES.items() if not v or not v.strip()]
        assert bad == [], f"Keys with empty values: {bad}"

    def test_minimum_unique_target_count(self):
        """At least 70 distinct target programs are covered."""
        unique = set(APP_ALIASES.values())
        assert len(unique) >= 70, (
            f"Only {len(unique)} unique targets, expected ≥70"
        )

    def test_minimum_alias_count(self):
        """Dictionary has a healthy number of total entries."""
        assert len(APP_ALIASES) >= 150, (
            f"Only {len(APP_ALIASES)} entries, expected ≥150"
        )


# ── resolve_app_alias() ── RU ─────────────────────────────────────────


class TestResolveRu:
    """Top-10 Russian (RU) spoken aliases."""

    @pytest.mark.parametrize(
        "alias, expected",
        [
            ("хром", "chrome"),
            ("гугл хром", "chrome"),
            ("телеграм", "telegram"),
            ("телеграмм", "telegram"),
            ("телега", "telegram"),
            ("ворд", "winword"),
            ("эксель", "excel"),
            ("блокнот", "notepad"),
            ("калькулятор", "calc"),
            ("проводник", "explorer"),
            ("дискорд", "discord"),
            ("спотифай", "spotify"),
            ("настройки", "ms-settings:"),
            ("диспетчер задач", "taskmgr"),
            ("командная строка", "cmd"),
        ],
    )
    def test_ru_alias(self, alias: str, expected: str):
        assert resolve_app_alias(alias) == expected


# ── resolve_app_alias() ── UK ─────────────────────────────────────────


class TestResolveUk:
    """Top-10 Ukrainian (UK) spoken aliases."""

    @pytest.mark.parametrize(
        "alias, expected",
        [
            ("провідник", "explorer"),
            ("налаштування", "ms-settings:"),
            ("діскорд", "discord"),
            ("тімс", "ms-teams"),
            ("стім", "steam"),
            ("ножиці", "snippingtool"),
            ("ексель", "excel"),
            ("кошик", "shell:RecycleBinFolder"),
            ("магазин додатків", "ms-windows-store:"),
            ("медіаплеєр", "wmplayer"),
        ],
    )
    def test_uk_alias(self, alias: str, expected: str):
        assert resolve_app_alias(alias) == expected


# ── resolve_app_alias() ── EN informal ────────────────────────────────


class TestResolveEn:
    """Top-10 English informal aliases (short forms, common misspellings)."""

    @pytest.mark.parametrize(
        "alias, expected",
        [
            ("chrome", "chrome"),
            ("vscode", "code"),
            ("vs code", "code"),
            ("edge", "msedge"),
            ("cmd", "cmd"),
            ("terminal", "wt"),
            ("task manager", "taskmgr"),
            ("notepad++", "notepad++"),
            ("vlc", "vlc"),
            ("rdp", "mstsc"),
            ("obs", "obs64"),
            ("git bash", "git-bash"),
        ],
    )
    def test_en_alias(self, alias: str, expected: str):
        assert resolve_app_alias(alias) == expected


# ── Edge cases ────────────────────────────────────────────────────────


class TestResolveEdgeCases:

    def test_case_insensitive_ru(self):
        """Cyrillic input is lowered before lookup."""
        assert resolve_app_alias("ХРОМ") == "chrome"
        assert resolve_app_alias("Телеграм") == "telegram"

    def test_case_insensitive_en(self):
        assert resolve_app_alias("Chrome") == "chrome"
        assert resolve_app_alias("VLC") == "vlc"
        assert resolve_app_alias("VSCODE") == "code"

    def test_strips_whitespace(self):
        """Leading / trailing whitespace is ignored."""
        assert resolve_app_alias("  хром  ") == "chrome"
        assert resolve_app_alias("\tchrome\n") == "chrome"

    def test_fallback_preserves_original(self):
        """Unknown names return the original string unchanged."""
        assert resolve_app_alias("some_custom_app") == "some_custom_app"
        assert resolve_app_alias("MySpecialTool") == "MySpecialTool"

    def test_fallback_preserves_case(self):
        """Fallback returns the original casing, not the lowered form."""
        assert resolve_app_alias("UnknownApp") == "UnknownApp"

    def test_empty_string_falls_through(self):
        """Empty input just returns the original (caller handles empty)."""
        assert resolve_app_alias("") == ""

    def test_tg_shorthand(self):
        """Telegram's 'тг' shorthand, commonly used in RU voice."""
        assert resolve_app_alias("тг") == "telegram"


# ── Backward-compat: _APP_ALIASES is the same object ─────────────────


def test_open_app_still_exports_app_aliases():
    """close_app and launch_workspace import _APP_ALIASES from open_app.
    That name must resolve to the extended dictionary."""
    from jarvis.tools.local.open_app import _APP_ALIASES

    assert _APP_ALIASES is APP_ALIASES
    assert "хром" in _APP_ALIASES
    assert _APP_ALIASES["chrome"] == "chrome"
