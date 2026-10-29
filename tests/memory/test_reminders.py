"""Tests for jarvis.memory.reminders (event-triggered reminders)."""

from __future__ import annotations

import pytest

from jarvis.memory.reminders import (
    check_reminders,
    clear_reminder,
    format_reminders_for_speech,
    parse_reminder,
    save_reminder,
)
from jarvis.memory.store import MemoryStore

# ---------------------------------------------------------------------------
# parse_reminder (synchronous, no store needed)
# ---------------------------------------------------------------------------

class TestParseReminder:
    """Pattern matching for reminder requests in EN/RU/UK."""

    # -- Russian patterns --

    def test_ru_play_game(self) -> None:
        result = parse_reminder(
            "напомни когда буду играть в Elden Ring что нужно найти Flame of the Fell God"
        )
        assert result is not None
        target, text = result
        assert "elden ring" in target.lower()
        assert "flame" in text.lower()

    def test_ru_play_game_with_dash(self) -> None:
        result = parse_reminder(
            "напомни когда буду играть в Stalker 2 — найти артефакт"
        )
        assert result is not None
        target, text = result
        assert "stalker" in target.lower()
        assert "артефакт" in text.lower()

    def test_ru_open_app(self) -> None:
        result = parse_reminder(
            "напомни когда открою Chrome что проверить почту"
        )
        assert result is not None
        target, text = result
        assert "chrome" in target.lower()
        assert "почту" in text.lower()

    def test_ru_launch_app(self) -> None:
        result = parse_reminder(
            "напомни если запущу Steam — скачать обновление"
        )
        assert result is not None
        target, text = result
        assert "steam" in target.lower()
        assert "обновление" in text.lower()

    def test_ru_next_launch(self) -> None:
        result = parse_reminder(
            "напомни при следующем запуске Discord что написать Максу"
        )
        assert result is not None
        target, text = result
        assert "discord" in target.lower()
        assert "максу" in text.lower()

    def test_ru_mne_variant(self) -> None:
        result = parse_reminder(
            "напомни мне когда буду играть в Skyrim что найти Даэдрический меч"
        )
        assert result is not None
        target, text = result
        assert "skyrim" in target.lower()

    # -- English patterns --

    def test_en_play_game(self) -> None:
        result = parse_reminder(
            "remind me when I play Elden Ring to find Flame of the Fell God"
        )
        assert result is not None
        target, text = result
        assert "elden ring" in target.lower()
        assert "flame" in text.lower()

    def test_en_launch_app(self) -> None:
        result = parse_reminder(
            "remind me when I launch Chrome to check my email"
        )
        assert result is not None
        target, text = result
        assert "chrome" in target.lower()
        assert "email" in text.lower()

    def test_en_next_time(self) -> None:
        result = parse_reminder(
            "remind me next time I open Steam that I need to download the update"
        )
        assert result is not None
        target, text = result
        assert "steam" in target.lower()
        assert "update" in text.lower()

    # -- Ukrainian patterns --

    def test_uk_play_game(self) -> None:
        result = parse_reminder(
            "нагадай коли буду грати в Elden Ring що знайти Flame of the Fell God"
        )
        assert result is not None
        target, text = result
        assert "elden ring" in target.lower()
        assert "flame" in text.lower()

    def test_uk_open_app(self) -> None:
        result = parse_reminder(
            "нагадай коли відкрию Chrome що перевірити пошту"
        )
        assert result is not None
        target, text = result
        assert "chrome" in target.lower()
        assert "пошту" in text.lower()

    # -- Non-matching utterances --

    def test_not_a_reminder_command(self) -> None:
        assert parse_reminder("open chrome") is None

    def test_not_a_reminder_question(self) -> None:
        assert parse_reminder("what is the weather?") is None

    def test_not_a_reminder_simple_remember(self) -> None:
        # "запомни что мой пароль 123" is an explicit-memory command, not a trigger reminder
        assert parse_reminder("запомни что мой пароль 123") is None

    def test_strips_trailing_punctuation(self) -> None:
        result = parse_reminder(
            "remind me when I play Skyrim to find the Daedric sword!"
        )
        assert result is not None
        _, text = result
        assert not text.endswith("!")


