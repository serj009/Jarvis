"""Jarvis configuration — data classes, load / save, migration chain.

Design notes (kept here so future readers see the rationale without digging history):

- Path is injected (load_config(path) / save_config(cfg, path)). The default lives
  in `default_config_path()`; OS-specific helpers will move to `platform/` once
  that layer exists. Until then, the one branch here is contained.
- `extra="forbid"` rejects unknown keys to catch typos in hand-edited JSON.
  Migrations must explicitly drop removed fields.
- `validate_assignment=True` makes bad UI writes fail at the assignment site
  rather than at next save.
- Save is atomic: write tmp, replace.
- Secrets (research API keys, MCP auth tokens) are encrypted at rest with
  Windows DPAPI. Encryption is a *persistence-boundary* concern: `save_config`
  encrypts on the way out and `load_config` decrypts on the way in, so the
  in-memory `JarvisConfig` always holds plaintext and every consumer
  (Settings UI, deep-research runner, MCP client) is unchanged. See
  `jarvis/platform/secrets.py` for the storage format and the documented
  non-Windows plaintext fallback. `_SECRET_FIELD_PATHS` below is the single
  list of what counts as a secret -- add to it, not to individual call sites.
- Migrations are a chain of v(N) -> v(N+1) functions, applied in sequence.
  Today there is only one schema version, so MIGRATIONS is empty; the comment
  in MIGRATIONS is the template for adding the first real migration.
- This module deliberately does not depend on `events.py`. ConfigChanged events
  are the responsibility of a future `ConfigManager` wrapper (Phase 5-ish)
  which will own (JarvisConfig, EventBus), expose set(path, value), compute
  the diff, and emit. Decision: chokepoint emission rather than emit-from-UI
  so any future caller (first-launch flow, migration completion, test
  fixtures) gets the event for free.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from os import environ
from pathlib import Path
from typing import Literal

import logging
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jarvis.platform.secrets import decrypt_secret, encrypt_secret

CURRENT_SCHEMA_VERSION = 35


DEFAULT_SYSTEM_PROMPT = (
    "You are Jarvis — calm, dryly witty, British AI assistant. "
    "Address the user as 'sir'. Keep replies to 1-2 sentences.\n"
    "\n"
    "RULES:\n"
    "1. Reply in the user's language (русский / українська / English).\n"
    "2. ACTION request → CALL the matching tool. Never just SAY you did it.\n"
    "3. QUESTION or chat (math, jokes, facts, advice, stories, counting) "
    "→ answer in words. No tool call.\n"
    "4. Unclear request → ask for clarification. Don't guess a tool.\n"
    "\n"
    "EXAMPLES:\n"
    '- "открой хром" / "open chrome" → open_app\n'
    '- "какая погода?" / "яка погода?" → get_weather\n'
    '- "включи джаз" / "play some jazz" → play_youtube_music\n'
    '- "сколько будет 2+2" / "tell a joke" → text answer, no tool\n'
    "\n"
    "PLAY disambiguation:\n"
    "- Music (song, artist, genre, 'поставь музыку'): play_youtube_music\n"
    "- Video game (Steam, 'запусти игру'): launch_steam_game\n"
    "- Desktop app ('открой программу', browser, editor): open_app\n"
    "- Workspace ('open my workspace'): launch_workspace\n"
    "\n"
    "RESEARCH:\n"
    "- Quick web lookup → research\n"
    "- In-depth report → deep_research\n"
    "- 'enable deep research ultra' → enable_deep_research_ultra\n"
    "\n"
    "Tools need no admin permissions. Don't hallucinate restrictions."
)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class GeneralConfig(_Base):
    start_with_windows: bool = False
    minimize_to_tray: bool = True
    show_overlay: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Speak a short confirmation phrase ("Going to sleep, sir.") before
    # ACTIVE/MUTED -> SLEEPING transitions. The coordinator awaits the
    # phrase before unloading TTS; a Wake request during the phrase
    # cancels the transition. Set False for silent sleep.
    sleep_confirmation: bool = True
    # Flipped to True once the first-run onboarding walkthrough is
    # dismissed or finished. The Qt main thread auto-shows the
    # OnboardingPanel when this is False.
    first_run_completed: bool = False
    # Play short synthesised tones (beep on wake, ding on success, etc.)
    # via sounddevice. Requires numpy + sounddevice; falls back silently
    # if either is missing. Volume is 0.0–1.0, default 0.3.
    sound_effects_enabled: bool = True
    sound_effects_volume: float = Field(default=0.3, ge=0.0, le=1.0)


class AudioConfig(_Base):
    # None = auto-detect at first launch via device_detector.py.
    # The smart detector filters virtual/loopback devices, prefers WASAPI,
    # and ranks physical microphones / speakers by channel count and sample
    # rate.  Users can still pin a specific device in Settings → Voice; the
    # substring-matching in audio/devices.py and tts.py falls back to the
    # system default with a warning when the named device is not found.
    input_device: str | None = None
    output_device: str | None = None
    prefer_respeaker: bool = True


class WakeWordConfig(_Base):
    enabled: bool = True
    sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    model: str = "hey_jarvis"


class STTConfig(_Base):
    # GPU-accelerated multilingual STT (UA/RU/EN).
    # "small" is the default — ~500 MB, ~85-90% accuracy for RU/UK/EN.
    # setup_jarvis.py downloads "small" on first run.
    # "base" is lighter (~150 MB) but less accurate (~70% for RU).
    # The .en suffix restricts to English only; avoid for multilingual.
    # "medium" is much larger (~1.5 GB); not recommended on CPU.
    model_size: Literal[
        "tiny", "tiny.en", "base", "base.en",
        "small", "small.en", "medium", "medium.en",
    ] = "small"
    # T2.1: "auto" enables Whisper language auto-detection (UA/RU/EN).
    # Fixed codes ("en", "ru", "uk") lock to one language; "auto" is
    # recommended for the multilingual project.
    language: str = "auto"
    # Device for Whisper inference.
    # "auto" = try CUDA first, fall back to CPU if unavailable.
    # "cuda" = force GPU (falls back to CPU with WARNING if no CUDA).
    # "cpu"  = force CPU (safe, always works).
    device: Literal["auto", "cpu", "cuda"] = "auto"
    # Compute type for Whisper model quantisation.
    # "auto" = float16 on CUDA, int8 on CPU (best defaults for each).
    # Explicit values override: "int8" (CPU-friendly), "float16" (GPU),
    # "float32" (highest precision, most VRAM).
    compute_type: Literal["auto", "int8", "float16", "float32"] = "auto"
    # T2.4: Confidence thresholds.
    # confidence >= proceed -> send to LLM as-is.
    # clarify <= confidence < proceed -> ask user to repeat.
    # confidence < clarify -> silently ignore (noise / hallucination).
    confidence_proceed: float = Field(default=0.70, ge=0.0, le=1.0)
    confidence_clarify: float = Field(default=0.30, ge=0.0, le=1.0)
    # Max times JARVIS asks "could you repeat?" before giving up.
    max_clarify_retries: int = Field(default=3, ge=1, le=10)


class TTSConfig(_Base):
    voice: str = "en_GB-alan-medium"
    # Per-language voice assignments. When detected language changes,
    # TTS switches to the appropriate voice automatically.
    # To use a custom trained voice, just change the value:
    #   voices_ru: "ru_RU-baranov-custom"
    voices_en: str = "en_GB-alan-medium"
    voices_ru: str = "ru_RU-dmitri-medium"
    voices_uk: str = "uk_UA-mykyta-high"
    # Backend for future Qwen3-TTS: "piper" (CPU) or "qwen3_tts" (GPU)
    # When "auto", uses Qwen3 if available, falls back to Piper
    backend: str = "piper"
    speed: float = Field(default=1.0, gt=0.0, le=4.0)
    volume: float = Field(default=1.0, ge=0.0, le=1.0)


class LLMConfig(_Base):
    model: str = "qwen3:8b"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, gt=0)
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    # Ollama keep_alive: how long the daemon keeps the model resident in
    # Ollama context window. 16384 costs ~1.2 GB more KV cache on
    # qwen3:8b vs 8192, but doubles the usable conversation window.
    # MUST stay the same across all requests (warm-up included):
    # changing num_ctx makes Ollama reload the model.
    num_ctx: int = Field(default=16384, gt=0)
    # Ollama keep_alive: how long the daemon keeps the model resident in
    # VRAM between requests. 1800 s = 30 min handles realistic
    # conversational gaps without re-paying cold-load latency. SLEEPING
    # mode explicitly evicts via keep_alive=0; this is the "active use"
    # default, not a memory-pressure ceiling.
    keep_alive_seconds: int = 1800
    # Conversation history length (number of turn messages retained,
    # distinct from max_tokens which caps per-response output). The
    # system prompt is not counted -- it's standing context, not a turn.
    max_turns: int = Field(default=10, gt=0)
    # Idle time after which the next user message starts a fresh
    # conversation (history cleared, system prompt re-applied). Stale
    # context degrades LLM response quality and creates surprising
    # "you said X two hours ago" behavior.
    inactivity_timeout_seconds: int = Field(default=300, gt=0)
    # How long after the last assistant response a new wake-word activation
    # is still considered a follow-up (history preserved). After this window
    # the activation starts a fresh session (history cleared).
    # Trade-offs: 10 s — aggressive follow-ups don't link cleanly;
    # 300 s — cross-task contamination risk returns. 60 s matches typical
    # conversational follow-up tempo without leaking prior task context.
    conversation_continuity_seconds: int = Field(default=60, gt=0)
    # Maximum LLM invocations per user turn when the model calls tools.
    # The router feeds each tool result back as a role:"tool" message and
    # re-invokes the model so it can act on what it got back ("check the
    # weather, and if it's cold open my coat app"). Every extra iteration
    # is another full inference, so this is a latency budget rather than
    # a capability ceiling: 3 covers the two-step requests people
    # actually make while capping the worst case at three inferences on
    # a local 7B. Set to 1 to disable the feedback loop and restore
    # one-shot dispatch — the tool's own output is spoken verbatim,
    # lowest latency, no follow-up reasoning.
    max_tool_iterations: int = Field(default=3, ge=1, le=10)

    # Universal provider support: by default "ollama" for full backward
    # compatibility. When set to "openai"/"anthropic"/"groq", the main
    # LLM uses a cloud API instead of local Ollama. The `model` field
    # stays as the Ollama model name for provider="ollama"; for API
    # providers, `api_model` is used instead.
    provider: str = "ollama"
    api_model: str = ""
    api_key: str = ""
    api_base_url: str = ""


class ToolsConfig(_Base):
    # Convention: a tool absent from `enabled` is treated as enabled.
    # The registry is the single source of truth for this lookup.
    # type_into_active_window is disabled by default: it simulates keystrokes
    # into the active window and LLM misfires can type unintended content into
    # user apps. Enable via Settings → Tools.
    enabled: dict[str, bool] = Field(
        default_factory=lambda: {"type_into_active_window": False}
    )


class HotkeysConfig(_Base):
    mute: str = "ctrl+shift+m"
    push_to_talk: str | None = None
    open_settings: str = "ctrl+shift+,"
    # Opens the command palette — keyboard-driven fuzzy launcher for
    # every Jarvis capability. Set to "" to disable the binding.
    command_palette: str = "ctrl+shift+p"
    # Opens / closes the text chat panel (alternative to voice).
    # Set to "" to disable.
    chat: str = "ctrl+enter"


# Default Trayce MCP endpoint. The port is overridden at connect time by
# %LOCALAPPDATA%\Trayce\port.txt when present (see tools/mcp_client.py).
DEFAULT_TRAYCE_URL = "http://127.0.0.1:52945/mcp"


class MCPServerConfig(_Base):
    # Defaults describe a Trayce connection so a bare MCPServerConfig() is
    # the Trayce entry; the fresh-config default list ships it disabled so
    # users opt in via Settings → Tools.
    name: str = "trayce"
    url: str = DEFAULT_TRAYCE_URL
    enabled: bool = True
    # When True, the auth token is read from
    # %LOCALAPPDATA%\Trayce\http-auth-token.txt at connect time (the
    # Trayce convention). When False, `auth_token` below is used verbatim.
    auth_token_from_file: bool = True
    auth_token: str | None = None
    # Gate every tool this server publishes behind the confirmation
    # prompt (see jarvis/tools/registry.py). Per SERVER and not per tool
    # because the tool list is the server's to define: it can add,
    # rename and re-describe tools between two connections, so a
    # per-tool opt-in would be consent given to a name the operator of
    # the server controls. The server is the thing the user actually
    # chose to trust, so the server is the unit the setting attaches to.
    #
    # Default False so existing configs keep working unchanged, and
    # because the honest default for a locally-run, user-added server
    # (Trayce, a filesystem bridge) is the one that does not prompt on
    # every call. Users pointing Jarvis at anything less local should
    # turn it on — Settings -> Tools has the checkbox.
    requires_confirmation: bool = False


def _default_mcp_servers() -> list[MCPServerConfig]:
    """Fresh installs ship a disabled Trayce entry so the Settings → Tools
    toggle is discoverable without the user hand-authoring JSON."""
    return [MCPServerConfig(name="trayce", url=DEFAULT_TRAYCE_URL, enabled=False)]


class LifecycleConfig(_Base):
    # Automatically sleep after this many minutes of total inactivity
    # (no wake-word, no STT, no LLM, no TTS). Opt-in: False by default.
    auto_sleep_enabled: bool = False
    idle_timeout_minutes: int = Field(default=30, gt=0, le=1440)
    # Reserved for future rules — no-op in Task 2.
    auto_sleep_on_low_battery: bool = False
    auto_sleep_on_user_idle: bool = False


class PipelineConfig(_Base):
    """Phase 3 pipeline runner settings (T3.3 / T3.4)."""

    # T3.4: Per-stage timeouts (seconds). If a stage exceeds its timeout,
    # the pipeline aborts the turn and returns to IDLE with a spoken error.
    # 0 = no timeout (not recommended for production).
    stt_timeout_s: float = Field(default=10.0, ge=0.0)
    llm_timeout_s: float = Field(default=20.0, ge=0.0)
    tts_timeout_s: float = Field(default=15.0, ge=0.0)

    # T3.4: Thinking phrases -- JARVIS speaks a short phrase via Piper (CPU)
    # immediately after STT, before the LLM starts generating. The user
    # never hears silence > 1-2 seconds. Disable if latency is already low.
    thinking_phrases_enabled: bool = True

    # T3.3: Half-duplex mode -- mute the microphone input while JARVIS is
    # SPEAKING. Prevents speaker-to-mic feedback from triggering false VAD
    # events without requiring AEC. Recommended for desktop speakers.
    # Disable for headphones (where barge-in is more useful).
    half_duplex: bool = True

    # T3.3: Barge-in / echo cancellation mode. Controls how JARVIS handles
    # mic input while speaking. Values: "half_duplex" (mute mic, default),
    # "aec" (WebRTC echo cancellation, requires pywebrtc-audio or echoff),
    # "headphones" (no processing, headphones prevent echo).
    # When "aec" is selected but no library is installed, falls back to
    # half_duplex automatically with a warning.
    barge_in_mode: str = "half_duplex"

    # T3.4: Max consecutive auto-recovery attempts before giving up.
    max_recovery_attempts: int = Field(default=3, ge=1, le=10)


class DebugConfig(_Base):
    # Log wake-word score every ~10 frames while Jarvis is SPEAKING.
    # Diagnostic confirmed working (wake-word interrupt verified 2026-05-19).
    # AEC deferred — see BUILD.md Phase 6 Task 4 and pipeline.py.
    log_wake_during_speaking: bool = False


class UIConfig(_Base):
    # Width of the research panel in pixels. Persisted so the user's last
    # resize survives restarts. Clamped to [280, 700] by the panel itself.
    research_panel_width: int = Field(default=420, ge=280, le=700)


class ResearchConfig(_Base):
    # Quick research uses the main llm.model. Deep research uses a smart
    # planner + a fast worker so the planner reasons about structure and
    # the worker (3B-class) does cheap bullet extraction over many pages.
    #
    # Empty model strings fall back to the main llm.model so a fresh
    # install works without forcing the user to pull a second model.
    api_key: str | None = None  # legacy; ignored. Kept for migration compat.
    planner_model: str = ""
    worker_model: str = ""
    depth: int = Field(default=5, ge=2, le=10)
    breadth: int = Field(default=5, ge=2, le=10)
    fetch_pages: bool = True
    max_page_chars: int = Field(default=6000, ge=1000, le=20000)
    enable_gap_fill: bool = True
    # Ultra: Brave search + Jina Reader + Groq 70B planner + 3x gap-fill (free APIs).
    ultra_enabled: bool = False
    brave_api_key: str = ""
    groq_api_key: str = ""
    ultra_planner_model: str = "groq/llama-3.3-70b-versatile"
    ultra_gap_fill_iterations: int = Field(default=3, ge=1, le=5)


def _default_cursor_path() -> str:
    return str(
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "cursor"
        / "Cursor.exe"
    )


def _default_workspace_apps() -> list[WorkspaceAppEntry]:
    """Dev-machine defaults; users customize in Settings → General."""
    return [
        WorkspaceAppEntry(
            label="Cursor",
            kind="executable",
            target=_default_cursor_path(),
        ),
        WorkspaceAppEntry(
            label="Apple Music",
            kind="shell",
            target=(
                r"shell:AppsFolder\AppleInc.AppleMusicWin_nzyj5cx40ttqa!AppleMusic"
            ),
        ),
    ]


class WorkspaceAppEntry(_Base):
    """One app to launch when the user says 'open my workspace'."""

    label: str = Field(min_length=1, description="Display name, e.g. Cursor")
    kind: Literal["installed_app", "executable", "shell"] = Field(
        description=(
            "installed_app: fuzzy-match Start Menu name; "
            "executable: full path to .exe; "
            "shell: explorer.exe argument (e.g. shell:AppsFolder\\...)"
        ),
    )
    target: str = Field(
        min_length=1,
        description="App name, .exe path, or shell URI depending on kind",
    )


class WorkspaceConfig(_Base):
    apps: list[WorkspaceAppEntry] = Field(default_factory=_default_workspace_apps)


class VisionConfig(_Base):
    """Settings for the `see_screen` tool (multimodal Ollama call).

    The vision model is independent from the conversational `llm.model`
    because text-only models (qwen2.5:7b-instruct, etc.) cannot accept
    images. Users must `ollama pull llava:7b` (or another vision-capable
    model) for the feature to work. Empty `model` disables see_screen
    cleanly (the tool returns a clear "no vision model configured" message
    instead of failing late inside Ollama).
    """

    # llava:7b is a 4-5 GB int4 multimodal model that runs on CPU and
    # ships with the Ollama default registry. moondream / minicpm-v are
    # other reasonable defaults; we don't try to pick one for the user.
    model: str = "llava:7b"
    # Long-edge pixel cap before we ship the image to Ollama. Vision
    # models do their own internal resize; this cap keeps the HTTP body
    # small (1280 px PNG is typically <500 KB) and the prompt-eval time
    # bounded. Lowering to 640 makes weak GPUs much snappier at a small
    # cost to text legibility in the screenshot.
    max_image_dim: int = Field(default=1280, ge=320, le=4096)
    max_tokens: int = Field(default=512, ge=64, le=4096)
    # Lower temperature than chat default (0.7) — vision descriptions
    # benefit from determinism so repeated calls describe the same UI
    # consistently rather than reinventing nouns each time.
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)

    # Universal provider support: by default "ollama" for full backward
    # compatibility. When set to "openai"/"anthropic", the vision model
    # uses a cloud API (GPT-4o, Claude 3.5 Sonnet, etc.) which have
    # built-in vision capabilities. For API providers, `api_model` is
    # the model name (e.g. "gpt-4o", "claude-3-5-sonnet-20241022").
    provider: str = "ollama"
    api_model: str = ""
    api_key: str = ""
    api_base_url: str = ""


class MemoryConfig(_Base):
    """Settings for the memory subsystem embedding search (T4.1/T4.2).

    When ``embeddings_enabled`` is True and sentence-transformers is
    installed, JARVIS builds a FAISS index for semantic search across
    stored facts. The model is downloaded once (~420 MB) and cached
    locally for offline use.
    """

    embeddings_enabled: bool = True
    embeddings_model: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    embeddings_device: str = "cpu"
    embeddings_top_k: int = 20


class WeatherConfig(_Base):
    # No hard-coded default coordinates: the weather tool auto-detects the
    # user's approximate location via ipapi.co on first use when both
    # values are None, then persists the result. Users can also enter
    # coordinates manually or click "Detect from IP" under
    # Settings → General. Shipping None defaults avoids embedding any
    # maintainer-specific location in the open-source repo.
    latitude: float | None = None
    longitude: float | None = None
    unit: Literal["celsius", "fahrenheit"] = "fahrenheit"


def _default_sandbox_allowed_paths() -> list[str]:
    """Default sandbox whitelist for fresh installs.

    Seeds ~/Desktop and ~/Documents so new users have a reasonable
    working surface without hand-editing config. The strings are stored
    as-is (not resolved) so the config is portable across home-directory
    renames and readable in JSON.
    """
    return ["~/Desktop", "~/Documents", "~/Downloads"]


class SandboxConfig(_Base):
    """Workspace sandbox settings (Phase 5, T5.1).

    Controls which filesystem paths tools are allowed to access and
    whether destructive actions require user confirmation.
    """

    # Directories tools may read/write. Paths are expanded at validation
    # time (~ → home, env vars, resolve). A path and all its descendants
    # are allowed; everything else is blocked.
    allowed_paths: list[str] = Field(
        default_factory=_default_sandbox_allowed_paths,
        description="Directories tools may access (~ expanded at runtime).",
    )
    # Master switch for destructive-action confirmation. When False, tools
    # tagged DESTRUCTIVE or SYSTEM run without prompting — useful for
    # automation / scripting, but NOT recommended for interactive use.
    confirm_destructive: bool = Field(
        default=True,
        description="Require user confirmation before destructive actions.",
    )


def _default_idle_game_patterns() -> list[str]:
    """Default IDLE game process-name patterns (matched as substrings)."""
    return [
        "cookie", "clicker", "idle", "autoclicker", "auto_clicker",
        "autoclick", "clickerheroes", "adventurecapitalist",
        "realm_grinder", "ngu_idle", "antimatter", "melvor",
        "idleminer", "idle_slayer", "taptitans", "egg_inc",
    ]




class SecurityConfig(_Base):
    """Encryption-at-rest settings for user data files.

    When enabled, sensitive data in SQLite columns (memory text, relation
    summaries) and JSON files (profile, bookmarks, aliases, etc.) is
    encrypted with Windows DPAPI before writing to disk.  On non-Windows
    platforms the setting is honoured but encryption is a no-op
    (plaintext fallback with a warning, same as ``secrets.py``).
    """

    encrypt_memory: bool = True
    encrypt_json_files: bool = True


class GameModeConfig(_Base):
    """Settings for automatic VRAM management when games are running.

    When a resource-heavy (MAIN) game is detected, JARVIS evicts models
    from VRAM, switches STT to a lightweight CPU model, and preloads
    game-specific memories for instant answers.
    """

    enabled: bool = True
    scan_interval_seconds: int = Field(default=30, ge=5, le=300)
    idle_game_patterns: list[str] = Field(
        default_factory=_default_idle_game_patterns,
    )
    # Custom MAIN game executables not in Steam (process names without .exe)
    custom_main_games: list[str] = Field(default_factory=list)


class CodingConfig(_Base):
    """Settings for the Coding Agent — a separate LLM for code generation.

    The coding model can be:
    - "main" provider → same as llm.model (Ollama, no extra download)
    - "ollama" provider → local coding model (e.g. qwen2.5-coder:7b)
    - "openai"/"anthropic"/"groq" → cloud API
    """

    # Provider: "main" (use main LLM), "ollama", "openai", "anthropic", "groq"
    provider: Literal["main", "ollama", "openai", "anthropic", "groq"] = "main"

    # Ollama model (when provider="ollama")
    ollama_model: str = "qwen2.5-coder:7b"

    # API settings (when provider is openai/anthropic/groq)
    api_model: str = ""
    api_key: str = ""
    api_base_url: str = ""

    # Agent behavior
    max_iterations: int = Field(default=10, ge=1, le=50)
    auto_commit: bool = False
    default_workspace: str = ""

    # Preferred language: "" = auto-detect, or one of:
    # python, javascript, typescript, csharp, java, go, rust, cpp
    preferred_language: str = ""


class ImageGenConfig(_Base):
    """Image generation settings.

    Supports local (ComfyUI, Automatic1111) and cloud (OpenAI, Stability,
    Replicate). Local providers have NO content filtering — user controls
    the model.
    """

    # Provider: "comfyui", "a1111", "openai", "stability", "replicate", "disabled"
    provider: str = "disabled"

    # Local settings
    local_endpoint: str = "http://127.0.0.1:8188"  # ComfyUI default
    local_model: str = ""  # SD model name (user selects from available)
    nsfw_filter: bool = False  # user controls — default OFF for local

    # API settings
    api_key: str = ""  # encrypted via DPAPI
    api_model: str = ""  # "dall-e-3", "stable-diffusion-xl", etc.

    # Generation defaults
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    steps: int = Field(default=20, ge=1, le=100)
    cfg_scale: float = Field(default=7.0, ge=1.0, le=30.0)
    save_directory: str = ""  # default: ~/Pictures/Jarvis

    # VRAM management
    auto_vram_swap: bool = True  # evict LLM when generating

    # Prompt enhancement
    enhance_mode: str = "template"   # "off" / "template" / "llm"
    default_style: str = "photorealistic"

    # Separate model for image prompt engineering + generation control.
    # When image_model_provider != "main", image prompts are processed by
    # a dedicated model — main LLM never sees the prompt (full isolation).
    # Use an uncensored model (e.g. "dolphin-mistral:7b") for unrestricted
    # prompt engineering.  "main" = use same LLM (current behaviour).
    image_model_provider: str = "main"  # "main", "ollama", "openai", "anthropic", "groq"
    image_model_name: str = ""          # e.g. "dolphin-mistral:7b", "nous-hermes-2:7b"
    image_model_api_key: str = ""       # API key when using cloud provider
    image_model_api_base_url: str = ""  # custom endpoint (optional)


class JarvisConfig(_Base):
    schema_version: int = CURRENT_SCHEMA_VERSION
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    wake_word: WakeWordConfig = Field(default_factory=WakeWordConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    hotkeys: HotkeysConfig = Field(default_factory=HotkeysConfig)
    lifecycle: LifecycleConfig = Field(default_factory=LifecycleConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    mcp_servers: list[MCPServerConfig] = Field(default_factory=_default_mcp_servers)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    game_mode: GameModeConfig = Field(default_factory=GameModeConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    coding: CodingConfig = Field(default_factory=CodingConfig)
    image_gen: ImageGenConfig = Field(default_factory=ImageGenConfig)

# --- secrets at rest -------------------------------------------------------

# Dotted paths of the fields encrypted in config.json. These operate on the
# raw JSON dict (not the pydantic model) because encryption happens at the
# persistence boundary: save writes ciphertext, load hands back plaintext.
#
# Deliberately a *narrow* list, not "encrypt everything that looks secret":
# the rest of config.json must stay human-readable and hand-editable, which
# is a real usability property of this project. Add a path here when a new
# secret field lands; do not sprinkle encrypt/decrypt calls at call sites.
_SECRET_FIELD_PATHS: tuple[tuple[str, str], ...] = (
    ("research", "brave_api_key"),
    ("research", "groq_api_key"),
    # Dead since v16 (nothing reads it) but it is still a key-shaped field
    # that a user could have pasted into by hand before it was retired, and
    # it defaults to None, so covering it costs nothing and cannot break a
    # consumer that does not exist.
    ("research", "api_key"),
    ("coding", "api_key"),
    ("llm", "api_key"),
    ("vision", "api_key"),
    ("image_gen", "api_key"),
    ("image_gen", "image_model_api_key"),
)

# Secrets inside the mcp_servers list, keyed by field name. Handled
# separately because the containing structure is a list, not a section.
_SECRET_MCP_FIELDS: tuple[str, ...] = ("auth_token",)

# Callable[[value, setting_name], value] — encrypt_secret / decrypt_secret.
_SecretTransform = Callable[[object, str], object]


def _walk_secrets(data: dict, transform: _SecretTransform) -> dict:
    """Apply `transform` to every secret field present in `data`, in place.

    Missing sections and missing keys are skipped rather than created: this
    runs over configs from every schema version, and materialising a key
    that the schema does not have yet would trip `extra="forbid"`.
    """
    for section, key in _SECRET_FIELD_PATHS:
        block = data.get(section)
        if isinstance(block, dict) and key in block:
            block[key] = transform(block[key], f"{section}.{key}")
    servers = data.get("mcp_servers")
    if isinstance(servers, list):
        for index, server in enumerate(servers):
            if not isinstance(server, dict):
                continue
            label = server.get("name") or index
            for key in _SECRET_MCP_FIELDS:
                if key in server:
                    server[key] = transform(
                        server[key], f"mcp_servers[{label}].{key}"
                    )
    return data


def encrypt_secrets(data: dict) -> dict:
    """Turn a config dict's plaintext secrets into on-disk ciphertext."""
    return _walk_secrets(
        data, lambda value, setting: encrypt_secret(value, setting=setting)
    )


