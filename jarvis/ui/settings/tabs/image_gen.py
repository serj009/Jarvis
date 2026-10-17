"""Image Generation settings tab: provider, model, API keys, generation params.

New tab '🎨 Image Gen' in the Settings sidebar. Supports both local
(ComfyUI, A1111) and cloud (OpenAI, Stability AI, Replicate) providers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig, save_config
from jarvis.ui.settings._widgets import SectionCard, ToggleSwitch, slider_with_label

log = logging.getLogger(__name__)

_ENHANCE_MODES = [
    ("off",      "Off"),
    ("template", "Template (instant, no LLM)"),
    ("llm",      "LLM (detailed, uses coding model)"),
]
_ENHANCE_INDEX = {key: i for i, (key, _) in enumerate(_ENHANCE_MODES)}

_IMAGE_MODEL_PROVIDERS = [
    ("main",      "Main LLM (current behaviour)"),
    ("ollama",    "Ollama Local (can be uncensored)"),
    ("openai",    "OpenAI"),
    ("anthropic", "Anthropic"),
    ("groq",      "Groq"),
]
_IMAGE_MODEL_PROVIDER_INDEX = {
    key: i for i, (key, _) in enumerate(_IMAGE_MODEL_PROVIDERS)
}

_STYLES = [
    ("photorealistic", "Photorealistic"),
    ("anime",          "Anime"),
    ("oil_painting",   "Oil Painting"),
    ("digital_art",    "Digital Art"),
    ("sketch",         "Sketch"),
    ("watercolor",     "Watercolor"),
    ("3d_render",      "3D Render"),
    ("pixel_art",      "Pixel Art"),
]
_STYLE_INDEX = {key: i for i, (key, _) in enumerate(_STYLES)}

_PROVIDERS = [
    ("disabled",  "Disabled"),
    ("comfyui",   "ComfyUI (Local)"),
    ("a1111",     "Automatic1111 (Local)"),
    ("openai",    "OpenAI DALL-E"),
    ("stability", "Stability AI"),
    ("replicate", "Replicate"),
]

_PROVIDER_INDEX = {key: i for i, (key, _) in enumerate(_PROVIDERS)}

_LOCAL_PROVIDERS = frozenset({"comfyui", "a1111"})
_API_PROVIDERS = frozenset({"openai", "stability", "replicate"})


class ImageGenTab(QWidget):
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

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.setSpacing(16)

        title = QLabel("Image Generation")
        title.setObjectName("pageTitle")
        desc = QLabel(
            "Configure text-to-image generation with local or cloud providers"
        )
        desc.setObjectName("pageDesc")
        root.addWidget(title)
        root.addWidget(desc)
        root.addSpacing(12)

        # -- Provider card --
        provider_card = SectionCard()
        provider_card.add_header("\U0001f3a8", "Provider")

        self._provider_combo = QComboBox()
        for _key, label in _PROVIDERS:
            self._provider_combo.addItem(label)
        current_idx = _PROVIDER_INDEX.get(config.image_gen.provider, 0)
        self._provider_combo.setCurrentIndex(current_idx)
        provider_card.add_row(
            "Provider", "Which backend to use for image generation",
            self._provider_combo, last=True,
        )
        root.addWidget(provider_card)

        # -- Local settings card --
        local_card = SectionCard()
        local_card.add_header("\U0001f4bb", "Local Settings")

        self._local_endpoint = QLineEdit(config.image_gen.local_endpoint)
        self._local_endpoint.setPlaceholderText("http://127.0.0.1:8188")
        local_card.add_row(
            "Endpoint", "ComfyUI / A1111 server URL",
            self._local_endpoint,
        )

        self._local_model = QLineEdit(config.image_gen.local_model)
        self._local_model.setPlaceholderText("e.g. sd_xl_base_1.0.safetensors")
        local_card.add_row(
            "Model", "Checkpoint model name (from your models folder)",
            self._local_model,
        )

        self._nsfw_filter = ToggleSwitch(checked=config.image_gen.nsfw_filter)
        local_card.add_row(
            "NSFW filter",
            "Enable content filtering for local generation (off by default)",
            self._nsfw_filter, last=True,
        )
        self._local_card = local_card
        root.addWidget(local_card)

        # -- API settings card --
        api_card = SectionCard()
        api_card.add_header("\U0001f511", "API Settings")

        self._api_key = QLineEdit(config.image_gen.api_key)
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("sk-... / stability key / replicate token")
        api_card.add_row("API key", "Encrypted at rest (DPAPI)", self._api_key)

        self._api_model = QLineEdit(config.image_gen.api_model)
        self._api_model.setPlaceholderText(
            "e.g. dall-e-3, stable-diffusion-xl-1024-v1-0"
        )
        api_card.add_row(
            "API model", "Model name for cloud providers",
            self._api_model, last=True,
        )
        self._api_card = api_card
        root.addWidget(api_card)

        # -- Generation defaults card --
        gen_card = SectionCard()
        gen_card.add_header("\u2699\ufe0f", "Generation Defaults")

        # Width
        self._width_slider = QSlider(Qt.Orientation.Horizontal)
        self._width_slider.setRange(256, 2048)
        self._width_slider.setSingleStep(64)
        self._width_slider.setValue(config.image_gen.width)
        self._width_label = QLabel(str(config.image_gen.width))
        self._width_label.setObjectName("settingHint")
        self._width_label.setMinimumWidth(40)
        gen_card.add_row(
            "Width", "Image width in pixels (256–2048)",
            slider_with_label(self._width_slider, self._width_label),
        )

        # Height
        self._height_slider = QSlider(Qt.Orientation.Horizontal)
        self._height_slider.setRange(256, 2048)
        self._height_slider.setSingleStep(64)
        self._height_slider.setValue(config.image_gen.height)
        self._height_label = QLabel(str(config.image_gen.height))
        self._height_label.setObjectName("settingHint")
        self._height_label.setMinimumWidth(40)
        gen_card.add_row(
            "Height", "Image height in pixels (256–2048)",
            slider_with_label(self._height_slider, self._height_label),
        )

        # Steps
        self._steps_slider = QSlider(Qt.Orientation.Horizontal)
        self._steps_slider.setRange(1, 100)
        self._steps_slider.setValue(config.image_gen.steps)
        self._steps_label = QLabel(str(config.image_gen.steps))
        self._steps_label.setObjectName("settingHint")
        self._steps_label.setMinimumWidth(30)
        gen_card.add_row(
            "Steps", "Sampling steps (more = higher quality, slower)",
            slider_with_label(self._steps_slider, self._steps_label),
        )

        # CFG scale
        self._cfg_slider = QSlider(Qt.Orientation.Horizontal)
        self._cfg_slider.setRange(10, 300)  # 1.0–30.0 * 10
        self._cfg_slider.setValue(int(config.image_gen.cfg_scale * 10))
        self._cfg_label = QLabel(f"{config.image_gen.cfg_scale:.1f}")
        self._cfg_label.setObjectName("settingHint")
        self._cfg_label.setMinimumWidth(40)
        gen_card.add_row(
            "CFG scale", "How closely to follow the prompt (1.0–30.0)",
            slider_with_label(self._cfg_slider, self._cfg_label),
        )

        # Save directory
        self._save_dir = QLineEdit(config.image_gen.save_directory)
        self._save_dir.setPlaceholderText("~/Pictures/Jarvis (default)")
        gen_card.add_row(
            "Save directory", "Where generated images are saved",
            self._save_dir, last=True,
        )
        root.addWidget(gen_card)

        # -- VRAM card --
        vram_card = SectionCard()
        vram_card.add_header("\U0001f4a1", "VRAM Management")

        self._auto_vram = ToggleSwitch(checked=config.image_gen.auto_vram_swap)
        vram_card.add_row(
            "Auto VRAM swap",
            "Evict LLM from VRAM during local image generation",
            self._auto_vram, last=True,
        )
        self._vram_card = vram_card
        root.addWidget(vram_card)

        # -- Prompt Enhancement card --
        enhance_card = SectionCard()
        enhance_card.add_header("\u2728", "Prompt Enhancement")

        self._enhance_combo = QComboBox()
        for _key, label in _ENHANCE_MODES:
            self._enhance_combo.addItem(label)
        enhance_idx = _ENHANCE_INDEX.get(config.image_gen.enhance_mode, 1)
        self._enhance_combo.setCurrentIndex(enhance_idx)
        enhance_card.add_row(
            "Mode",
            "Off = raw prompt, Template = instant quality tags, "
            "LLM = detailed rewrite via coding model",
            self._enhance_combo,
        )

        self._style_combo = QComboBox()
        for _key, label in _STYLES:
            self._style_combo.addItem(label)
        style_idx = _STYLE_INDEX.get(config.image_gen.default_style, 0)
        self._style_combo.setCurrentIndex(style_idx)
        enhance_card.add_row(
            "Default style",
            "Art style applied when not specified in the prompt",
            self._style_combo, last=True,
        )
        root.addWidget(enhance_card)

        # -- Image AI Model card (isolated model for prompt engineering) --
        image_model_card = SectionCard()
        image_model_card.add_header("\U0001f9e0", "Image AI Model")

        self._image_model_provider_combo = QComboBox()
        for _key, label in _IMAGE_MODEL_PROVIDERS:
            self._image_model_provider_combo.addItem(label)
        img_prov_idx = _IMAGE_MODEL_PROVIDER_INDEX.get(
            config.image_gen.image_model_provider, 0,
        )
        self._image_model_provider_combo.setCurrentIndex(img_prov_idx)
        image_model_card.add_row(
            "Provider",
            "Set a separate model for image prompts. "
            "Use an uncensored model for unrestricted generation.",
            self._image_model_provider_combo,
        )

        self._image_model_name = QLineEdit(config.image_gen.image_model_name)
        self._image_model_name.setPlaceholderText(
            "e.g. dolphin-mistral:7b, nous-hermes-2:7b"
        )
        image_model_card.add_row(
            "Model name",
            "Ollama model name or API model identifier",
            self._image_model_name,
        )

        self._image_model_api_key = QLineEdit(
            config.image_gen.image_model_api_key,
        )
        self._image_model_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._image_model_api_key.setPlaceholderText("API key (encrypted at rest)")
        image_model_card.add_row(
            "API key",
            "Required for cloud providers (OpenAI, Anthropic, Groq)",
            self._image_model_api_key,
        )

        hint = QLabel(
            "When set, main LLM never sees the image prompt \u2014 full isolation."
        )
        hint.setObjectName("settingHint")
        hint.setWordWrap(True)
        image_model_card.layout().addWidget(hint)

        self._image_model_card = image_model_card
        root.addWidget(image_model_card)

        root.addStretch(1)

        # -- Initial visibility --
        self._update_field_visibility()

        # -- Connections --
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self._local_endpoint.editingFinished.connect(self._on_local_endpoint)
        self._local_model.editingFinished.connect(self._on_local_model)
        self._nsfw_filter.toggled.connect(self._on_nsfw_filter)
        self._api_key.editingFinished.connect(self._on_api_key)
        self._api_model.editingFinished.connect(self._on_api_model)
        self._width_slider.valueChanged.connect(self._on_width)
        self._height_slider.valueChanged.connect(self._on_height)
        self._steps_slider.valueChanged.connect(self._on_steps)
        self._cfg_slider.valueChanged.connect(self._on_cfg)
        self._save_dir.editingFinished.connect(self._on_save_dir)
        self._auto_vram.toggled.connect(self._on_auto_vram)
        self._enhance_combo.currentIndexChanged.connect(self._on_enhance_mode)
        self._style_combo.currentIndexChanged.connect(self._on_default_style)
        self._image_model_provider_combo.currentIndexChanged.connect(
            self._on_image_model_provider,
        )
        self._image_model_name.editingFinished.connect(self._on_image_model_name)
        self._image_model_api_key.editingFinished.connect(self._on_image_model_api_key)

    # -- Visibility --

    def _update_field_visibility(self) -> None:
        provider = _PROVIDERS[self._provider_combo.currentIndex()][0]
        is_local = provider in _LOCAL_PROVIDERS
        is_api = provider in _API_PROVIDERS
        is_active = provider != "disabled"

        self._local_card.setVisible(is_local)
        self._api_card.setVisible(is_api)
        self._vram_card.setVisible(is_local)
        # Generation defaults visible for all active providers
        # (the gen_card parent is always visible, individual controls
        # remain enabled only when provider is active)
        self._width_slider.setEnabled(is_active)
        self._height_slider.setEnabled(is_active)
        self._steps_slider.setEnabled(is_active)
        self._cfg_slider.setEnabled(is_active)
        self._save_dir.setEnabled(is_active)

        # Image model card: visible only when enhance_mode="llm"
        enhance_mode = _ENHANCE_MODES[self._enhance_combo.currentIndex()][0]
        self._image_model_card.setVisible(enhance_mode == "llm")

        # Within the image model card, API key is only for cloud providers
        img_prov = _IMAGE_MODEL_PROVIDERS[
            self._image_model_provider_combo.currentIndex()
        ][0]
        is_cloud = img_prov in ("openai", "anthropic", "groq")
        self._image_model_api_key.setVisible(is_cloud)
        # Model name visible for all except "main" (main uses llm.model)
        self._image_model_name.setVisible(img_prov != "main")

    # -- Handlers --

    def _on_provider_changed(self, index: int) -> None:
        self._config.image_gen.provider = _PROVIDERS[index][0]
        self._update_field_visibility()
        self._persist()

    def _on_local_endpoint(self) -> None:
        self._config.image_gen.local_endpoint = (
            self._local_endpoint.text().strip()
        )
        self._persist()

    def _on_local_model(self) -> None:
        self._config.image_gen.local_model = self._local_model.text().strip()
        self._persist()

    def _on_nsfw_filter(self) -> None:
        self._config.image_gen.nsfw_filter = self._nsfw_filter.isChecked()
        self._persist()

    def _on_api_key(self) -> None:
        self._config.image_gen.api_key = self._api_key.text().strip()
        self._persist()

    def _on_api_model(self) -> None:
        self._config.image_gen.api_model = self._api_model.text().strip()
        self._persist()

    def _on_width(self, value: int) -> None:
        # Snap to nearest 64
        snapped = (value // 64) * 64
        self._config.image_gen.width = max(256, min(2048, snapped))
        self._width_label.setText(str(self._config.image_gen.width))
        self._persist()

    def _on_height(self, value: int) -> None:
        snapped = (value // 64) * 64
        self._config.image_gen.height = max(256, min(2048, snapped))
        self._height_label.setText(str(self._config.image_gen.height))
        self._persist()

    def _on_steps(self, value: int) -> None:
        self._config.image_gen.steps = value
        self._steps_label.setText(str(value))
        self._persist()

    def _on_cfg(self, value: int) -> None:
        cfg = value / 10.0
        self._config.image_gen.cfg_scale = cfg
        self._cfg_label.setText(f"{cfg:.1f}")
        self._persist()

    def _on_save_dir(self) -> None:
        self._config.image_gen.save_directory = self._save_dir.text().strip()
        self._persist()

    def _on_auto_vram(self) -> None:
        self._config.image_gen.auto_vram_swap = self._auto_vram.isChecked()
        self._persist()

    def _on_enhance_mode(self, index: int) -> None:
        self._config.image_gen.enhance_mode = _ENHANCE_MODES[index][0]
        self._update_field_visibility()
        self._persist()

    def _on_default_style(self, index: int) -> None:
        self._config.image_gen.default_style = _STYLES[index][0]
        self._persist()

    def _on_image_model_provider(self, index: int) -> None:
        self._config.image_gen.image_model_provider = (
            _IMAGE_MODEL_PROVIDERS[index][0]
        )
        self._update_field_visibility()
        self._persist()

    def _on_image_model_name(self) -> None:
        self._config.image_gen.image_model_name = (
            self._image_model_name.text().strip()
        )
        self._persist()

    def _on_image_model_api_key(self) -> None:
        self._config.image_gen.image_model_api_key = (
            self._image_model_api_key.text().strip()
        )
        self._persist()

    def _persist(self) -> None:
        try:
            save_config(self._config)
        except Exception:
            log.exception("save_config failed from ImageGenTab")
            return
        try:
            self._on_change()
        except Exception:
            log.exception("on_change callback raised from ImageGenTab")
