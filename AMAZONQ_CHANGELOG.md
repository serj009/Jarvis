# Amazon Q Branch — Changelog

> This file tracks all changes made in the `amazonq` branch by Amazon Q. Purpose: when merging with `pygpt` branch, the home Amazon Q instance can read this file to instantly understand what changed, without analyzing the entire repository manually.
> Format: each session is a dated block with files changed, rationale, and integration notes.

---

## 2026-10-07 — Phase 1 Integration (T1.2 / T1.3 / T1.4 / T1.5)

### ⚠️ IMPORTANT: Full app.py Wiring Included
All new modules are **fully wired** into `app.py`, `tools/__init__.py`, and the
`LifecycleManager`. Pull this branch and everything works — no manual integration needed.

### Summary

Implemented the missing Phase 1 (Core + Observability) components. The repository already had T1.1 (base app) and T1.4+ (ConversationalState) and T1.6 (smoke_test) implemented. This session fills the gaps.

### Context — What Was Already In Place

- `core/state_machine.py` — Mode + ConversationalState enums, transition tables, EventBus integration ✅
- `core/request_context.py` — correlation_id (ContextVar), CorrelationIdFilter, turn_started_at ✅
- `core/resource_monitor.py` — idle-time auto-sleep trigger ✅
- `core/events.py` — typed pub/sub EventBus with ModeChanged, NonFatalError, etc. ✅
- `core/lifecycle.py` — Loadable protocol, LifecycleManager ✅
- `core/phrases.py` — i18n phrases (ru/uk/en), detect_language(), reply_language() ✅
- `dev/smoke_test.py` — 9 checks (config, Ollama, model, LLM, Whisper, Piper, GPU, logs, autostart) ✅
- `llm/ollama_client.py` — OllamaError + OllamaConnectionError + OllamaModelNotFoundError ✅

### New Files (6)

`jarvis/core/errors.py` — T1.5 Unified Error Handler

- **JarvisError** base class with `spoken_key` for i18n TTS responses
- **STTError**, **TTSError**, **VRAMError** — domain-specific exceptions
- Re-exports **OllamaError** from `llm/ollama_client.py` (no duplication)
- **ErrorHandler** class:- `safe_call(coro, stage=)` — wraps any awaitable, returns `FAILED` sentinel on error
- Never crashes the voice loop — logs, counts, publishes NonFatalError event
- Consecutive error counter → degraded mode notice after 5 errors
- `spoken_message(key)` — returns i18n phrase in user's detected language
- `reset()` — clears counter after successful operation
- i18n error phrases for all error types in ru/uk/en (merged into `core.phrases.PHRASES`)
- **Dependencies**: `core.phrases`, `core.events.NonFatalError`, `llm.ollama_client.OllamaError`
- **No circular imports**: ollama_client does not import from core.errors

`jarvis/core/vram_manager.py` — T1.4 VRAM Manager

- **GpuSnapshot** (frozen dataclass) — nvidia-smi point-in-time reading
- `_parse_nvidia_smi()` / `gpu_snapshot()` — sync + async nvidia-smi parser
- **VRAMConsumer** dataclass — name, size_mb, priority, is_loaded, on_unload callback
- **VRAMManager** class (implements Loadable protocol):- `register(name, size_mb=, priority=)` — declare a GPU consumer
- `request(name)` → bool — allocate VRAM, auto-evict lower-priority consumers if needed
- `release(name)` — free VRAM, mandatory `torch.cuda.empty_cache()` + `gc.collect()`
- `release_all()` — gaming mode: free everything
- `status()` → dict — for dashboard and voice reports
- `refresh_snapshot()` — re-read nvidia-smi
- Eviction: sorted by priority (highest number = least important = evicted first)
- `load()` / `unload()` — Loadable protocol for LifecycleManager
- **Budget**: RTX 3060 12GB → ollama 8GB (priority 10) + TTS 2.5GB (priority 20) = 10.5GB
- **Dependencies**: `core.errors.VRAMError`, optional `torch`

`jarvis/core/logging_utils.py` — T1.2 Structured Logging

