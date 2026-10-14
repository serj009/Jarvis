"""Voice tab: input/output devices, wake-word sensitivity, TTS knobs,
test-voice button. Heavy IO (sd.query_devices, voice-file enumeration,
TTS test) is gated to lazy calls so opening other tabs stays snappy."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig, save_config
from jarvis.ui.settings._widgets import SectionCard, slider_with_label

log = logging.getLogger(__name__)


def _enumerate_devices(kind: str) -> list[tuple[str, str]]:
    """Return [(label, name)] for input or output devices. label includes
    the host API so duplicates (WASAPI vs MME variants of the same device)
    are distinguishable. Returns [] on any sounddevice error so the
    settings UI degrades gracefully when no audio backend is present."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as e:
        log.warning("could not enumerate %s devices: %s", kind, e)
        return []
    field = "max_input_channels" if kind == "input" else "max_output_channels"
    out: list[tuple[str, str]] = []
    for dev in devices:
        if int(dev.get(field, 0)) <= 0:
            continue
        name = str(dev.get("name", ""))
        ha_index = dev.get("hostapi")
        ha_name = ""
        if isinstance(ha_index, int) and 0 <= ha_index < len(hostapis):
            ha_name = str(hostapis[ha_index].get("name", ""))
        label = f"{name} [{ha_name}]" if ha_name else name
        out.append((label, name))
    return out


def _enumerate_voices(voices_dir: Path) -> list[str]:
    """Return sorted voice names (without .onnx extension) found in
    voices_dir. Empty on missing dir or any os error."""
    try:
        return sorted(p.stem for p in voices_dir.glob("*.onnx"))
    except Exception as e:
        log.warning("could not enumerate voices in %s: %s", voices_dir, e)
        return []


def _default_voices_dir() -> Path:
    env = os.environ.get("JARVIS_PIPER_VOICES_DIR")
    if env:
        return Path(env)
    return Path.home() / ".jarvis" / "voices"


