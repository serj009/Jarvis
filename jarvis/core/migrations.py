"""Configuration schema migrations extracted from config.py.

All _migrate_vX_to_vY functions, the MIGRATIONS dict, the migrate()
function, ConfigMigrationError, and the old prompt constants that
migrations reference live here.

Importing this module does NOT create a circular dependency:
- config.py imports: ``from jarvis.core.migrations import MIGRATIONS, migrate, ConfigMigrationError``
- migrations.py imports: ``from jarvis.core.config import (DEFAULT_SYSTEM_PROMPT, DEFAULT_TRAYCE_URL,
                            encrypt_secrets, _default_workspace_apps, CURRENT_SCHEMA_VERSION)``
  These are constants and pure functions — no model classes, no load/save.
"""

from __future__ import annotations

from collections.abc import Callable

from jarvis.core.config import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TRAYCE_URL,
    encrypt_secrets,
    _default_workspace_apps,
)

Migration = Callable[[dict], dict]

# ---------------------------------------------------------------------------
# Prompts from prior schema versions — used by migrations to detect and
# replace the old default without overwriting user-customised prompts.
# Moved here from config.py so config.py stays focused on the live schema.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_V3 = (
    "You are Jarvis, an AI assistant inspired by Iron Man. Calm, dryly "
    "witty, British, address the user as 'sir'. Keep responses concise "
    "— one or two sentences usually.\n"
    "\n"
    "CRITICAL TOOL USAGE RULES:\n"
    "- Default behavior is to answer in words, not call tools.\n"
    "- Only call a tool when the user gives a clear, explicit command "
    "matching that exact tool's purpose.\n"
    "- For greetings, factual questions, math, opinions, or unclear "
    "input: answer in words. Do not call any tool.\n"
    "- If you can't understand what the user said, ask for clarification. "
    "Do not guess and call a tool.\n"
    "- Never call tools speculatively or 'just in case'."
)

# Prompt shipped with schema v6 — over-restricted conversational tasks
# (Jarvis would refuse counting/math with "I'm designed to assist with
# computer tasks"). Replaced by v7 default below.
_SYSTEM_PROMPT_V6 = (
    "You are Jarvis, an AI assistant inspired by Iron Man. Calm, dryly "
    "witty, British, address the user as 'sir'. Keep responses concise "
    "— one or two sentences usually.\n"
    "\n"
    "YOUR JOB:\n"
    "- Answer questions, have conversations, perform mental tasks "
    "(math, counting, reasoning, jokes, advice).\n"
    "- For factual questions, just answer. For mental tasks like counting "
    "or arithmetic, just do them.\n"
    "- Match the user's energy and intent. Be useful.\n"
    "\n"
    "TOOL USAGE:\n"
    "- You have tools for specific actions (screenshots, opening apps, "
    "system stats, etc).\n"
    "- ONLY call a tool when the user explicitly asks for that specific "
    "action.\n"
    "- For everything else — questions, conversations, math, counting, "
    "reasoning — answer in words. Do not call any tool.\n"
    "- If the user's request is unclear, ask for clarification. "
    "Don't refuse, don't guess and call a tool."
)

# Prompt shipped with schema v7–v26 — English-only, no multilingual examples.
# Replaced by v27 with multilingual rules and shorter footprint.
_SYSTEM_PROMPT_V26 = (
    "You are Jarvis, an AI assistant inspired by Iron Man. Calm, dryly "
    "witty, British, address the user as 'sir'. Keep responses concise "
    "\u2014 one or two sentences usually.\n"
    "\n"
    "YOUR JOB:\n"
    "- Answer questions, have conversations, do mental tasks (math, counting, "
    "reasoning, jokes, opinions, advice, storytelling).\n"
    "- When the user asks you to count, do math, tell a story, or have a "
    "conversation: just do it directly in your response.\n"
    "- When the user asks for a specific computer action (open an app, take a "
    "screenshot, check CPU stats, lock the screen, etc.): call the tool \u2014 do not "
    "only say you did it in text.\n"
    "\n"
    "TOOL USAGE:\n"
    "- Only call a tool when the user explicitly asks for that specific "
    "computer action.\n"
    "- For everything else (questions, conversations, counting, math, jokes, "
    "reasoning, advice): answer in words. Do not call any tool.\n"
    "- Read the full utterance and use context to pick the right tool. "
    "Ambiguous 'play X' is common \u2014 decide from clues:\n"
    "  * Music (song, artist, album, genre, 'some jazz', 'that track'): "
    "play_youtube_music with query = song/artist terms only; it finds and "
    "opens the video with autoplay.\n"
    "  * Video game (game title, Steam, 'boot up', 'launch the game'): "
    "launch_steam_game.\n"
    "  * Desktop program (app name, 'open', browser, editor): open_app.\n"
    "  * Whole workspace / dev setup ('open my workspace', 'launch workspace'): "
    "launch_workspace.\n"
    "  * In-depth report with pause/resume ('deep research [topic]'): "
    "deep_research. Quick summary only: research.\n"
    "  * Stronger free-tier pipeline ('enable deep research ultra'): "
    "enable_deep_research_ultra; disable with disable_deep_research_ultra.\n"
    "- Never guess between Steam and YouTube from title overlap alone; use "
    "what the user actually asked for.\n"
    "- If the user's request is unclear, ask for clarification. Don't refuse.\n"
    "- Tools require no admin permissions. Don't hallucinate restrictions."
)


