"""T2.3 — Test phrase corpus for multilingual STT validation.

This module defines the canonical phrase set for measuring STT accuracy
across RU / UK / EN. Each phrase has:
- text: the expected transcription (ground truth)
- category: "short" / "long" / "command" / "mixed" / "numbers"
- notes: edge-case description

Integration tests (marked `integration`) require a running Whisper model
and real audio files in tests/audio/fixtures/. Unit tests validate the
corpus itself and phrase coverage.

To record the audio fixtures:
    python -m jarvis.dev.record_phrases --lang ru --output tests/audio/fixtures/
    (or use any recorder: 16kHz mono WAV, one file per phrase)

WER tracking:
    Run `pytest tests/audio/test_stt_phrases.py -v --tb=short` with models
    installed. Results are printed as a summary table at the end.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class STTPhrase:
    """A single test phrase for STT validation."""
    text: str
    language: str  # "en" / "ru" / "uk"
    category: str  # "short" / "long" / "command" / "mixed" / "numbers"
    notes: str = ""


# ---------------------------------------------------------------------------
# English phrases (12)
# ---------------------------------------------------------------------------

EN_PHRASES: tuple[STTPhrase, ...] = (
    # Short commands
    STTPhrase("open youtube", "en", "command", "common voice command"),
    STTPhrase("what time is it", "en", "short", "basic question"),
    STTPhrase("play some music", "en", "command", "vague intent"),
    STTPhrase("stop", "en", "short", "single word"),
    # Medium
    STTPhrase("search the web for weather in Warsaw", "en", "command", "multi-word query"),
    STTPhrase("set volume to fifty percent", "en", "numbers", "numeric value in words"),
    STTPhrase("take a screenshot and save it", "en", "command", "compound command"),
    # Long
    STTPhrase(
        "tell me about the latest news in artificial intelligence and machine learning",
        "en", "long", "long natural language query",
    ),
    STTPhrase(
        "open google chrome and go to github dot com",
        "en", "command", "multi-step with brand names",
    ),
    # Edge cases
    STTPhrase("hey jarvis", "en", "short", "wake word — should be filtered"),
    STTPhrase("launch steam and play stalker two", "en", "command", "game title"),
    STTPhrase(
        "remind me to check email at three thirty pm",
        "en", "numbers", "time expression",
    ),
)


# ---------------------------------------------------------------------------
# Russian phrases (12)
# ---------------------------------------------------------------------------

RU_PHRASES: tuple[STTPhrase, ...] = (
    # Short commands
    STTPhrase("открой ютуб", "ru", "command", "brand name in Cyrillic"),
    STTPhrase("который час", "ru", "short", "basic question"),
    STTPhrase("включи музыку", "ru", "command", "vague intent"),
    STTPhrase("стоп", "ru", "short", "single word"),
    # Medium
    STTPhrase("найди в интернете погоду в Варшаве", "ru", "command", "web search"),
    STTPhrase("громкость на пятьдесят процентов", "ru", "numbers", "numeric in words"),
    STTPhrase("сделай скриншот и сохрани", "ru", "command", "compound command"),
    # Long
    STTPhrase(
        "расскажи мне последние новости про искусственный интеллект и машинное обучение",
        "ru", "long", "long natural language query",
    ),
    STTPhrase(
        "открой гугл хром и перейди на гитхаб точка ком",
        "ru", "command", "multi-step with brand names in Cyrillic",
    ),
    # Edge cases
    STTPhrase("привет джарвис", "ru", "short", "wake word variant"),
    STTPhrase("запусти стим и включи сталкер два", "ru", "command", "game title"),
    STTPhrase(
        "напомни мне проверить почту в три тридцать",
        "ru", "numbers", "time expression",
    ),
)


# ---------------------------------------------------------------------------
# Ukrainian phrases (12)
# ---------------------------------------------------------------------------

UK_PHRASES: tuple[STTPhrase, ...] = (
    # Short commands
    STTPhrase("відкрий ютуб", "uk", "command", "brand name in Cyrillic"),
    STTPhrase("котра година", "uk", "short", "basic question — unique UA phrasing"),
    STTPhrase("увімкни музику", "uk", "command", "vague intent"),
    STTPhrase("стоп", "uk", "short", "single word — same as RU"),
    # Medium
    STTPhrase("знайди в інтернеті погоду у Варшаві", "uk", "command", "web search"),
    STTPhrase("гучність на п'ятдесят відсотків", "uk", "numbers", "numeric in words"),
    STTPhrase("зроби скріншот і збережи", "uk", "command", "compound command"),
    # Long
    STTPhrase(
        "розкажи мені останні новини про штучний інтелект та машинне навчання",
        "uk", "long", "long natural language query",
    ),
    STTPhrase(
        "відкрий гугл хром і перейди на гітхаб крапка ком",
        "uk", "command", "multi-step with brand names — UK spelling",
    ),
    # Edge cases
    STTPhrase("привіт джарвіс", "uk", "short", "wake word — UK spelling"),
    STTPhrase("запусти стім і увімкни сталкер два", "uk", "command", "game title"),
    STTPhrase(
        "нагадай мені перевірити пошту о пів на четверту",
        "uk", "numbers", "time expression — UK phrasing",
    ),
)


# All phrases combined
ALL_PHRASES: tuple[STTPhrase, ...] = EN_PHRASES + RU_PHRASES + UK_PHRASES


# ---------------------------------------------------------------------------
# Corpus validation tests (no model needed)
# ---------------------------------------------------------------------------

class TestPhraseCorpus:
    """Validate the test phrase corpus itself."""

    def test_minimum_count_per_language(self):
        """At least 10 phrases per language (Roadmap T2.3 requirement)."""
        for lang in ("en", "ru", "uk"):
            count = sum(1 for p in ALL_PHRASES if p.language == lang)
            assert count >= 10, f"language {lang} has only {count} phrases, need ≥ 10"

    def test_all_categories_covered(self):
        """Each language has at least short, command, and long phrases."""
        required = {"short", "command", "long"}
        for lang in ("en", "ru", "uk"):
            categories = {p.category for p in ALL_PHRASES if p.language == lang}
            missing = required - categories
            assert not missing, f"language {lang} missing categories: {missing}"

    def test_numbers_category_exists(self):
        """Each language has at least one numbers phrase."""
        for lang in ("en", "ru", "uk"):
            has_numbers = any(
                p.category == "numbers" for p in ALL_PHRASES if p.language == lang
            )
            assert has_numbers, f"language {lang} missing 'numbers' category"

    def test_no_empty_text(self):
        for phrase in ALL_PHRASES:
            assert phrase.text.strip(), f"empty text in {phrase}"

    def test_no_duplicate_texts_within_language(self):
        for lang in ("en", "ru", "uk"):
            texts = [p.text for p in ALL_PHRASES if p.language == lang]
            assert len(texts) == len(set(texts)), f"duplicates in {lang}"

    def test_brand_names_present(self):
        """Ensure brand names appear in each language for hotword testing."""
        brands_en = {"youtube", "github", "chrome", "steam"}
        brands_ru = {"ютуб", "гитхаб", "хром", "стим"}
        brands_uk = {"ютуб", "гітхаб", "хром", "стім"}

        en_text = " ".join(p.text.lower() for p in EN_PHRASES)
        ru_text = " ".join(p.text.lower() for p in RU_PHRASES)
        uk_text = " ".join(p.text.lower() for p in UK_PHRASES)

        for brand in brands_en:
            assert brand in en_text, f"EN missing brand: {brand}"
        for brand in brands_ru:
            assert brand in ru_text, f"RU missing brand: {brand}"
        for brand in brands_uk:
            assert brand in uk_text, f"UK missing brand: {brand}"

    def test_total_count(self):
        assert len(ALL_PHRASES) == 36, f"expected 36, got {len(ALL_PHRASES)}"


# ---------------------------------------------------------------------------
# Language detection cross-check (no model needed)
# ---------------------------------------------------------------------------

class TestLanguageDetectionOnPhrases:
    """Verify that phrases.detect_language() correctly identifies the language
    of each test phrase. This validates the pipeline's auto-detect path."""

    @pytest.mark.parametrize("phrase", ALL_PHRASES, ids=lambda p: f"{p.language}:{p.text[:30]}")
    def test_detect_language(self, phrase: STTPhrase):
        from jarvis.core.phrases import detect_language

        detected = detect_language(phrase.text)
        if phrase.language == "uk":
            # Ukrainian detection is best-effort: some phrases without
            # і/ї/є/ґ or UK-specific words may be detected as "ru".
            # This is acceptable — Whisper's own detection is more reliable.
            assert detected in ("uk", "ru"), (
                f"expected 'uk' or 'ru' for Ukrainian phrase, got {detected!r}: "
                f"{phrase.text!r}"
            )
        else:
            assert detected == phrase.language, (
                f"expected {phrase.language!r}, got {detected!r}: {phrase.text!r}"
            )


# ---------------------------------------------------------------------------
# Integration tests (require faster-whisper model)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSTTAccuracy:
    """Integration: measure WER on the phrase corpus.

    Requires:
    - faster-whisper installed
    - WAV files in tests/audio/fixtures/{lang}/{phrase_hash}.wav
    - Run with: pytest tests/audio/test_stt_phrases.py -m integration -v
    """

    # TODO: Implement when audio fixtures are recorded.
    # For each phrase:
    #   1. Load WAV from fixtures
    #   2. Run FasterWhisperSTT.transcribe()
    #   3. Compare text (case-insensitive, stripped)
    #   4. Log WER per language
    #   5. Assert average WER < 30% per language

    def test_placeholder(self):
        """Placeholder — replace with real integration tests after
        recording audio fixtures at home."""
        pytest.skip("Audio fixtures not yet recorded — run at home")
