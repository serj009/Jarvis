"""Tests for jarvis.memory.auto_tagger."""

from __future__ import annotations

from jarvis.memory.auto_tagger import AutoTagger, detect_language


class TestDetectLanguage:
    def test_english(self) -> None:
        assert detect_language("Hello world") == "en"

    def test_russian(self) -> None:
        assert detect_language("Привет мир") == "ru"

    def test_ukrainian(self) -> None:
        assert detect_language("Привіт світ") == "uk"
        assert detect_language("Що нового?") == "uk"

    def test_empty(self) -> None:
        assert detect_language("") == "en"

    def test_mixed_cyrillic_latin(self) -> None:
        # Cyrillic present -> detect Cyrillic language
        assert detect_language("Открой YouTube") == "ru"


class TestAutoTagger:
    def test_game_detection_en(self) -> None:
        tagger = AutoTagger()
        result = tagger.analyze("My Stalker 2 save is corrupted")
        assert "stalker_2" in result.tags
        assert result.category == "games"

    def test_game_detection_ru(self) -> None:
        tagger = AutoTagger()
        result = tagger.analyze("Мой сохранение в Скайриме потерялось")
        assert "skyrim" in result.tags
        assert result.language == "ru"

    def test_game_detection_uk(self) -> None:
        tagger = AutoTagger()
        result = tagger.analyze("Досягнення в Відьмаку 3 не працюють")
        assert "witcher_3" in result.tags
        assert result.language == "uk"

    def test_tech_detection(self) -> None:
        tagger = AutoTagger()
        result = tagger.analyze("Python script to parse JSON with docker")
        assert "python" in result.tags
        assert "docker" in result.tags
        assert result.category == "tech"

    def test_personal_detection(self) -> None:
        tagger = AutoTagger()
        result = tagger.analyze("My name is Serhii and my birthday is October 7")
        assert "name" in result.tags
        assert "birthday" in result.tags
        assert result.category == "personal"

    def test_media_detection(self) -> None:
        tagger = AutoTagger()
        result = tagger.analyze("I love watching anime and reading manga")
        assert "anime" in result.tags
        assert "manga" in result.tags
        assert result.category == "media"

    def test_no_match_returns_general(self) -> None:
        tagger = AutoTagger()
        result = tagger.analyze("The weather is nice today")
        assert result.category == "general"

    def test_category_hint_preserved(self) -> None:
        tagger = AutoTagger()
        result = tagger.analyze("Some text", category_hint="custom")
        assert result.category == "custom"

    def test_multiple_tags(self) -> None:
        tagger = AutoTagger()
        result = tagger.analyze("Stalker 2 achievements guide with save locations")
        assert "stalker_2" in result.tags
        assert "achievements" in result.tags
        assert "saves" in result.tags

    def test_hierarchical_category(self) -> None:
        tagger = AutoTagger()
        result = tagger.analyze("Stalker 2 quest list")
        assert "games" in result.full_category