# --- migration -------------------------------------------------------------


# Migration[N] takes a dict at schema_version N and returns a dict at N+1.
# Register here when introducing a new schema version. Template:
#
#     def _migrate_vN_to_vN1(data: dict) -> dict:
#         data["some_new_section"] = {"field": "default"}
#         data.pop("removed_field", None)
#         data["schema_version"] = N + 1
#         return data
#
#     MIGRATIONS[N] = _migrate_vN_to_vN1


def _migrate_v1_to_v2(data: dict) -> dict:
    """Backfill audio device defaults introduced in v2.

    v1 configs stored None for both devices (system default). v2 uses
    TONOR/Pebble Pro as dev-machine defaults. Only fills in None values
    so explicitly-set devices (including null → some-other-device edits)
    are NOT overwritten.
    """
    audio = data.setdefault("audio", {})
    if audio.get("input_device") is None:
        audio["input_device"] = "TONOR"
    if audio.get("output_device") is None:
        audio["output_device"] = "Pebble Pro"
    data["schema_version"] = 2
    return data


def _migrate_v2_to_v3(data: dict) -> dict:
    """Schema v3: default STT model changed from 'base' to 'tiny.en'.

    This migration is intentionally a no-op on existing field values: any
    model_size already in the config (including 'base', 'base.en', 'small',
    etc.) was an explicit user choice and must not be overwritten. Only
    fresh installs (no config file on disk) receive the new 'tiny.en'
    default via STTConfig's field default.
    """
    data["schema_version"] = 3
    return data


def _migrate_v3_to_v4(data: dict) -> dict:
    """Schema v4: replace the over-restrictive v3 system prompt.

    Only replaces the prompt when it exactly matches the v3 default so
    that user-customised prompts are left untouched.
    """
    llm = data.setdefault("llm", {})
    if llm.get("system_prompt") == _SYSTEM_PROMPT_V3:
        llm["system_prompt"] = DEFAULT_SYSTEM_PROMPT
    data["schema_version"] = 4
    return data


def _migrate_v4_to_v5(data: dict) -> dict:
    """Schema v5: introduce general.sleep_confirmation.

    Existing v4 configs simply gain the new field at its default (True).
    GeneralConfig's field default would supply it on validate, but writing
    it explicitly here keeps round-tripped JSON stable and surfaces the
    change in diffs.
    """
    general = data.setdefault("general", {})
    general.setdefault("sleep_confirmation", True)
    data["schema_version"] = 5
    return data


def _migrate_v5_to_v6(data: dict) -> dict:
    """Schema v6: introduce lifecycle section (auto-sleep config).

    Existing v5 configs gain the section with all-default values.
    The four fields are written explicitly so the on-disk JSON matches
    what a fresh install would produce and diffs are stable.
    """
    data.setdefault("lifecycle", {
        "auto_sleep_enabled": False,
        "idle_timeout_minutes": 30,
        "auto_sleep_on_low_battery": False,
        "auto_sleep_on_user_idle": False,
    })
    data["schema_version"] = 6
    return data