def decrypt_secrets(data: dict) -> dict:
    """Turn a config dict's on-disk secrets back into plaintext.

    Values without the `dpapi:` prefix are legacy plaintext and pass through
    untouched — that is what keeps pre-v21 configs working. Values that
    cannot be decrypted (config copied from another machine or user account)
    come back empty with a warning rather than blowing up startup.
    """
    return _walk_secrets(
        data, lambda value, setting: decrypt_secret(value, setting=setting)
    )


# --- migration -------------------------------------------------------------

# --- migration (extracted to jarvis/core/migrations.py) --------------------
# All _migrate_vX_to_vY functions, MIGRATIONS dict, migrate(),
# ConfigMigrationError, and old prompt constants now live in migrations.py.
# Re-exported here so existing consumers (tests, app.py) work unchanged.

from jarvis.core.migrations import (  # noqa: E402
    ConfigMigrationError,
    Migration,
    MIGRATIONS,
    migrate,
)

# Re-export old prompt constants for any external consumers.
from jarvis.core.migrations import (  # noqa: E402, F811
    _SYSTEM_PROMPT_V3,
    _SYSTEM_PROMPT_V6,
    _SYSTEM_PROMPT_V26,
)

# --- persistence -----------------------------------------------------------


def default_config_path() -> Path:
    """Default config location.

    Windows: `%APPDATA%/Jarvis/config.json`
    Other:   `~/.jarvis/config.json` (skeleton; full platform support is
             out of scope for v1, see SPEC § Out of scope).
    """
    if sys.platform == "win32":
        appdata = environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Jarvis" / "config.json"
    return Path.home() / ".jarvis" / "config.json"