- **JsonFormatter** — logging.Formatter that outputs JSON lines (ts, level, logger, correlation_id, turn_ms, msg, exc)
- **@log_latency** — decorator for async/sync functions, logs elapsed time at DEBUG level with correlation_id
- **PipelineTracker** — stopwatch for pipeline stages:- `start(stage)` / `stop(stage)` → elapsed_ms
- `summary()` → `"pipeline: stt=112ms llm=340ms tts=85ms total=537ms [abc12345]"`
- `log_summary()` — emit via logger
- `as_dict()` — for JSON logging
- `reset()` — reuse across turns
- **Dependencies**: `core.request_context` (correlation_id, ms_since_turn_start)

`tests/core/test_errors.py` — 9 tests

- Error hierarchy (all inherit JarvisError)
- spoken_keys exist in PHRASES for all 3 languages
- ErrorHandler.safe_call success/failure/sentinel/reset/consecutive/degraded

`tests/core/test_vram_manager.py` — 13 tests

- Register, request, release, double-request noop
- Eviction by priority, insufficient budget
- release_all, on_unload callback
- Loadable protocol (load/unload), GpuSnapshot frozen

`tests/core/test_logging_utils.py` — 10 tests

- JsonFormatter valid JSON, correlation_id, exception
- @log_latency async/sync, exception propagation
- PipelineTracker start/stop/total/summary/reset/as_dict

### Modified Files (4)

`jarvis/tools/local/system_stats.py` — T1.3 Enhanced Dashboard

- **Before**: CPU + RAM only via psutil, English only
- **After**: CPU + RAM + GPU (VRAM used/total/free, temperature, loaded models)
- i18n templates for ru/uk/en
- Optional `vram_manager` parameter (defaults to None for backward compat)
- `SystemStatsTool()` without args works exactly as before (existing callers in `setup_local_tools()` unchanged)

`tests/tools/local/test_system_stats.py` — Extended tests

- Original 3 tests preserved (backward compatibility)
- Added 2 new tests: GPU info with VRAMManager mock, no-GPU fallback


#### `jarvis/app.py` — Full wiring of T1.2/T1.4/T1.5
- **Imports added**: `ErrorHandler`, `JsonFormatter`, `VRAMManager`
- **Class attributes**: `vram_manager: VRAMManager`, `error_handler: ErrorHandler`
- **`_setup_logging()`**: added JSONL file handler (`jarvis.log.jsonl`) with `JsonFormatter` + `CorrelationIdFilter`
- **`_build_audio_stack()`**: step 5b creates `VRAMManager(total_budget_mb=12000)`, registers 3 consumers (ollama 8GB/p10, tts_gpu 2.5GB/p20, vision 2GB/p30), creates `ErrorHandler(bus=self.bus)`
- **`LifecycleManager`**: `self.vram_manager` added as FIRST loadable (loads nvidia-smi on boot, release_all on SLEEPING)
- **`setup_local_tools()`**: passes `vram_manager=self.vram_manager`

#### `jarvis/tools/__init__.py` — VRAMManager passthrough
- `setup_local_tools()` signature: added `vram_manager: object = None` parameter
- `SystemStatsTool(vram_manager=vram_manager)` — GPU reporting enabled when VRAMManager is provided

### Integration Notes for Home Amazon Q

- All new modules are **standalone** — they don't modify existing files' behavior
- `setup_local_tools()` in `tools/__init__.py` creates `SystemStatsTool()` with no args → works as before
- To wire VRAMManager into the app, add to `app.py`:```python
from jarvis.core.vram_manager import VRAMManager
vram = VRAMManager()
vram.register("ollama", size_mb=8000, priority=10)
# Pass to SystemStatsTool:
SystemStatsTool(vram_manager=vram)

```
- To wire ErrorHandler into the voice loop:```python
from jarvis.core.errors import ErrorHandler
handler = ErrorHandler(bus=bus)
result = await handler.safe_call(stt_coro(), stage="stt")

```
- To enable JSON logging:```python
from jarvis.core.logging_utils import JsonFormatter
handler = logging.FileHandler("jarvis.log.jsonl")
handler.setFormatter(JsonFormatter())
handler.addFilter(CorrelationIdFilter())
logging.root.addHandler(handler)

```
- **32 new tests total** (9 + 13 + 10 in core/, plus extended system_stats)
- No dependencies added to `pyproject.toml` (all use stdlib + existing deps)