def _migrate_v6_to_v7(data: dict) -> dict:
    """Schema v7: fix over-restrictive system prompt + add new LLM/debug fields.

    - Replaces the v6 default system prompt only when it matches exactly.
      User-customised prompts are left untouched.
    - Adds llm.conversation_continuity_seconds (default 60) so on-disk
      JSON stays stable after the round-trip without relying solely on
      pydantic's field default.
    - Adds the debug section (all defaults False).
    """
    llm = data.setdefault("llm", {})
    if llm.get("system_prompt") == _SYSTEM_PROMPT_V6:
        llm["system_prompt"] = DEFAULT_SYSTEM_PROMPT
    llm.setdefault("conversation_continuity_seconds", 60)
    data.setdefault("debug", {"log_wake_during_speaking": False})
    data["schema_version"] = 7
    return data


def _migrate_v7_to_v8(data: dict) -> dict:
    """Schema v8: enable wake-during-speaking debug logging + add weather.

    - Flips debug.log_wake_during_speaking to True on all existing configs
      for a temporary AEC diagnosis window (see pipeline.py / DebugConfig).
      Set back to False in the JSON once the scores have been inspected.
    - Adds the weather section with all defaults (None lat/lon = IP auto-detect,
      unit = fahrenheit).
    """
    debug = data.setdefault("debug", {})
    debug["log_wake_during_speaking"] = True
    data.setdefault("weather", {
        "latitude": None,
        "longitude": None,
        "unit": "fahrenheit",
    })
    data["schema_version"] = 8
    return data


def _migrate_v8_to_v9(data: dict) -> dict:
    """Schema v9: fill Reston, VA defaults for weather coords + disable debug flag.

    - Sets weather.latitude / longitude to Reston, VA (38.9586, -77.357) when
      they are None (i.e. the user never explicitly configured a location). The
      v9→v10 migration replaces these with Oakwood, OH on the next load.
    - Sets debug.log_wake_during_speaking = False. The AEC diagnostic window
      is over: wake-word interrupt was confirmed working on tested hardware
      (2026-05-19). See BUILD.md Phase 6 Task 4.
    """
    weather = data.setdefault("weather", {})
    if weather.get("latitude") is None:
        weather["latitude"] = 38.9586
    if weather.get("longitude") is None:
        weather["longitude"] = -77.3570
    debug = data.setdefault("debug", {})
    debug["log_wake_during_speaking"] = False
    data["schema_version"] = 9
    return data


def _migrate_v9_to_v10(data: dict) -> dict:
    """Schema v10: change default weather location from Reston, VA to Oakwood, OH.

    Replaces coordinates only when they exactly match the Reston defaults
    introduced in v9 (38.9586, -77.357). Any other value — including
    user-configured or IP-detected coordinates — is left untouched.
    """
    weather = data.setdefault("weather", {})
    if weather.get("latitude") == 38.9586 and weather.get("longitude") == -77.3570:
        weather["latitude"] = 39.7187
        weather["longitude"] = -84.1736
    data["schema_version"] = 10
    return data


def _migrate_v10_to_v11(data: dict) -> dict:
    """Schema v11: tighten the output-device default set by the v1→v2 migration.

    The v1→v2 migration wrote audio.output_device = "Pebble Pro", which is
    a broad substring that also matches Windows phantom re-enumerations such
    as "(2- Creative Pebble Pro)" and the Bluetooth headset entry. Replace
    with "Speakers (Creative Pebble Pro)" which uniquely identifies the
    wired speaker and is the AudioConfig field default since v2.

    Only rewrites the exact old default; any other value (including
    user-customised strings) is left untouched.
    """
    audio = data.setdefault("audio", {})
    if audio.get("output_device") == "Pebble Pro":
        audio["output_device"] = "Speakers (Creative Pebble Pro)"
    data["schema_version"] = 11
    return data


def _migrate_v11_to_v12(data: dict) -> dict:
    """Schema v12: reshape MCPServerConfig for the Trayce MCP client.

    - Renames each server's `url_or_command` -> `url`.
    - Drops the unused `tools_enabled` map (per-tool enable/disable now
      flows through tools.enabled with server-prefixed names, e.g.
      `trayce_search_context`).
    - Adds `auth_token_from_file` (default True) and `auth_token`
      (default None) to each server.
    - Ensures a disabled Trayce entry exists so the Settings → Tools
      toggle is present for users upgrading from a config that had no
      MCP servers.
    """
    servers = data.get("mcp_servers")
    if not isinstance(servers, list):
        servers = []
    migrated: list[dict] = []
    for srv in servers:
        if not isinstance(srv, dict):
            continue
        url = srv.pop("url_or_command", None) or srv.get("url") or DEFAULT_TRAYCE_URL
        srv.pop("tools_enabled", None)
        srv["url"] = url
        srv.setdefault("auth_token_from_file", True)
        srv.setdefault("auth_token", None)
        migrated.append(srv)
    if not any(s.get("name") == "trayce" for s in migrated):
        migrated.append({
            "name": "trayce",
            "url": DEFAULT_TRAYCE_URL,
            "enabled": False,
            "auth_token_from_file": True,
            "auth_token": None,
        })
    data["mcp_servers"] = migrated
    data["schema_version"] = 12
    return data