# ---------------------------------------------------------------------------
# save_reminder + check_reminders + clear_reminder (async, needs store)
# ---------------------------------------------------------------------------

@pytest.fixture()
async def store(tmp_path) -> MemoryStore:
    s = MemoryStore(db_path=tmp_path / "test_reminders.db")
    await s.open()
    yield s
    await s.close()


class TestSaveAndCheckReminders:
    @pytest.mark.asyncio()
    async def test_save_and_check(self, store) -> None:
        """Save a reminder, then check it fires for the right target."""
        saved = await save_reminder(store, "Elden Ring", "найти Flame of the Fell God")
        assert saved is True

        reminders = await check_reminders(store, "Elden Ring")
        assert len(reminders) >= 1
        assert any("flame" in r.lower() for r in reminders)

    @pytest.mark.asyncio()
    async def test_no_reminders_for_different_target(self, store) -> None:
        """Reminders for one target don't fire for another."""
        await save_reminder(store, "Elden Ring", "найти Flame")

        reminders = await check_reminders(store, "Skyrim")
        assert len(reminders) == 0

    @pytest.mark.asyncio()
    async def test_multiple_reminders_same_target(self, store) -> None:
        """Multiple reminders for the same game all fire."""
        await save_reminder(store, "Elden Ring", "найти Flame")
        await save_reminder(store, "Elden Ring", "прокачать силу до 40")

        reminders = await check_reminders(store, "Elden Ring")
        assert len(reminders) >= 2

    @pytest.mark.asyncio()
    async def test_case_insensitive_target(self, store) -> None:
        """Trigger matching is case-insensitive."""
        await save_reminder(store, "Chrome", "проверить почту")

        reminders = await check_reminders(store, "chrome")
        assert len(reminders) >= 1
        assert any("почту" in r for r in reminders)

    @pytest.mark.asyncio()
    async def test_clear_fired_reminder(self, store) -> None:
        """After clearing a fired reminder, it no longer appears."""
        await save_reminder(store, "Steam", "скачать обновление")

        # Check it exists
        reminders = await check_reminders(store, "Steam")
        assert len(reminders) >= 1

        # Clear it
        cleared = await clear_reminder(store, "Steam", "скачать обновление")
        assert cleared is True

        # Should be gone now
        reminders = await check_reminders(store, "Steam")
        assert len(reminders) == 0

    @pytest.mark.asyncio()
    async def test_no_reminders_empty_store(self, store) -> None:
        """Empty store returns no reminders."""
        reminders = await check_reminders(store, "Anything")
        assert reminders == []


# ---------------------------------------------------------------------------
# format_reminders_for_speech
# ---------------------------------------------------------------------------

class TestFormatRemindersForSpeech:
    def test_empty_list(self) -> None:
        assert format_reminders_for_speech([]) == ""

    def test_single_reminder_en(self) -> None:
        result = format_reminders_for_speech(["find Flame of the Fell God"], lang="en")
        assert "reminder" in result.lower()
        assert "Flame" in result

    def test_single_reminder_ru(self) -> None:
        result = format_reminders_for_speech(["найти Flame"], lang="ru")
        assert "напоминание" in result.lower()

    def test_multiple_reminders_en(self) -> None:
        result = format_reminders_for_speech(
            ["find Flame", "level up strength"],
            lang="en",
        )
        assert "2 reminders" in result
        assert "1." in result
        assert "2." in result

    def test_single_reminder_uk(self) -> None:
        result = format_reminders_for_speech(["перевірити пошту"], lang="uk")
        assert "нагадування" in result.lower()
