"""Tests for jarvis.platform.app_discovery."""

from __future__ import annotations

from jarvis.platform.app_discovery import (
    fuzzy_resolve,
    normalize_open_query,
    normalize_query,
)


def test_normalize_open_query_strips_command_words():
    assert normalize_open_query("hey jarvis please open Google Chrome") == "Google Chrome"


def test_normalize_query_strips_fillers_and_punctuation():
    assert normalize_query("My  Photoshop!") == "photoshop"


def test_fuzzy_resolve_exact_key():
    choices = {"visual studio code": "code"}
    assert fuzzy_resolve("Visual Studio Code", choices) == (
        "visual studio code",
        "code",
    )


def test_fuzzy_resolve_close_typo():
    choices = {"adobe photoshop": "/path/photoshop.lnk"}
    hit = fuzzy_resolve("photoshop", choices, cutoff=0.5)
    assert hit is not None
    assert hit[1] == "/path/photoshop.lnk"


def test_fuzzy_resolve_miss_returns_none():
    assert fuzzy_resolve("zzzzunknown", {"foo bar": 1}) is None


def test_fuzzy_resolve_single_word_matches_multiword_via_token_overlap():
    """The common real failure: a one-word spoken name against the full
    Start-Menu display name. Token overlap handles it."""
    choices = {
        "microsoft word": "word.lnk",
        "microsoft excel": "excel.lnk",
        "visual studio code": "code.lnk",
    }
    assert fuzzy_resolve("word", choices) == ("microsoft word", "word.lnk")
    assert fuzzy_resolve("excel", choices) == ("microsoft excel", "excel.lnk")
    assert fuzzy_resolve("code", choices) == ("visual studio code", "code.lnk")


def test_fuzzy_resolve_substring_prominence():
    """A prominent substring resolves even when difflib's whole-string
    ratio is low ('teams' inside 'microsoft teams classic')."""
    choices = {"microsoft teams classic": "teams.lnk"}
    hit = fuzzy_resolve("teams", choices)
    assert hit is not None
    assert hit[1] == "teams.lnk"


def test_fuzzy_resolve_prefix():
    choices = {"spotify": "spotify.lnk"}
    hit = fuzzy_resolve("spot", choices)
    assert hit is not None
    assert hit[1] == "spotify.lnk"


def test_fuzzy_resolve_typo_via_difflib():
    choices = {"photoshop": "ps.lnk"}
    hit = fuzzy_resolve("photoshpo", choices)
    assert hit is not None
    assert hit[1] == "ps.lnk"


def test_fuzzy_resolve_picks_best_of_several():
    """When several keys partially match, the highest-scoring wins."""
    choices = {
        "discord": "discord.lnk",
        "discord ptb": "ptb.lnk",
        "discord canary": "canary.lnk",
    }
    # Exact-ish single token should prefer the bare "discord".
    assert fuzzy_resolve("discord", choices) == ("discord", "discord.lnk")


def test_fuzzy_resolve_short_query_does_not_overmatch():
    """A 2-char query must not substring-match into unrelated long keys."""
    choices = {"calculator": "calc.lnk"}
    assert fuzzy_resolve("go", choices) is None


# --- Task 8b-1: Cyrillic transliteration --------------------------------

def test_transliterate_basic_ru():
    from jarvis.platform.app_discovery import transliterate_cyrillic
    assert transliterate_cyrillic("дискорд") == "diskord"
    assert transliterate_cyrillic("телеграм") == "telegram"
    assert transliterate_cyrillic("стим") == "stim"


def test_transliterate_leaves_latin_untouched():
    from jarvis.platform.app_discovery import transliterate_cyrillic
    assert transliterate_cyrillic("battle.net") == "battle.net"
    assert transliterate_cyrillic("Steam123") == "Steam123"  # case preserved here


def test_normalize_query_transliterates_cyrillic():
    # Before 8b-1 this returned "" (Latin-only cleanup erased Cyrillic).
    assert normalize_query("баттл нет") == "battl net"
    assert normalize_query("Дискорд") == "diskord"


def test_normalize_query_latin_unchanged_by_translit():
    assert normalize_query("Google Chrome") == "google chrome"
    assert normalize_query("Visual Studio Code") == "visual studio code"


def test_cyrillic_app_name_now_resolves():
    """A Cyrillic spoken name fuzzy-matches a Latin index key (the whole
    point of 8b-1 — previously it normalised to '' and matched nothing)."""
    choices = {"battle net": "battlenet.lnk"}
    hit = fuzzy_resolve("баттл нет", choices)
    assert hit is not None
    assert hit[1] == "battlenet.lnk"


def test_cyrillic_discord_resolves_via_typo_floor():
    choices = {"discord": "discord.lnk"}
    hit = fuzzy_resolve("дискорд", choices)
    assert hit is not None
    assert hit[1] == "discord.lnk"


def test_cyrillic_index_key_also_transliterated():
    """Transliteration is symmetric: a Cyrillic INDEX key and a Cyrillic
    query both normalise the same way, so they still match."""
    choices = {normalize_query("Телеграм"): "tg.lnk"}
    assert "telegram" in choices  # key was transliterated at build time
    hit = fuzzy_resolve("телеграм", choices)
    assert hit is not None and hit[1] == "tg.lnk"