def _migrate_v12_to_v13(data: dict) -> dict:
    """Schema v13: add ui section with research_panel_width.

    Existing v12 configs gain the section at the default width (420 px).
    Users who have resized the panel will have their preference persisted
    by the panel's on_width_change callback going forward; this migration
    just ensures the key exists so model_validate succeeds.
    """
    data.setdefault("ui", {"research_panel_width": 420})
    data["schema_version"] = 13
    return data


def _migrate_v13_to_v14(data: dict) -> dict:
    """Schema v14: add research section with optional api_key."""
    data.setdefault("research", {"api_key": None})
    data["schema_version"] = 14
    return data


def _migrate_v14_to_v15(data: dict) -> dict:
    """Schema v15: customizable workspace apps (open my workspace)."""
    if "workspace" not in data:
        data["workspace"] = {
            "apps": [e.model_dump(mode="json") for e in _default_workspace_apps()],
        }
    data["schema_version"] = 15
    return data


def _migrate_v15_to_v16(data: dict) -> dict:
    """Schema v16: deep-research planner/worker model split + tuning knobs."""
    research = data.setdefault("research", {"api_key": None})
    research.setdefault("planner_model", "")
    research.setdefault("worker_model", "")
    research.setdefault("depth", 5)
    research.setdefault("breadth", 5)
    research.setdefault("fetch_pages", True)
    research.setdefault("max_page_chars", 6000)
    research.setdefault("enable_gap_fill", True)
    data["schema_version"] = 16
    return data


def _migrate_v16_to_v17(data: dict) -> dict:
    """Schema v17: deep research Ultra mode (free-tier retrieval + Groq planner)."""
    research = data.setdefault("research", {"api_key": None})
    research.setdefault("ultra_enabled", False)
    research.setdefault("brave_api_key", "")
    research.setdefault("groq_api_key", "")
    research.setdefault("ultra_planner_model", "groq/llama-3.3-70b-versatile")
    research.setdefault("ultra_gap_fill_iterations", 3)
    data["schema_version"] = 17
    return data


# Coordinates that prior migrations auto-stamped onto user configs. v18
# resets them so the open-source build doesn't leak any maintainer-specific
# location to users who never set a location themselves.
_STAMPED_WEATHER_COORDS: tuple[tuple[float, float], ...] = (
    (38.9586, -77.3570),  # Reston, VA — v8→v9 default fill
    (39.7187, -84.1736),  # Oakwood, OH — v9→v10 default replacement
)


def _migrate_v18_to_v19(data: dict) -> dict:
    """Schema v19: command_palette hotkey + first_run_completed flag.

    Pre-existing installs keep their existing hotkeys; we add the new
    binding with the standard default. The first-run flag is set True
    for existing users so they don't get an unexpected tutorial pop-up
    after the upgrade.
    """
    hotkeys = data.setdefault("hotkeys", {})
    hotkeys.setdefault("command_palette", "ctrl+shift+p")
    general = data.setdefault("general", {})
    general.setdefault("first_run_completed", True)
    data["schema_version"] = 19
    return data


def _migrate_v19_to_v20(data: dict) -> dict:
    """Schema v20: introduce vision section for the see_screen tool.

    Existing v19 configs gain the section with the shipping defaults
    (llava:7b vision model, 1280 px long edge, 512 max tokens, temp 0.2).
    Users who haven't pulled llava:7b will hear a clear "no vision model
    pulled" message when they first say "see my screen" — better than
    autoselecting the user's main text-only model and getting an opaque
    Ollama error mid-tool-call.
    """
    data.setdefault("vision", {
        "model": "llava:7b",
        "max_image_dim": 1280,
        "max_tokens": 512,
        "temperature": 0.2,
    })
    data["schema_version"] = 20
    return data


