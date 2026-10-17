"""Models tab: STT model size + device + compute, LLM model + sampling +
keep-alive, Vision model + settings.

System prompt is intentionally hidden from the UI — it is power-user
territory and editing it can break Jarvis's persona/tool-calling
behaviour. Power users can edit cfg.llm.system_prompt directly in the
JSON config file."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig, save_config
from jarvis.ui.settings._widgets import SectionCard, slider_with_label

log = logging.getLogger(__name__)

# ── STT value ↔ display-name mappings ──────────────────────────────────

_STT_SIZE_ITEMS: tuple[tuple[str, str], ...] = (
    ("tiny", "Tiny (fast, low accuracy)"),
    ("base", "Base (balanced)"),
    ("small", "Small (recommended)"),
    ("medium", "Medium (best accuracy, slow)"),
)
_STT_SIZE_VALUES: tuple[str, ...] = tuple(v for v, _ in _STT_SIZE_ITEMS)

_STT_COMPUTE_ITEMS: tuple[tuple[str, str], ...] = (
    ("auto", "Auto (recommended)"),
    ("int8", "INT8 (CPU-friendly)"),
    ("float16", "Float16 (GPU)"),
    ("float32", "Float32 (highest precision)"),
)
_STT_COMPUTE_VALUES: tuple[str, ...] = tuple(v for v, _ in _STT_COMPUTE_ITEMS)

_STT_DEVICE_ITEMS: tuple[tuple[str, str], ...] = (
    ("auto", "Auto (recommended)"),
    ("cuda", "GPU (CUDA)"),
    ("cpu", "CPU"),
)
_STT_DEVICE_VALUES: tuple[str, ...] = tuple(v for v, _ in _STT_DEVICE_ITEMS)

# ── Provider dropdown items ────────────────────────────────────────────

_LLM_PROVIDER_ITEMS: tuple[tuple[str, str], ...] = (
    ("ollama", "Ollama (Local)"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("groq", "Groq"),
)

_VISION_PROVIDER_ITEMS: tuple[tuple[str, str], ...] = (
    ("ollama", "Ollama (Local)"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
)


def _ollama_list_models() -> list[str]:
    """Shell out to `ollama list` and parse the model column. Returns []
    on any failure (daemon down, ollama not installed, weird output)."""
    try:
        proc = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.info("ollama list unavailable: %s", e)
        return []
    if proc.returncode != 0:
        log.info("ollama list returned %d: %s", proc.returncode, proc.stderr.strip())
        return []
    names: list[str] = []
    for line in proc.stdout.splitlines()[1:]:  # skip header row
        token = line.split(None, 1)[0].strip() if line.strip() else ""
        if token:
            names.append(token)
    return names


# Ollama model-name prefixes that indicate a vision (multimodal) model.
_VISION_PREFIXES: tuple[str, ...] = (
    "llava", "moondream", "minicpm-v", "qwen2.5-vl", "qwen2-vl", "bakllava",
)


class ModelsTab(QWidget):
    def __init__(
        self,
        *,
        config: JarvisConfig,
        on_change: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._on_change = on_change

        # ── Speech Recognition section ─────────────────────────────────
        stt_card = SectionCard()
        stt_card.add_header(
            "\U0001f3a4", "Speech Recognition",
            "How Jarvis hears and transcribes your voice",
        )

        # STT model size.
        self.stt_size = QComboBox()
        for value, display in _STT_SIZE_ITEMS:
            self.stt_size.addItem(display, value)
        idx = self.stt_size.findData(config.stt.model_size)
        if idx >= 0:
            self.stt_size.setCurrentIndex(idx)
        stt_card.add_setting(
            "Recognition Model",
            "Larger = more accurate. 'Small' recommended for multilingual",
            self.stt_size,
        )

        # STT language.
        self.stt_language = QLineEdit(config.stt.language)
        self.stt_language.setPlaceholderText("auto = detect automatically")
        stt_card.add_setting(
            "Language",
            "auto = detect automatically (recommended for RU/UK/EN)",
            self.stt_language,
        )

        # STT device (NEW — config.stt.device).
        self.stt_device = QComboBox()
        for value, display in _STT_DEVICE_ITEMS:
            self.stt_device.addItem(display, value)
        idx = self.stt_device.findData(config.stt.device)
        if idx >= 0:
            self.stt_device.setCurrentIndex(idx)
        stt_card.add_setting(
            "Device",
            "Auto = GPU if available, CPU otherwise",
            self.stt_device,
        )

        # STT compute type (added "auto" to choices).
        self.stt_compute = QComboBox()
        for value, display in _STT_COMPUTE_ITEMS:
            self.stt_compute.addItem(display, value)
        idx = self.stt_compute.findData(config.stt.compute_type)
        if idx >= 0:
            self.stt_compute.setCurrentIndex(idx)
        stt_card.add_setting(
            "Processing",
            "auto = GPU-optimized float16 if available, INT8 on CPU",
            self.stt_compute,
        )

        # ── AI Brain section ───────────────────────────────────────────
        llm_card = SectionCard()
        llm_card.add_header(
            "\U0001f9e0", "AI Brain",
            "The language model that thinks and responds",
        )

        # LLM Provider dropdown.
        self.llm_provider = QComboBox()
        for value, display in _LLM_PROVIDER_ITEMS:
            self.llm_provider.addItem(display, value)
        idx = self.llm_provider.findData(config.llm.provider)
        if idx >= 0:
            self.llm_provider.setCurrentIndex(idx)
        llm_card.add_setting(
            "Provider",
            "Ollama = local (free), others = cloud API (requires key)",
            self.llm_provider,
        )

        # LLM Ollama model dropdown + refresh (shown when provider=ollama).
        self.llm_model = QComboBox()
        self.llm_model.setEditable(True)
        refresh_models = QPushButton("\U0001f504 Scan")
        refresh_models.clicked.connect(self._refresh_models)
        self._llm_ollama_row = QWidget()
        mrl = QHBoxLayout(self._llm_ollama_row)
        mrl.setContentsMargins(0, 0, 0, 0)
        mrl.addWidget(self.llm_model, 1)
        mrl.addWidget(refresh_models)
        self._populate_models()
        llm_card.add_setting(
            "AI Model",
            "Local Ollama model (scan to refresh list)",
            self._llm_ollama_row,
        )

        # LLM API model name (shown when provider != ollama).
        self.llm_api_model = QLineEdit(config.llm.api_model)
        self.llm_api_model.setPlaceholderText("e.g. gpt-4o, claude-3.5-sonnet")
        llm_card.add_setting(
            "API Model",
            "Model name for the cloud provider",
            self.llm_api_model,
        )

        # LLM API key (shown when provider != ollama).
        self.llm_api_key = QLineEdit(config.llm.api_key)
        self.llm_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.llm_api_key.setPlaceholderText("sk-... or ant-...")
        llm_card.add_setting(
            "API Key",
            "Encrypted at rest (DPAPI)",
            self.llm_api_key,
        )

        # LLM API base URL (shown when provider != ollama).
        self.llm_api_base_url = QLineEdit(config.llm.api_base_url)
        self.llm_api_base_url.setPlaceholderText("(optional) custom endpoint")
        llm_card.add_setting(
            "API Base URL",
            "Leave blank for the provider's default endpoint",
            self.llm_api_base_url,
        )

        # LLM temperature.
        self.llm_temperature = QSlider(Qt.Orientation.Horizontal)
        self.llm_temperature.setRange(0, 200)  # 0.00..2.00
        self.llm_temperature.setValue(int(round(config.llm.temperature * 100)))
        self.llm_temperature_label = QLabel(f"{config.llm.temperature:.2f}")
        temp_row = slider_with_label(
            self.llm_temperature, self.llm_temperature_label,
        )
        llm_card.add_setting(
            "Creativity",
            "0 = precise and focused, 1+ = creative and varied",
            temp_row,
        )

        # LLM max tokens.
        self.llm_max_tokens = QSpinBox()
        self.llm_max_tokens.setRange(64, 8192)
        self.llm_max_tokens.setSingleStep(64)
        self.llm_max_tokens.setValue(config.llm.max_tokens)
        llm_card.add_setting(
            "Max Response Length",
            "Maximum words in a single response",
            self.llm_max_tokens,
        )

        # LLM keep alive (only relevant for Ollama).
        self.llm_keep_alive = QSpinBox()
        self.llm_keep_alive.setRange(60, 86400)
        self.llm_keep_alive.setSingleStep(60)
        self.llm_keep_alive.setValue(config.llm.keep_alive_seconds)
        self.llm_keep_alive.setSuffix(" s")
        llm_card.add_setting(
            "Keep Model Loaded",
            "How long the model stays in memory after last use (Ollama only)",
            self.llm_keep_alive,
        )

        # Store references to the widgets that need show/hide toggling.
        self._llm_api_widgets = [
            self.llm_api_model, self.llm_api_key, self.llm_api_base_url,
        ]

        # ── Vision section ─────────────────────────────────────────────
        vision_card = SectionCard()
        vision_card.add_header(
            "\U0001f441\ufe0f", "Vision",
            "Used for \"see my screen\". Ollama needs a multimodal model; "
            "cloud APIs (GPT-4o, Claude) have vision built in",
        )

        # Vision Provider dropdown.
        self.vision_provider = QComboBox()
        for value, display in _VISION_PROVIDER_ITEMS:
            self.vision_provider.addItem(display, value)
        idx = self.vision_provider.findData(config.vision.provider)
        if idx >= 0:
            self.vision_provider.setCurrentIndex(idx)
        vision_card.add_setting(
            "Provider",
            "Ollama = local, others = cloud API (requires key)",
            self.vision_provider,
        )

        # Vision Ollama model dropdown + scan (shown when provider=ollama).
        self.vision_model = QComboBox()
        self.vision_model.setEditable(True)
        refresh_vision = QPushButton("\U0001f504 Scan")
        refresh_vision.clicked.connect(self._refresh_vision_models)
        self._vision_ollama_row = QWidget()
        vrl = QHBoxLayout(self._vision_ollama_row)
        vrl.setContentsMargins(0, 0, 0, 0)
        vrl.addWidget(self.vision_model, 1)
        vrl.addWidget(refresh_vision)
        self._populate_vision_models()
        vision_card.add_setting(
            "Vision Model",
            "Local Ollama multimodal model (scan to refresh)",
            self._vision_ollama_row,
        )

        # Vision API model name.
        self.vision_api_model = QLineEdit(config.vision.api_model)
        self.vision_api_model.setPlaceholderText("e.g. gpt-4o, claude-3.5-sonnet")
        vision_card.add_setting(
            "API Model",
            "Cloud model name (must support vision)",
            self.vision_api_model,
        )

        # Vision API key.
        self.vision_api_key = QLineEdit(config.vision.api_key)
        self.vision_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.vision_api_key.setPlaceholderText("sk-... or ant-...")
        vision_card.add_setting(
            "API Key",
            "Encrypted at rest (DPAPI)",
            self.vision_api_key,
        )

        # Vision API base URL.
        self.vision_api_base_url = QLineEdit(config.vision.api_base_url)
        self.vision_api_base_url.setPlaceholderText("(optional) custom endpoint")
        vision_card.add_setting(
            "API Base URL",
            "Leave blank for the provider's default endpoint",
            self.vision_api_base_url,
        )

        self.vision_max_dim = QSpinBox()
        self.vision_max_dim.setRange(320, 4096)
        self.vision_max_dim.setSingleStep(64)
        self.vision_max_dim.setValue(config.vision.max_image_dim)
        self.vision_max_dim.setSuffix(" px")
        self.vision_max_dim.setToolTip(
            "Long-edge pixel cap before the screenshot is sent. "
            "Lower = faster, less detail."
        )
        vision_card.add_setting(
            "Image Quality",
            "Higher = more detail, slower processing",
            self.vision_max_dim,
        )

        self.vision_max_tokens = QSpinBox()
        self.vision_max_tokens.setRange(64, 4096)
        self.vision_max_tokens.setSingleStep(64)
        self.vision_max_tokens.setValue(config.vision.max_tokens)
        vision_card.add_setting(
            "Max Description Length",
            "Maximum tokens for the vision model's response",
            self.vision_max_tokens,
        )

        self.vision_temperature = QSlider(Qt.Orientation.Horizontal)
        self.vision_temperature.setRange(0, 200)
        self.vision_temperature.setValue(
            int(round(config.vision.temperature * 100))
        )
        self.vision_temperature_label = QLabel(
            f"{config.vision.temperature:.2f}"
        )
        v_temp_row = slider_with_label(
            self.vision_temperature, self.vision_temperature_label,
        )
        vision_card.add_setting(
            "Description Creativity",
            "0 = factual, 1+ = more descriptive",
            v_temp_row,
        )

        # Store references for show/hide toggling.
        self._vision_api_widgets = [
            self.vision_api_model, self.vision_api_key,
            self.vision_api_base_url,
        ]

        # ── Assemble scrollable layout ─────────────────────────────────
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(20)
        vbox.addWidget(stt_card)
        vbox.addWidget(llm_card)
        vbox.addWidget(vision_card)
        vbox.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        # ── Signal connections ─────────────────────────────────────────
        self.stt_size.currentIndexChanged.connect(self._on_stt_size)
        self.stt_language.editingFinished.connect(self._on_stt_language)
        self._stt_language_timer = QTimer(self)
        self._stt_language_timer.setSingleShot(True)
        self._stt_language_timer.setInterval(400)
        self._stt_language_timer.timeout.connect(self._on_stt_language)
        self.stt_language.textChanged.connect(self._stt_language_timer.start)
        self.stt_device.currentIndexChanged.connect(self._on_stt_device)
        self.stt_compute.currentIndexChanged.connect(self._on_stt_compute)

        self.llm_provider.currentIndexChanged.connect(self._on_llm_provider)
        self.llm_model.currentTextChanged.connect(self._on_llm_model)
        self.llm_api_model.editingFinished.connect(self._on_llm_api_model)
        self.llm_api_key.editingFinished.connect(self._on_llm_api_key)
        self.llm_api_base_url.editingFinished.connect(self._on_llm_api_base_url)
        self.llm_temperature.valueChanged.connect(self._on_llm_temperature)
        self.llm_max_tokens.valueChanged.connect(self._on_llm_max_tokens)
        self.llm_keep_alive.valueChanged.connect(self._on_llm_keep_alive)

        self.vision_provider.currentIndexChanged.connect(
            self._on_vision_provider,
        )
        self.vision_model.currentTextChanged.connect(self._on_vision_model)
        self.vision_api_model.editingFinished.connect(
            self._on_vision_api_model,
        )
        self.vision_api_key.editingFinished.connect(self._on_vision_api_key)
        self.vision_api_base_url.editingFinished.connect(
            self._on_vision_api_base_url,
        )
        self.vision_max_dim.valueChanged.connect(self._on_vision_max_dim)
        self.vision_max_tokens.valueChanged.connect(self._on_vision_max_tokens)
        self.vision_temperature.valueChanged.connect(
            self._on_vision_temperature
        )

        # Set initial visibility based on current provider values.
        self._sync_llm_visibility()
        self._sync_vision_visibility()

    # -- visibility toggling ---------------------------------------------

    def _sync_llm_visibility(self) -> None:
        """Show/hide LLM widgets based on the selected provider."""
        is_ollama = (self.llm_provider.currentData() == "ollama")
        self._llm_ollama_row.setVisible(is_ollama)
        self.llm_keep_alive.setVisible(is_ollama)
        # Also hide the keep_alive label (its parent row)
        keep_alive_parent = self.llm_keep_alive.parentWidget()
        if keep_alive_parent is not None:
            keep_alive_parent.setVisible(is_ollama)
        for w in self._llm_api_widgets:
            w.setVisible(not is_ollama)
            # Also toggle parent row visibility
            p = w.parentWidget()
            if p is not None and p is not self:
                p.setVisible(not is_ollama)

    def _sync_vision_visibility(self) -> None:
        """Show/hide Vision widgets based on the selected provider."""
        is_ollama = (self.vision_provider.currentData() == "ollama")
        self._vision_ollama_row.setVisible(is_ollama)
        for w in self._vision_api_widgets:
            w.setVisible(not is_ollama)
            p = w.parentWidget()
            if p is not None and p is not self:
                p.setVisible(not is_ollama)

    # -- population ------------------------------------------------------

    def _populate_models(self) -> None:
        self.llm_model.blockSignals(True)
        self.llm_model.clear()
        names = _ollama_list_models()
        if names:
            self.llm_model.addItems(names)
        current = self._config.llm.model
        idx = self.llm_model.findText(current)
        if idx >= 0:
            self.llm_model.setCurrentIndex(idx)
        else:
            # User's configured model isn't in the local pull list — keep
            # the configured value as the editable text so it's visible
            # and editable.
            self.llm_model.setEditText(current)
        self.llm_model.blockSignals(False)

    def _refresh_models(self) -> None:
        self._populate_models()

    def _populate_vision_models(self) -> None:
        self.vision_model.blockSignals(True)
        self.vision_model.clear()
        self.vision_model.addItem("(disabled)")
        names = _ollama_list_models()
        vision_names = [
            n for n in names
            if any(n.startswith(p) for p in _VISION_PREFIXES)
        ]
        if vision_names:
            self.vision_model.addItems(vision_names)
        current = self._config.vision.model
        if current:
            idx = self.vision_model.findText(current)
            if idx >= 0:
                self.vision_model.setCurrentIndex(idx)
            else:
                # Model not in the scanned list — keep it as editable text.
                self.vision_model.setEditText(current)
        else:
            self.vision_model.setCurrentIndex(0)  # (disabled)
        self.vision_model.blockSignals(False)

    def _refresh_vision_models(self) -> None:
        self._populate_vision_models()

    # -- slots -----------------------------------------------------------

    def _on_stt_size(self, _index: int) -> None:
        value = self.stt_size.currentData()
        if value not in _STT_SIZE_VALUES:
            return
        self._config.stt.model_size = value  # type: ignore[assignment]
        self._persist()

    def _on_stt_language(self) -> None:
        value = self.stt_language.text().strip() or "en"
        self._config.stt.language = value
        self._persist()

    def _on_stt_device(self, _index: int) -> None:
        value = self.stt_device.currentData()
        if value not in _STT_DEVICE_VALUES:
            return
        self._config.stt.device = value  # type: ignore[assignment]
        self._persist()

    def _on_stt_compute(self, _index: int) -> None:
        value = self.stt_compute.currentData()
        if value not in _STT_COMPUTE_VALUES:
            return
        self._config.stt.compute_type = value  # type: ignore[assignment]
        self._persist()

    # -- LLM provider slots ----------------------------------------------

    def _on_llm_provider(self, _index: int) -> None:
        value = self.llm_provider.currentData()
        if not value:
            return
        self._config.llm.provider = value
        self._sync_llm_visibility()
        self._persist()

    def _on_llm_model(self, text: str) -> None:
        value = text.strip()
        if not value:
            return
        self._config.llm.model = value
        self._persist()

    def _on_llm_api_model(self) -> None:
        value = self.llm_api_model.text().strip()
        self._config.llm.api_model = value
        self._persist()

    def _on_llm_api_key(self) -> None:
        value = self.llm_api_key.text().strip()
        self._config.llm.api_key = value
        self._persist()

    def _on_llm_api_base_url(self) -> None:
        value = self.llm_api_base_url.text().strip()
        self._config.llm.api_base_url = value
        self._persist()

    def _on_llm_temperature(self, value: int) -> None:
        f = value / 100.0
        self._config.llm.temperature = f
        self.llm_temperature_label.setText(f"{f:.2f}")
        self._persist()

    def _on_llm_max_tokens(self, value: int) -> None:
        self._config.llm.max_tokens = value
        self._persist()

    def _on_llm_keep_alive(self, value: int) -> None:
        self._config.llm.keep_alive_seconds = value
        self._persist()

    # -- Vision provider slots -------------------------------------------

    def _on_vision_provider(self, _index: int) -> None:
        value = self.vision_provider.currentData()
        if not value:
            return
        self._config.vision.provider = value
        self._sync_vision_visibility()
        self._persist()

    def _on_vision_model(self, text: str) -> None:
        value = text.strip()
        if value == "(disabled)":
            value = ""
        self._config.vision.model = value
        self._persist()

    def _on_vision_api_model(self) -> None:
        value = self.vision_api_model.text().strip()
        self._config.vision.api_model = value
        self._persist()

    def _on_vision_api_key(self) -> None:
        value = self.vision_api_key.text().strip()
        self._config.vision.api_key = value
        self._persist()

    def _on_vision_api_base_url(self) -> None:
        value = self.vision_api_base_url.text().strip()
        self._config.vision.api_base_url = value
        self._persist()

    def _on_vision_max_dim(self, value: int) -> None:
        self._config.vision.max_image_dim = value
        self._persist()

    def _on_vision_max_tokens(self, value: int) -> None:
        self._config.vision.max_tokens = value
        self._persist()

    def _on_vision_temperature(self, value: int) -> None:
        f = value / 100.0
        self._config.vision.temperature = f
        self.vision_temperature_label.setText(f"{f:.2f}")
        self._persist()

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("save_config failed from ModelsTab")
            return
        try:
            self._on_change()
        except Exception:
            log.exception("on_change callback raised from ModelsTab")
