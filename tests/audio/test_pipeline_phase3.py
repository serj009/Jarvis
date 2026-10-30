"""Tests for Phase 3 pipeline enhancements: thinking phrases, timeouts, half-duplex.

T3.3: Half-duplex mode
T3.4: Pipeline timeouts + thinking phrases
"""

from __future__ import annotations

from jarvis.core.phrases import PHRASES

# ---------------------------------------------------------------------------
# Thinking phrases exist in PHRASES (T3.4)
# ---------------------------------------------------------------------------


class TestThinkingPhrases:
    _THINKING_KEYS = [
        "thinking_general_1", "thinking_general_2", "thinking_general_3",
        "thinking_check_1", "thinking_check_2", "thinking_long_1",
    ]

    def test_all_thinking_keys_exist(self) -> None:
        for key in self._THINKING_KEYS:
            assert key in PHRASES, f"Missing phrase key: {key}"

    def test_all_thinking_phrases_have_3_languages(self) -> None:
        for key in self._THINKING_KEYS:
            for lang in ("en", "ru", "uk"):
                assert lang in PHRASES[key], f"PHRASES[{key!r}] missing {lang}"

    def test_thinking_phrases_are_short(self) -> None:
        """Thinking phrases should be 1-2 sentences max for fast TTS."""
        for key in self._THINKING_KEYS:
            for lang in ("en", "ru", "uk"):
                text = PHRASES[key][lang]
                assert len(text) < 80, f"Phrase too long for TTS: {key}/{lang} = {text!r}"

    def test_thinking_phrases_not_empty(self) -> None:
        for key in self._THINKING_KEYS:
            for lang in ("en", "ru", "uk"):
                assert PHRASES[key][lang].strip(), f"Empty phrase: {key}/{lang}"


# ---------------------------------------------------------------------------
# Timeout error phrases (T3.4)
# ---------------------------------------------------------------------------


class TestTimeoutPhrases:
    _TIMEOUT_KEYS = ["error_stt_timeout", "error_llm_timeout"]

    def test_timeout_phrases_exist(self) -> None:
        for key in self._TIMEOUT_KEYS:
            assert key in PHRASES, f"Missing phrase key: {key}"
            for lang in ("en", "ru", "uk"):
                assert lang in PHRASES[key], f"PHRASES[{key!r}] missing {lang}"


# ---------------------------------------------------------------------------
# PipelineConfig defaults (T3.3 / T3.4)
# ---------------------------------------------------------------------------


class TestPipelineConfig:
    def test_pipeline_config_defaults(self) -> None:
        from jarvis.core.config import PipelineConfig

        cfg = PipelineConfig()
        assert cfg.stt_timeout_s == 10.0
        assert cfg.llm_timeout_s == 20.0
        assert cfg.tts_timeout_s == 15.0
        assert cfg.thinking_phrases_enabled is True
        assert cfg.half_duplex is True
        assert cfg.max_recovery_attempts == 3

    def test_pipeline_config_custom(self) -> None:
        from jarvis.core.config import PipelineConfig

        cfg = PipelineConfig(
            stt_timeout_s=5.0,
            llm_timeout_s=30.0,
            tts_timeout_s=20.0,
            thinking_phrases_enabled=False,
            half_duplex=False,
            max_recovery_attempts=5,
        )
        assert cfg.stt_timeout_s == 5.0
        assert cfg.thinking_phrases_enabled is False
        assert cfg.half_duplex is False

    def test_pipeline_config_in_jarvis_config(self) -> None:
        from jarvis.core.config import JarvisConfig

        cfg = JarvisConfig()
        assert hasattr(cfg, "pipeline")
        assert cfg.pipeline.thinking_phrases_enabled is True
        assert cfg.pipeline.half_duplex is True


# ---------------------------------------------------------------------------
# AudioPipeline constructor accepts Phase 3 params
# ---------------------------------------------------------------------------


class TestPipelineConstructorParams:
    def test_pipeline_accepts_phase3_params(self) -> None:
        """Verify AudioPipeline.__init__ accepts the new keyword args
        without TypeError. Actual behavior tested in integration."""
        import inspect

        from jarvis.audio.pipeline import AudioPipeline

        sig = inspect.signature(AudioPipeline.__init__)
        params = sig.parameters

        assert "thinking_phrases_enabled" in params
        assert "half_duplex" in params
        assert "stt_timeout_s" in params
        assert "llm_timeout_s" in params
        assert "tts_timeout_s" in params


class TestLanguageSwitchFloor:
    """Task 7.1: _language_switch_reason gates spurious voice switches."""

    def test_low_probability_blocks_switch(self) -> None:
        from jarvis.audio.pipeline import _language_switch_reason
        # 4-char utterance at p=0.34 — the real log case that flipped voice.
        assert _language_switch_reason(
            detected_language="ru", language_probability=0.34,
            text_len=4, min_probability=0.70, min_chars=8,
        ) == "low-probability"

    def test_short_text_blocks_switch(self) -> None:
        from jarvis.audio.pipeline import _language_switch_reason
        assert _language_switch_reason(
            detected_language="uk", language_probability=0.95,
            text_len=4, min_probability=0.70, min_chars=8,
        ) == "short-text"

    def test_no_language_blocks_switch(self) -> None:
        from jarvis.audio.pipeline import _language_switch_reason
        assert _language_switch_reason(
            detected_language=None, language_probability=1.0,
            text_len=50, min_probability=0.70, min_chars=8,
        ) == "no-language"

    def test_confident_long_text_allows_switch(self) -> None:
        from jarvis.audio.pipeline import _language_switch_reason
        assert _language_switch_reason(
            detected_language="en", language_probability=0.95,
            text_len=20, min_probability=0.70, min_chars=8,
        ) is None

    def test_thresholds_are_inclusive_floors(self) -> None:
        """Exactly at the floor (p==min, chars==min) is allowed; the gate
        only trips strictly below."""
        from jarvis.audio.pipeline import _language_switch_reason
        assert _language_switch_reason(
            detected_language="ru", language_probability=0.70,
            text_len=8, min_probability=0.70, min_chars=8,
        ) is None


class TestLanguageSwitchConfig:
    def test_stt_config_has_switch_floors_with_defaults(self) -> None:
        from jarvis.core.config import STTConfig

        cfg = STTConfig()
        assert cfg.language_switch_min_probability == 0.70
        assert cfg.language_switch_min_chars == 8

    def test_switch_floors_are_configurable(self) -> None:
        from jarvis.core.config import STTConfig

        cfg = STTConfig(
            language_switch_min_probability=0.85,
            language_switch_min_chars=12,
        )
        assert cfg.language_switch_min_probability == 0.85
        assert cfg.language_switch_min_chars == 12

    def test_pipeline_accepts_switch_floor_params(self) -> None:
        import inspect

        from jarvis.audio.pipeline import AudioPipeline

        params = inspect.signature(AudioPipeline.__init__).parameters
        assert "language_switch_min_probability" in params
        assert "language_switch_min_chars" in params