def _migrate_v20_to_v21(data: dict) -> dict:
    """Schema v21: encrypt secrets at rest (Windows DPAPI).

    Up to v20 the research API keys and MCP auth tokens sat in config.json
    as plaintext with default file permissions. v21 stores them as
    `dpapi:<base64>` (see jarvis/platform/secrets.py).

    This migration encrypts whatever plaintext the user already has, so the
    keys stop being readable as soon as the migrated config is written. It
    is not the only thing keeping old configs working, though —
    `load_config` decrypts after migrating and `decrypt_secrets` passes
    un-prefixed values through unchanged, so a config that is *already* at
    v21 but still holds plaintext (hand-edited, or written on a non-Windows
    host) keeps working and gets encrypted on its next save.

    Off Windows `encrypt_secrets` is a documented no-op passthrough, so this
    migration leaves such configs byte-identical apart from the version bump.
    """
    encrypt_secrets(data)
    data["schema_version"] = 21
    return data


def _migrate_v17_to_v18(data: dict) -> dict:
    """Schema v18: privacy reset for migration-stamped weather coordinates.

    Earlier migrations (v8→v9, v9→v10) auto-filled the weather section
    with the maintainer's location when users had no coordinates set.
    Reset those exact stamped values back to None so the weather tool's
    ipapi.co fallback runs on first use and the user gets *their* local
    forecast. Any other coordinate (including a user who genuinely
    entered one of the stamped values by hand) is left untouched if it
    does not match the exact stamped pair.
    """
    weather = data.setdefault("weather", {})
    lat = weather.get("latitude")
    lon = weather.get("longitude")
    if (lat, lon) in _STAMPED_WEATHER_COORDS:
        weather["latitude"] = None
        weather["longitude"] = None
    data["schema_version"] = 18
    return data


def _migrate_v21_to_v22(data: dict) -> dict:
    """Schema v22: llm.max_tool_iterations for the tool-result feedback loop.

    Existing configs get the shipping default (3), which switches the
    loop on for them. That IS the intended upgrade: before it the model
    never saw what a tool returned, so nothing needing two steps
    ("list my Downloads and tell me which is the invoice") could work
    at all. Users who prefer the previous one-shot behaviour — the
    tool's own output spoken verbatim, exactly one inference per turn —
    set it to 1.
    """
    llm = data.setdefault("llm", {})
    llm.setdefault("max_tool_iterations", 3)
    data["schema_version"] = 22
    return data