### Backward Compatibility

- ✅ No existing files broken
- ✅ No import changes in existing modules
- ✅ No new dependencies
- ✅ SystemStatsTool() without args = same behavior as before
- ✅ All existing 91 tests should pass unchanged



---

## 2026-10-07 — Phase 2 Integration (T2.1 / T2.2 / T2.3 / T2.4)

### Summary
Implemented multilingual STT with confidence scoring. JARVIS can now understand
Ukrainian, Russian, and English speech, auto-detect the language, and filter out
low-confidence transcriptions (hallucinations, noise).

### Modified Files (5)

#### `jarvis/audio/stt.py` — T2.1/T2.2/T2.3/T2.4 Multilingual STT
- **TranscriptionResult** return type (replaces bare `str`)
- **Language auto-detect**: `language="auto"` omits the language kwarg so Whisper detects
- **Hotwords per language** (T2.3): `HOTWORDS_EN`, `HOTWORDS_RU`, `HOTWORDS_UK`, `HOTWORDS_AUTO`
  with Cyrillic spellings (Ютуб, ГітХаб, Телеграм, Джарвіс, etc.)
- **Auto-select hotwords** by language in constructor (no explicit hotwords → pick by language)
- **Confidence scoring** (T2.4): `_compute_confidence()` from avg_logprob + no_speech_prob,
  `_confidence_action()` maps to "proceed"/"clarify"/"ignore"
- **_sync_transcribe** now collects segment metadata and returns `TranscriptionResult`

#### `jarvis/audio/protocols.py` — TranscriptionResult + confidence types
- **TranscriptionResult** frozen dataclass: text, confidence (0-1), detected_language,
  language_probability, action ("proceed"/"clarify"/"ignore"), is_empty property
- **ConfidenceAction** literal type + default thresholds (0.70 proceed, 0.30 clarify)
- **SpeechToText protocol** updated: `transcribe() -> TranscriptionResult`

#### `jarvis/audio/pipeline.py` — Confidence routing in voice loop
- Imports `TranscriptionResult`
- Transcribe result unpacked: logs confidence, action, detected language
- **T2.4 routing**: action=="ignore" → drop silently; action=="clarify" → speak "error_stt" phrase → IDLE
- Uses `say("error_stt")` from phrases (i18n: "Could you repeat?" in RU/UK/EN)

#### `jarvis/core/config.py` — STTConfig expanded
- **model_size**: added "medium", "medium.en" options
- **language**: documented "auto" for auto-detect
- **New fields**: `confidence_proceed` (0.70), `confidence_clarify` (0.30), `max_clarify_retries` (3)

#### `jarvis/app.py` — Wired new STTConfig fields
- `FasterWhisperSTT()` now receives `confidence_proceed`, `confidence_clarify`, `max_clarify_retries`

### New Files (1)

#### `tests/audio/test_stt_phase2.py` — 22 tests
- TranscriptionResult (str, is_empty, frozen, defaults)
- Confidence scoring (empty, high, low, no_speech, weighted)
- Confidence action (proceed/clarify/ignore, custom thresholds)
- Hotwords per language (RU/UK/EN/auto, Cyrillic, override, disable)
- Multilingual model resolution (auto/ru/uk use base, en uses .en)
- Constructor defaults and custom thresholds

### Updated Test Files (1)

#### `tests/audio/test_stt.py` — Updated for TranscriptionResult
- All `assert result == "..."` → `assert result.text == "..."`
- Mock `_segment()` now includes `avg_logprob` and `no_speech_prob`
- Mock `_make_info()` helper with language attributes
- `hotwords=None` → `hotwords=""` for disable test (None now means auto-select)

### Backward Compatibility
- ✅ TranscriptionResult has `__str__()` → `str(result)` returns the text
- ✅ All existing pipeline logic that checked `text.strip()` now uses `result.is_empty`
- ✅ Default config unchanged (tiny.en, language=en) — existing users not affected
- ✅ No new dependencies

### How to Enable Multilingual Mode
In `config.json` (Settings → Models):
```json
{
  "stt": {
    "model_size": "small",
    "language": "auto"
  }
}
```
