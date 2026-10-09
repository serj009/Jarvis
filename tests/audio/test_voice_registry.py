"""Tests for jarvis.audio.voice_registry and tts_manager."""

from __future__ import annotations

from pathlib import Path

from jarvis.audio.voice_registry import (
    BUILTIN_VOICES,
    VoiceBackend,
    VoiceProfile,
    VoiceRegistry,
)


class TestVoiceRegistry:
    def test_defaults(self) -> None:
        reg = VoiceRegistry()
        assert reg.get_voice_name("en") == "en_GB-alan-medium"
        assert reg.get_voice_name("ru") == "ru_RU-dmitri-medium"
        assert reg.get_voice_name("uk") == "uk_UA-mykyta-medium"

    def test_set_voice(self) -> None:
        reg = VoiceRegistry()
        reg.set_voice("ru", "ru_RU-irina-medium")
        assert reg.get_voice_name("ru") == "ru_RU-irina-medium"

    def test_unknown_language_falls_back_to_en(self) -> None:
        reg = VoiceRegistry()
        voice = reg.get_voice("fr")
        assert voice is not None
        assert voice.language == "en"

    def test_register_custom_voice(self) -> None:
        reg = VoiceRegistry()
        custom = VoiceProfile(
            name="ru_RU-baranov-custom",
            language="ru",
            display_name="Baranov",
            is_custom=True,
        )
        reg.register(custom)
        reg.set_voice("ru", "ru_RU-baranov-custom")
        assert reg.get_voice_name("ru") == "ru_RU-baranov-custom"
        voice = reg.get_voice("ru")
        assert voice is not None
        assert voice.is_custom is True

    def test_list_voices(self) -> None:
        reg = VoiceRegistry()
        all_voices = reg.list_voices()
        assert len(all_voices) == len(BUILTIN_VOICES)

    def test_list_by_language(self) -> None:
        reg = VoiceRegistry()
        ru_voices = reg.list_voices("ru")
        assert all(v.language == "ru" for v in ru_voices)
        assert len(ru_voices) >= 1

    def test_is_downloaded(self, tmp_path) -> None:
        reg = VoiceRegistry(voices_dir=tmp_path)
        assert not reg.is_downloaded("en_GB-alan-medium")

        # Create fake voice files
        (tmp_path / "en_GB-alan-medium.onnx").touch()
        (tmp_path / "en_GB-alan-medium.onnx.json").touch()
        assert reg.is_downloaded("en_GB-alan-medium")

    def test_list_available(self, tmp_path) -> None:
        reg = VoiceRegistry(voices_dir=tmp_path)
        (tmp_path / "en_GB-alan-medium.onnx").touch()
        (tmp_path / "en_GB-alan-medium.onnx.json").touch()
        available = reg.list_available("en")
        assert len(available) == 1
        assert available[0].name == "en_GB-alan-medium"

    def test_assignments(self) -> None:
        reg = VoiceRegistry()
        a = reg.assignments
        assert "en" in a
        assert "ru" in a
        assert "uk" in a

    def test_unregister(self) -> None:
        reg = VoiceRegistry()
        reg.unregister("en_GB-alan-medium")
        voice = reg.get_voice("en")
        # Should still have a name in assignments, but profile is None
        assert voice is None


class TestVoiceProfile:
    def test_piper_backend(self) -> None:
        v = VoiceProfile(name="test", language="en")
        assert v.backend == VoiceBackend.PIPER

    def test_frozen(self) -> None:
        v = VoiceProfile(name="test", language="en")
        try:
            v.name = "changed"  # type: ignore
            assert False, "should be frozen"
        except AttributeError:
            pass