def _migrate_v22_to_v23(data: dict) -> dict:
    """Schema v23: memory.embeddings_* settings for FAISS semantic search.

    Adds the ``memory`` config section with embedding defaults. Existing
    configs get embeddings_enabled=True so semantic search activates
    automatically once sentence-transformers is installed.
    """
    mem = data.setdefault("memory", {})
    mem.setdefault("embeddings_enabled", True)
    mem.setdefault("embeddings_model",
                   "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    mem.setdefault("embeddings_device", "cpu")
    mem.setdefault("embeddings_top_k", 20)
    data["schema_version"] = 23
    return data


def _migrate_v23_to_v24(data: dict) -> dict:
    """Schema v24: align STT defaults with setup_jarvis.py downloads.

    setup_jarvis.py downloads the multilingual ``base`` model, but the
    v2→v3 migration changed the default to ``tiny.en`` (English-only).
    That model is never downloaded, so fresh installs crash on first
    launch with ``local_files_only=True``.

    This migration converts ``tiny.en`` → ``base`` and ``en`` → ``auto``
    only when the values match the broken v3 defaults (i.e. the user
    never changed them). Explicitly-chosen models are left untouched.
    """
    stt = data.get("stt", {})
    if stt.get("model_size") == "tiny.en":
        stt["model_size"] = "base"
    if stt.get("language") == "en":
        stt["language"] = "auto"
    data["schema_version"] = 24
    return data


def _migrate_v24_to_v25(data: dict) -> dict:
    """Schema v25: workspace sandbox configuration (Phase 5, T5.1).

    Adds the ``sandbox`` section with a default whitelist of
    ~/Desktop, ~/Documents, ~/Downloads and destructive-action
    confirmation enabled. Existing configs gain the section at
    defaults so existing tools keep working (the whitelist covers
    the most common user directories).
    """
    data.setdefault(
        "sandbox",
        {
            "allowed_paths": ["~/Desktop", "~/Documents", "~/Downloads"],
            "confirm_destructive": True,
        },
    )
    data["schema_version"] = 25
    return data


def _migrate_v25_to_v26(data: dict) -> dict:
    """Schema v26: GPU-accelerated STT with auto-device.

    Adds device="auto" and compute_type="auto" for GPU acceleration.
    Does NOT change model_size — the "small" model may not be downloaded
    yet, and switching to a model that isn't on disk would crash the app
    with local_files_only=True (same bug as the v3 tiny.en incident).
    Users who want "small" can switch via Settings or setup_jarvis.py.
    """
    stt = data.setdefault("stt", {})
    # Add GPU auto-detection and auto compute type for existing configs.
    # model_size is deliberately left untouched (see docstring).
    if "device" not in stt:
        stt["device"] = "auto"
        if stt.get("compute_type", "int8") == "int8":
            stt["compute_type"] = "auto"
    data["schema_version"] = 26
    return data


def _migrate_v26_to_v27(data: dict) -> dict:
    """Schema v27: multilingual system prompt for better tool-calling accuracy.

    Replaces the English-only v7–v26 default prompt with a shorter,
    multilingual version that includes RU/UK/EN examples and explicit
    language-matching rules. Qwen 2.5 7B responds much better to the
    user's language when the system prompt itself demonstrates it.

    Only replaces the prompt when it exactly matches the v26 default so
    that user-customised prompts are left untouched.
    """
    llm = data.setdefault("llm", {})
    if llm.get("system_prompt") == _SYSTEM_PROMPT_V26:
        llm["system_prompt"] = DEFAULT_SYSTEM_PROMPT
    data["schema_version"] = 27
    return data


def _migrate_v27_to_v28(data: dict) -> dict:
    """Schema v28: Game Mode — intelligent VRAM management when games run.

    Adds the ``game_mode`` section with default settings. Existing configs
    get game mode enabled by default; the feature is fully automatic and
    safe (only activates when a known game process is detected).
    """
    data.setdefault("game_mode", {})
    data["schema_version"] = 28
    return data


def _migrate_v28_to_v29(data: dict) -> dict:
    """Schema v29: Smart audio device auto-detection.

    Replaces hard-coded developer-machine device names with None (auto-detect).
    The device_detector module will pick the best physical microphone and
    speaker at first launch, filtering out virtual/loopback devices and
    preferring WASAPI for lower latency.

    Migration rules:
    - input_device == "TONOR" → None  (dev-machine default, useless elsewhere)
    - output_device == "Speakers (Creative Pebble Pro)" → None  (same)
    - Any other explicit value is left untouched (user chose it deliberately).
    """
    audio = data.get("audio")
    if isinstance(audio, dict):
        # Clear hard-coded dev-machine defaults so auto-detect kicks in.
        if audio.get("input_device") == "TONOR":
            audio["input_device"] = None
        if audio.get("output_device") == "Speakers (Creative Pebble Pro)":
            audio["output_device"] = None
    data["schema_version"] = 29
    return data


def _migrate_v29_to_v30(data: dict) -> dict:
    """Schema v30: Sync LLM model default + fix Ukrainian voice quality.

    1. LLM model: setup_jarvis.py downloads qwen3:8b but config defaulted
       to qwen2.5:7b-instruct.  Align the default to what setup actually
       installs so a fresh clone works without manual ollama pull.
       Only migrate if the user still has the old default.

    2. Ukrainian TTS voice: uk_UA-mykyta-medium does not exist on
       HuggingFace (only "high" quality is published).  Rename to
       uk_UA-mykyta-high so the voice actually downloads and loads.
    """
    llm = data.get("llm")
    if isinstance(llm, dict):
        if llm.get("model") == "qwen2.5:7b-instruct":
            llm["model"] = "qwen3:8b"

    tts = data.get("tts")
    if isinstance(tts, dict):
        if tts.get("voices_uk") == "uk_UA-mykyta-medium":
            tts["voices_uk"] = "uk_UA-mykyta-high"
        if tts.get("voice") == "uk_UA-mykyta-medium":
            tts["voice"] = "uk_UA-mykyta-high"
    data["schema_version"] = 30
    return data


def _migrate_v30_to_v31(data: dict) -> dict:
    """Schema v31: Add security section (encrypted storage at rest).

    Adds ``security`` config section with ``encrypt_memory`` and
    ``encrypt_json_files`` both defaulting to True.  Existing users
    get encryption enabled automatically — legacy plaintext data is
    migrated transparently on first read/write by EncryptedJsonStore
    and the column-level encryption helpers in encrypted_db.py.
    """
    if "security" not in data:
        data["security"] = {"encrypt_memory": True, "encrypt_json_files": True}
    data["schema_version"] = 31
    return data


def _migrate_v31_to_v32(data: dict) -> dict:
    """Schema v32: Coding Agent — universal LLM provider for code generation.

    Adds the ``coding`` section with sensible defaults.  The ``provider``
    defaults to ``"main"`` (reuse the main Ollama model) so the feature
    is inert until the user explicitly configures a coding model in
    Settings → Coding.  The ``api_key`` field is covered by
    ``_SECRET_FIELD_PATHS`` and encrypted at rest like the research keys.
    """
    if "coding" not in data:
        data["coding"] = {
            "provider": "main",
            "ollama_model": "qwen2.5-coder:7b",
            "api_model": "",
            "api_key": "",
            "api_base_url": "",
            "max_iterations": 10,
            "auto_commit": False,
            "default_workspace": "",
        }
    data["schema_version"] = 32
    return data


def _migrate_v32_to_v33(data: dict) -> dict:
    """Schema v33: Universal Model Provider for LLM and Vision.

    Adds provider/api fields to the ``llm`` and ``vision`` sections so
    users can choose between Ollama (local, default) and cloud APIs
    (OpenAI, Anthropic, Groq) for the main conversational model and
    the vision model.

    Existing configs get provider="ollama" and empty API fields, so
    behaviour is identical to pre-v33 — purely additive, no breaking
    change.
    """
    llm = data.setdefault("llm", {})
    llm.setdefault("provider", "ollama")
    llm.setdefault("api_model", "")
    llm.setdefault("api_key", "")
    llm.setdefault("api_base_url", "")

    vision = data.setdefault("vision", {})
    vision.setdefault("provider", "ollama")
    vision.setdefault("api_model", "")
    vision.setdefault("api_key", "")
    vision.setdefault("api_base_url", "")

    data["schema_version"] = 33
    return data


def _migrate_v33_to_v34(data: dict) -> dict:
    """Schema v34: Image Generation — multi-provider abstraction.

    Adds the ``image_gen`` section with all defaults.  The ``provider``
    defaults to ``"disabled"`` so the feature is inert until the user
    explicitly configures a provider in Settings → Image Gen.  The
    ``api_key`` field is covered by ``_SECRET_FIELD_PATHS`` and encrypted
    at rest like the other API keys.

    Local providers (ComfyUI, A1111) have ``nsfw_filter`` OFF by default
    — the user controls what models they install, so no content filtering
    from our side unless they opt in.
    """
    if "image_gen" not in data:
        data["image_gen"] = {
            "provider": "disabled",
            "local_endpoint": "http://127.0.0.1:8188",
            "local_model": "",
            "nsfw_filter": False,
            "api_key": "",
            "api_model": "",
            "width": 1024,
            "height": 1024,
            "steps": 20,
            "cfg_scale": 7.0,
            "save_directory": "",
            "auto_vram_swap": True,
        }
    data["schema_version"] = 34
    return data

def _migrate_v34_to_v35(data: dict) -> dict:
    """Schema v35: Image Model Isolation — separate model for image prompts.

    Adds ``image_model_provider``, ``image_model_name``,
    ``image_model_api_key``, and ``image_model_api_base_url`` to the
    ``image_gen`` section.  When ``image_model_provider`` is anything other
    than ``"main"``, image prompt engineering is handled by a dedicated
    model and the main LLM never sees the prompt (full isolation).

    Default ``"main"`` preserves current behaviour: the main LLM handles
    prompt enhancement (when enhance_mode="llm"), so this is purely
    additive with no breaking change.

    ``image_model_api_key`` is covered by ``_SECRET_FIELD_PATHS`` and
    encrypted at rest.
    """
    ig = data.setdefault("image_gen", {})
    ig.setdefault("image_model_provider", "main")
    ig.setdefault("image_model_name", "")
    ig.setdefault("image_model_api_key", "")
    ig.setdefault("image_model_api_base_url", "")
    data["schema_version"] = 35
    return data


def _migrate_v35_to_v36(data: dict) -> dict:
    """Schema v36: Autonomous Agent settings.

    Adds the ``agent`` section with settings for the general-purpose
    plan-and-execute autonomous agent (Phase 11).  The agent chains
    tools to accomplish complex multi-step goals.

    Fields:
    - ``enabled`` (bool, default True)
    - ``max_iterations`` (int, default 20)
    - ``max_time_seconds`` (int, default 300)
    - ``confirm_destructive`` (bool, default True)
    - ``model_provider`` (str, default "main")
    - ``verbose_reasoning`` (bool, default False)
    """
    ag = data.setdefault("agent", {})
    ag.setdefault("enabled", True)
    ag.setdefault("max_iterations", 20)
    ag.setdefault("max_time_seconds", 300)
    ag.setdefault("confirm_destructive", True)
    ag.setdefault("model_provider", "main")
    ag.setdefault("verbose_reasoning", False)
    data["schema_version"] = 36
    return data


def _migrate_v36_to_v37(data: dict) -> dict:
    """Schema v37: optional self-evaluation (auto-critic) settings.

    Adds the ``self_evaluation`` section. Disabled by default — Jarvis
    stays fully local unless the user opts in and supplies an external
    critic API key. See SelfEvaluationConfig.
    """
    se = data.setdefault("self_evaluation", {})
    se.setdefault("enabled", False)
    se.setdefault("provider", "groq")
    se.setdefault("api_key", "")
    se.setdefault("api_model", "llama-3.3-70b-versatile")
    se.setdefault("api_base_url", "")
    se.setdefault("interval_hours", 24.0)
    se.setdefault("max_traces_per_run", 20)
    se.setdefault("save_insights_to_memory", False)
    data["schema_version"] = 37
    return data

MIGRATIONS: dict[int, Migration] = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
    4: _migrate_v4_to_v5,
    5: _migrate_v5_to_v6,
    6: _migrate_v6_to_v7,
    7: _migrate_v7_to_v8,
    8: _migrate_v8_to_v9,
    9: _migrate_v9_to_v10,
    10: _migrate_v10_to_v11,
    11: _migrate_v11_to_v12,
    12: _migrate_v12_to_v13,
    13: _migrate_v13_to_v14,
    14: _migrate_v14_to_v15,
    15: _migrate_v15_to_v16,
    16: _migrate_v16_to_v17,
    17: _migrate_v17_to_v18,
    18: _migrate_v18_to_v19,
    19: _migrate_v19_to_v20,
    20: _migrate_v20_to_v21,
    21: _migrate_v21_to_v22,
    22: _migrate_v22_to_v23,
    23: _migrate_v23_to_v24,
    24: _migrate_v24_to_v25,
    25: _migrate_v25_to_v26,
    26: _migrate_v26_to_v27,
    27: _migrate_v27_to_v28,
    28: _migrate_v28_to_v29,
    29: _migrate_v29_to_v30,
    30: _migrate_v30_to_v31,
    31: _migrate_v31_to_v32,
    32: _migrate_v32_to_v33,
    33: _migrate_v33_to_v34,
    34: _migrate_v34_to_v35,
    35: _migrate_v35_to_v36,
    36: _migrate_v36_to_v37,
}