_log = logging.getLogger(__name__)

def load_config(path: Path | None = None) -> JarvisConfig:
    """Load config from disk, applying migrations. If the file does not exist,
    write a default config to that path and return it."""
    p = path if path is not None else default_config_path()
    if not p.exists():
        cfg = JarvisConfig()
        save_config(cfg, p)
        return cfg
    with p.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ConfigMigrationError(
            f"config root must be a JSON object, got {type(raw).__name__}"
        )
    # --- Config backup before migration (T0.4) ---
    old_version = raw.get("schema_version", 0)
    bak = p.with_suffix(p.suffix + ".bak")
    if old_version < CURRENT_SCHEMA_VERSION:
        try:
            shutil.copy2(p, bak)
            _log.info(
                "Config backed up before migration v%s→v%s: %s",
                old_version, CURRENT_SCHEMA_VERSION, bak,
            )
        except OSError as exc:
            _log.warning("Failed to back up config before migration: %s", exc)
    try:
        raw = migrate(raw)
    except Exception:
        if bak.exists():
            _log.warning("Migration failed — restoring config from %s", bak)
            shutil.copy2(bak, p)
        raise
    # Decrypt after migrating: migrations reason about the on-disk shape,
    # and everything downstream of here expects plaintext. Legacy plaintext
    # values survive this untouched and get encrypted on the next save.
    raw = decrypt_secrets(raw)
    return JarvisConfig.model_validate(raw)