class VoiceTab(QWidget):
    def __init__(
        self,
        *,
        config: JarvisConfig,
        on_change: Callable[[], None],
        voices_dir: Path | None = None,
        on_test_voice: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._on_change = on_change
        self._voices_dir = voices_dir or _default_voices_dir()
        self._on_test_voice = on_test_voice

        # ── Audio Devices section ──────────────────────────────────────
        devices_card = SectionCard()
        devices_card.add_header(
            "\U0001f3a7", "Audio Devices",
            "Select your microphone and speaker for voice interaction",
        )

        self.input_device = QComboBox()
        devices_card.add_setting(
            "\U0001f3a4 Microphone",
            "Select your microphone for voice commands",
            self.input_device,
        )

        self.output_device = QComboBox()
        devices_card.add_setting(
            "\U0001f50a Speaker",
            "Where Jarvis speaks",
            self.output_device,
        )

        refresh_devices = QPushButton("Refresh Devices")
        refresh_devices.clicked.connect(self._refresh_devices)
        refresh_row = QWidget()
        rfl = QHBoxLayout(refresh_row)
        rfl.setContentsMargins(0, 0, 0, 0)
        rfl.addWidget(refresh_devices)
        rfl.addStretch(1)
        devices_card.add_widget(refresh_row)

        self._populate_devices()

        # ── Wake Word section ──────────────────────────────────────────
        wake_card = SectionCard()
        wake_card.add_header(
            "\U0001f442", "Wake Word",
            "Control how easily Jarvis responds to \"Hey Jarvis\"",
        )

        self.sensitivity = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity.setRange(0, 100)
        self.sensitivity.setValue(
            int(round(config.wake_word.sensitivity * 100))
        )
        self.sensitivity_label = QLabel(
            f"{config.wake_word.sensitivity:.2f}"
        )
        sens_row = slider_with_label(self.sensitivity, self.sensitivity_label)

        wake_card.add_setting(
            "\U0001f442 Wake Word Sensitivity",
            "How easily \"Hey Jarvis\" is detected. "
            "Higher = more sensitive, may trigger on noise",
            sens_row,
        )

        # ── Text-to-Speech section ─────────────────────────────────────
        tts_card = SectionCard()
        tts_card.add_header(
            "\U0001f5e3\ufe0f", "Text-to-Speech",
            "How Jarvis speaks back to you",
        )

        self.voice = QComboBox()
        self._populate_voices()
        tts_card.add_setting(
            "\U0001f5e3\ufe0f Voice",
            "Choose which voice Jarvis speaks with",
            self.voice,
        )

        # TTS speed (0.5–2.0 in 0.05 steps -> int slider 50..200 / 100).
        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(50, 200)
        self.speed.setSingleStep(5)
        self.speed.setValue(int(round(config.tts.speed * 100)))
        self.speed_label = QLabel(f"{config.tts.speed:.2f}\u00d7")
        speed_row = slider_with_label(self.speed, self.speed_label)
        tts_card.add_setting(
            "\u26a1 Speech Speed",
            "How fast Jarvis speaks",
            speed_row,
        )

        # TTS volume (0.0–1.0 in 0.01 steps -> int slider 0..100 / 100).
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(round(config.tts.volume * 100)))
        self.volume_label = QLabel(f"{config.tts.volume:.2f}")
        vol_row = slider_with_label(self.volume, self.volume_label)
        tts_card.add_setting(
            "\U0001f509 Speech Volume",
            "How loud Jarvis speaks",
            vol_row,
        )

        # Test voice button.
        self.test_voice_button = QPushButton("Test Voice")
        self.test_voice_button.clicked.connect(self._on_test_voice_clicked)
        test_row = QWidget()
        trl = QHBoxLayout(test_row)
        trl.setContentsMargins(0, 0, 0, 0)
        trl.addWidget(self.test_voice_button)
        trl.addStretch(1)
        tts_card.add_widget(test_row)

        # ── Assemble scrollable layout ─────────────────────────────────
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(20)
        vbox.addWidget(devices_card)
        vbox.addWidget(wake_card)
        vbox.addWidget(tts_card)
        vbox.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        # ── Signal connections ─────────────────────────────────────────
        self.input_device.currentIndexChanged.connect(self._on_input_changed)
        self.output_device.currentIndexChanged.connect(self._on_output_changed)
        self.sensitivity.valueChanged.connect(self._on_sensitivity_changed)
        self.voice.currentTextChanged.connect(self._on_voice_changed)
        self.speed.valueChanged.connect(self._on_speed_changed)
        self.volume.valueChanged.connect(self._on_volume_changed)

    # -- population ------------------------------------------------------

    def _populate_devices(self) -> None:
        """Wipe + repopulate both device combos. Slot connections are
        defensive about empty values during the wipe — currentText goes
        through "" briefly, which our handlers ignore."""
        for combo, kind, current in (
            (self.input_device, "input", self._config.audio.input_device),
            (self.output_device, "output", self._config.audio.output_device),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(system default)", None)
            for label, name in _enumerate_devices(kind):
                combo.addItem(label, name)
            # Select the currently-configured device using the same
            # case-insensitive substring logic the audio layer uses at
            # runtime (audio/devices.py, audio/tts.py). The config stores a
            # short substring like "TONOR" while sounddevice may return
            # "Microphone (TONOR TM20 Audio Device)". An exact findData()
            # would miss and show "(not connected)" even when the device is
            # fully operational.
            if current is not None:
                needle = current.lower()
                idx = -1
                for i in range(combo.count()):
                    data = combo.itemData(i)
                    if isinstance(data, str) and needle in data.lower():
                        idx = i
                        break
                if idx < 0:
                    # Configured name genuinely absent (device unplugged
                    # or renamed); add a placeholder so the user can see
                    # the stale value.
                    combo.addItem(f"{current} (not connected)", current)
                    idx = combo.count() - 1
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _populate_voices(self) -> None:
        names = _enumerate_voices(self._voices_dir)
        self.voice.blockSignals(True)
        self.voice.clear()
        if not names:
            self.voice.addItem("(none found)", "")
        else:
            for n in names:
                self.voice.addItem(n, n)
        current = self._config.tts.voice
        idx = self.voice.findData(current)
        if idx >= 0:
            self.voice.setCurrentIndex(idx)
        elif current and self.voice.count() and self.voice.itemData(0) != "":
            # Configured voice not on disk; surface it as a stale entry.
            self.voice.addItem(f"{current} (missing)", current)
            self.voice.setCurrentIndex(self.voice.count() - 1)
        self.voice.blockSignals(False)

    def _refresh_devices(self) -> None:
        self._populate_devices()
        self._populate_voices()

    # -- slots -----------------------------------------------------------

    def _on_input_changed(self, _idx: int) -> None:
        value = self.input_device.currentData()
        self._config.audio.input_device = value
        self._persist()

    def _on_output_changed(self, _idx: int) -> None:
        value = self.output_device.currentData()
        self._config.audio.output_device = value
        self._persist()

    def _on_sensitivity_changed(self, value: int) -> None:
        f = value / 100.0
        self._config.wake_word.sensitivity = f
        self.sensitivity_label.setText(f"{f:.2f}")
        self._persist()

    def _on_voice_changed(self, _text: str) -> None:
        value = self.voice.currentData()
        if not value:
            return
        self._config.tts.voice = value
        self._persist()

    def _on_speed_changed(self, value: int) -> None:
        f = value / 100.0
        self._config.tts.speed = f
        self.speed_label.setText(f"{f:.2f}\u00d7")
        self._persist()

    def _on_volume_changed(self, value: int) -> None:
        f = value / 100.0
        self._config.tts.volume = f
        self.volume_label.setText(f"{f:.2f}")
        self._persist()

    def _on_test_voice_clicked(self) -> None:
        cb = self._on_test_voice
        if cb is None:
            log.info("test-voice clicked but no callback wired")
            return
        try:
            cb("hello, this is jarvis")
        except Exception:
            log.exception("test voice callback raised")

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("save_config failed from VoiceTab")
            return
        try:
            self._on_change()
        except Exception:
            log.exception("on_change callback raised from VoiceTab")