class ConfigMigrationError(ValueError):
    """Raised when migration cannot proceed (missing version, unknown future
    version, missing migration, or a migration that didn't bump the version)."""


def migrate(data: dict) -> dict:
    """Apply registered migrations in sequence until data is at
    CURRENT_SCHEMA_VERSION. Returns the same dict object (possibly mutated)."""
    if "schema_version" not in data:
        raise ConfigMigrationError("config missing schema_version")
    version = data["schema_version"]
    if not isinstance(version, int):
        raise ConfigMigrationError(f"schema_version must be int, got {type(version).__name__}")
    if version > CURRENT_SCHEMA_VERSION:
        raise ConfigMigrationError(
            f"config schema_version {version} is newer than supported "
            f"{CURRENT_SCHEMA_VERSION}; downgrade is not supported"
        )
    while version < CURRENT_SCHEMA_VERSION:
        migration = MIGRATIONS.get(version)
        if migration is None:
            raise ConfigMigrationError(
                f"no migration registered for schema_version {version}"
            )
        data = migration(data)
        new_version = data.get("schema_version")
        if new_version != version + 1:
            raise ConfigMigrationError(
                f"migration {version} -> {version + 1} produced "
                f"schema_version={new_version!r}"
            )
        # Past that check new_version *is* version + 1, so step the counter
        # rather than reading an untyped value back out of the dict — a
        # migration cannot smuggle a non-int version through the loop.
        version += 1
    return data