def save_config(config: JarvisConfig, path: Path | None = None) -> None:
    """Write config atomically (tmp + replace).

    Secret fields are encrypted on the way out (see `_SECRET_FIELD_PATHS`);
    the in-memory `config` is not modified.
    """
    p = path if path is not None else default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # model_dump builds a fresh dict, so mutating it does not touch `config`.
    data = encrypt_secrets(config.model_dump(mode="json"))
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(p)


# ---------------------------------------------------------------------------
# Human-readable load errors (roadmap T1.1: "an invalid config gives a clear
# error, not a stack trace"). load_config() keeps raising its original
# exception types (tests rely on them); callers that face the user catch
# CONFIG_LOAD_ERRORS and show format_config_error() instead of a traceback.
# ---------------------------------------------------------------------------

CONFIG_LOAD_ERRORS: tuple[type[BaseException], ...] = (
    json.JSONDecodeError,
    UnicodeDecodeError,
    ValidationError,
    ConfigMigrationError,
)

_MAX_REPORTED_ERRORS = 10
_MAX_INPUT_REPR = 60


def _short_repr(value: object) -> str:
    text = repr(value)
    if len(text) > _MAX_INPUT_REPR:
        text = text[: _MAX_INPUT_REPR - 3] + "..."
    return text


def _format_validation_item(err: dict) -> str:
    loc_parts = [str(part) for part in err.get("loc", ())]
    loc = ".".join(loc_parts) or "<root>"
    err_type = err.get("type", "")
    if err_type == "extra_forbidden":
        return f"- {loc}: unknown key (typo, or a setting from another version?)"
    msg = err.get("msg", "invalid value")
    # Never echo values of secret-looking fields back to the user/log.
    is_secret = any("key" in p.lower() or "token" in p.lower() for p in loc_parts)
    if err_type == "missing" or is_secret or "input" not in err:
        return f"- {loc}: {msg}"
    return f"- {loc}: {msg} (got {_short_repr(err['input'])})"


def format_config_error(exc: BaseException, path: Path) -> str:
    """Turn a load_config() failure into a short message a user can act on."""
    if isinstance(exc, json.JSONDecodeError):
        details = [
            f"- JSON syntax error at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ]
    elif isinstance(exc, UnicodeDecodeError):
        details = ["- the file is not valid UTF-8 text"]
    elif isinstance(exc, ValidationError):
        errors = exc.errors()
        details = [_format_validation_item(e) for e in errors[:_MAX_REPORTED_ERRORS]]
        if len(errors) > _MAX_REPORTED_ERRORS:
            details.append(f"- ...and {len(errors) - _MAX_REPORTED_ERRORS} more")
    elif isinstance(exc, ConfigMigrationError):
        details = [f"- {exc}"]
    else:
        details = [f"- {type(exc).__name__}: {exc}"]
    return "\n".join(
        [
            "Jarvis cannot start: the config file is invalid.",
            f"File: {path}",
            "",
            *details,
            "",
            "Fix the file (or restore it from backup) and start Jarvis again.",
        ]
    )
