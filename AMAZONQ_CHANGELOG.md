# Amazon Q Branch — Changelog

> This file tracks all changes made in the `amazonq` branch by Amazon Q. Purpose: when merging with `pygpt` branch, the home Amazon Q instance can read this file to instantly understand what changed, without analyzing the entire repository manually.
> Format: each session is a dated block with files changed, rationale, and integration notes.

---

## 2026-10-09 — AutoClassifier + RelationStore + EmbeddingCache Integration

### Summary
Integrated three Kit components into the JARVIS memory subsystem:
1. **AutoClassifier** — LLM-based entity classification for automatic shard routing
2. **RelationStore** — graph of typed relations between facts (belongs_to, related_to, etc.)
3. **EmbeddingCache** — LRU cache for embedding vectors (avoids recomputing same text)

All three are fully local (no internet), backward compatible, and degrade
gracefully when dependencies are unavailable.

### New Files (3)

#### `jarvis/memory/auto_classifier.py` — AutoClassifier
- **AutoClassifier** class: classifies unknown entities via local Ollama LLM
- Uses httpx to call Ollama `/api/generate` endpoint (non-streaming, fast)
- `ENTITY_TYPE_TO_SHARD` mapping: game→games, movie→tv_media, food→cooking, etc.
- `ClassificationResult` dataclass (frozen=True, slots=True)
- LRU cache (OrderedDict, max 5000) with JSON persistence to disk
- `classify(entity, context)` → cache → LLM → unknown fallback
- No web_fn — project is fully local, no internet access
- Graceful: if Ollama unavailable, returns shard="general"

#### `jarvis/memory/relations.py` — RelationStore
- **RelationStore** class: typed graph of relations between facts
- SQLite `relations` table in the same DB as facts (TEXT foreign keys)
- Relation types: belongs_to, related_to, depends_on, contradicts, part_of
- `add_relation(from_id, to_id, type, strength)` → returns relation ID
- `get_related(fact_id, type=None, depth=1)` → BFS graph traversal
- `find_all_related_to_tag(tag)` → discovers all facts connected to a tag
- `get_relations_for_fact(fact_id)` → all direct relations (both directions)
- Follows repo pattern: each method creates its own sqlite3.Connection
- WAL mode, row_factory=sqlite3.Row for dict-like access

#### `jarvis/memory/embedding_cache.py` — EmbeddingCache
- **EmbeddingCache** class: LRU cache for embedding vectors
- OrderedDict with MD5 keys (normalized, case-insensitive)
- `get(text)` / `put(text, vector)` / `get_or_compute(text, fn)`
- `get_stats()` → size, hits, misses, hit_rate
- Default max_size=2000 (sufficient for typical search sessions)

### New Test Files (3)

#### `tests/memory/test_auto_classifier.py` — 10 tests
- ClassificationResult frozen/to_dict/from_dict tests
- Entity type mapping verification
- LLM classification with mocked httpx (success, cache hit, case-insensitive)
- Graceful degradation (LLM failure, no httpx)
- Cache persistence to JSON, LRU eviction, stats

#### `tests/memory/test_relations.py` — 14 tests
- Schema creation, idempotency, index verification
- Relation CRUD: add, remove, count, unknown types
- Graph traversal: depth-1, depth-2, filtered by type, empty, circular
- Tag discovery: find_all_related_to_tag with/without relations
- get_relations_for_fact both directions
- Integration: MemoryStore.open() creates relations table

#### `tests/memory/test_embedding_cache.py` — 14 tests
- Basic put/get, miss returns None
- Case-insensitive keys, whitespace normalization
- LRU eviction, access refreshes position
- Hit rate tracking, zero stats
- get_or_compute (miss computes, hit skips)
- clear(), size property, large vectors (384-dim), overwrite

### Modified Files (7)

#### `jarvis/memory/store.py` — Relations table in schema
- Added `_RELATIONS_SCHEMA` SQL (CREATE TABLE relations + indexes)
- `_sync_open()` now executes `_RELATIONS_SCHEMA` alongside facts/fact_tags
- New `update_category()` async method for AutoClassifier integration
- Existing facts/fact_tags schema UNCHANGED — only additions

#### `jarvis/memory/auto_tagger.py` — Classifier integration hooks
- Added `set_classifier(classifier)` method to attach AutoClassifier
- Added `has_classifier` / `classifier` properties
- AutoTagger remains pure regex (fast, sync) — the classifier is stored
  as a reference for the caller (MemoryManager) to use asynchronously
- Existing analyze() logic UNCHANGED — backward compatible

#### `jarvis/memory/smart_search.py` — Relation graph enrichment
- Constructor accepts optional `relation_store` parameter
- `set_relation_store(store)` for dynamic attachment
- `enrich_with_relations(fact_ids)` → expands set via 1-hop graph traversal
- Graceful: if RelationStore unavailable, returns original set
- Existing scoring weights and rank() logic UNCHANGED

#### `jarvis/memory/memory_manager.py` — New subsystem wiring
- Constructor accepts `ollama_endpoint` and `ollama_model` for classifier
- Initializes AutoClassifier with cache_path in data_root
- Initializes RelationStore with same db_path as MemoryStore
- Initializes EmbeddingCache (max_size=2000)
- Wires classifier into AutoTagger via set_classifier()
- Wires RelationStore into SmartSearch
- `open()`: initializes RelationStore schema
- `remember()`: async LLM classification for "general" category facts
- `search()`: enriches results with related facts from graph
- `add_relation()` / `find_related()` convenience methods
- `stats()`: includes classifier, relations, and embedding cache stats

#### `jarvis/memory/embeddings.py` — EmbeddingCache integration
- Creates EmbeddingCache(max_size=2000) in __init__
- `embedding_cache` property for external access/stats
- `_encode()` checks cache before computing (cache hit ~0.01ms vs ~50ms)
- `_raw_encode()` extracted for batch operations and cache bypass
- `unload()` clears the embedding cache

#### `jarvis/memory/__init__.py` — Updated docstring
- Added auto_classifier, embedding_cache, and relations sections

#### `AMAZONQ_CHANGELOG.md` — This entry

---

## 2026-10-09 — FAISS + Embeddings: Semantic Memory Search (T4.1/T4.2)

### Summary
Added FAISS-backed semantic search to the memory subsystem. Facts stored in
memory are now indexed with sentence-transformers embeddings (paraphrase-
multilingual-MiniLM-L12-v2, 384-dim, 50+ languages). A query like "что я
сохранял про машины" now finds the fact "купил новый автомобиль" — even
though the words don't overlap. Cross-lingual too: "cars" finds "автомобиль".

The system is fully local (model downloads once, works offline), optional
(graceful degradation without sentence-transformers), and backward
compatible (existing keyword-only search is unchanged when embeddings are
unavailable).

### New Files (2)

#### `jarvis/memory/embeddings.py` — EmbeddingIndex
- **EmbeddingIndex** class implementing the Loadable protocol
- Model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  - 384-dimensional embeddings, 50+ languages (RU/UK/EN)
  - CPU-friendly: ~50-100ms per query
  - ~420 MB download, cached locally after first use
- FAISS IndexFlatIP (inner product on L2-normalized vectors = cosine similarity)
- API: `add(fact_id, text)`, `remove(fact_id)`, `search(query, top_k)`,
  `rebuild(facts)`, `save()`, `load()`, `unload()`
- Index persisted to `data/embeddings/faiss.index` + `id_map.json`
- Lazy model loading: sentence-transformers imported only on first `load()`
- Offline-first: `local_files_only=True`, falls back to download if not cached
- Tombstone-based removal with compaction on save

#### `tests/memory/test_embeddings.py` — Comprehensive test suite
- **TestEmbeddingIndexMocked**: Unit tests with mocked model (fast, no downloads)
  - add, search, remove, rebuild, save/load, count, empty index
- **TestSmartSearchWithEmbeddings**: Hybrid scoring integration tests
  - Semantic signal included when embeddings available
  - Keyword-only fallback when not available
  - Graceful degradation on search error
  - Backward compatibility with existing tests
- **TestEmbeddingIndexReal** (marked `@pytest.mark.slow`):
  - Semantic synonym: "машины" → "автомобиль"
  - Cross-lingual: "cars" → "автомобіль"
  - Ukrainian: "машини" → "автомобіль"
  - Save/load round-trip quality preservation
  - 384-dim verification

### Modified Files (8)

#### `jarvis/memory/smart_search.py` — 6-signal hybrid scoring
- Added **semantic similarity** as the 6th scoring signal
- Two weight profiles:
  - **Keyword-only** (no embeddings): unchanged weights (0.40/0.20/0.20/0.10/0.10)
  - **Hybrid** (with embeddings): semantic=0.35, text=0.20, freq=0.15,
    recency=0.15, lang=0.08, cat=0.07
- `SmartSearch.__init__` accepts optional `embedding_index` parameter
- `has_embeddings` property for checking availability
- `ScoredFact` dataclass extended with `semantic_score` field
- Graceful: if embedding search throws, falls back to keyword-only

#### `jarvis/memory/store.py` — Embedding sync on CRUD
- `set_embedding_index(index)` method to attach/detach EmbeddingIndex
- `add()` now calls `_maybe_embed()` to index new facts
- `delete()` now calls `_maybe_remove_embedding()` to remove from index
- `update()` now re-embeds the updated text
- `all_facts_for_embedding()` method for full rebuild on first load

#### `jarvis/memory/memory_manager.py` — EmbeddingIndex lifecycle
- Constructor accepts optional `embedding_index` parameter
- `open()` loads embedding index; rebuilds if empty but store has facts
- `close()` saves and unloads embedding index
- `search()` merges keyword + semantic results before ranking
  (facts found only by semantics are fetched and included)
- `stats()` includes embedding count and loaded status

#### `jarvis/core/config.py` — MemoryConfig section
- New `MemoryConfig` pydantic model:
  - `embeddings_enabled: bool = True`
  - `embeddings_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"`
  - `embeddings_device: str = "cpu"`
  - `embeddings_top_k: int = 20`
- Added `memory: MemoryConfig` field to `JarvisConfig`
- Schema version bumped 22 → 23
- Migration `_migrate_v22_to_v23` adds `memory` section with defaults

#### `jarvis/app.py` — Wiring EmbeddingIndex
- Imports `EmbeddingIndex` from `jarvis.memory.embeddings`
- In `_build_audio_stack()` (step 5c): creates `EmbeddingIndex` based
  on `cfg.memory` settings, attaches to `MemoryStore`
- Wrapped in try/except: if sentence-transformers not installed,
  `embedding_index` stays None (graceful degradation)

#### `setup_jarvis.py` — Step 9: Embedding model download
- Added `step_embeddings()` as step [9/9]
- Downloads the sentence-transformers model on first run
- Verifies encoding works (sanity check)
- Status: WARN (not FAIL) if not installed — embeddings are optional
- Updated all step numbering from /8 to /9

#### `pyproject.toml` — Dependencies
- Added `[project.optional-dependencies] embeddings` group:
  - `sentence-transformers>=2.6,<4`
  - `faiss-cpu>=1.7,<2`
- Also added to `[dev]` group for CI
- Added `slow` pytest marker for model-dependent tests
- Default pytest `addopts` now excludes both `manual` and `slow` markers

#### `jarvis/memory/__init__.py` — Updated module docstring
- Added `embeddings` module to the subsystem listing
- Changed SmartSearch description from "5-signal" to "6-signal"

### Architecture Notes
- **Fully local**: Model downloaded once from HuggingFace, cached in
  `~/.cache/huggingface/`. After that, `HF_HUB_OFFLINE=1` ensures no
  network calls.
- **Graceful degradation**: Every consumer checks
  `embedding_index is not None and embedding_index.is_loaded` before
  use. Without sentence-transformers, the entire pipeline works
  exactly as before (keyword-only SmartSearch, 5-signal scoring).
- **Lazy loading**: The heavy `sentence_transformers` import happens
  only inside `EmbeddingIndex._sync_load()`, not at module level.
  This keeps startup fast when embeddings are disabled.
- **Hybrid search strategy**: MemoryManager first runs FTS5 keyword
  search, then also runs FAISS semantic search. Facts found only by
  semantics (no keyword match) are fetched from SQLite and added to
  the candidate pool. SmartSearch then scores all candidates with
  6 signals and returns the top results.
- **FAISS IndexFlatIP**: Inner product on L2-normalized vectors equals
  cosine similarity. Simple, exact, no training needed. For <100k
  facts this is perfectly fast.
- **Tombstone removal**: FAISS doesn't support in-place delete.
  `remove()` marks positions as deleted; `save()` compacts by
  reconstructing live vectors into a new index.

### How to use
```powershell
# Install embedding dependencies
pip install -e ".[embeddings]"

# Download the model (first time only, ~420 MB)
python setup_jarvis.py

# Run tests (fast, mocked)
pytest tests/memory/test_embeddings.py

# Run tests including real model tests
pytest -m slow tests/memory/test_embeddings.py
```

### Backward Compatibility
- ✅ All existing SmartSearch tests pass unchanged
- ✅ MemoryStore API unchanged (new methods are additive)
- ✅ MemoryManager API unchanged (embedding_index is optional kwarg)
- ✅ Without sentence-transformers installed, behavior is identical to before
- ✅ Config migration handles upgrade from schema v22 → v23

---

## 2026-10-09 — Setup Script + Voice Expansion + AEC Dependency

### Summary
Created a comprehensive first-run setup script (`setup_jarvis.py`) that
automates the entire JARVIS environment bootstrap: venv creation,
dependency installation, ML model downloads (Piper voices, Whisper,
Silero VAD, openWakeWord), Ollama health check, and smoke test.
Also restored alternative (female) Piper voices for all three languages
and added `pywebrtc-audio` as an optional dependency for AEC mode.

### New Files (1)

#### `setup_jarvis.py` — First-run setup script
- **8-step automated setup** with Russian-language progress output
- Step 1: Python version check (>= 3.10)
- Step 2: Creates `.venv/` virtual environment if not present
- Step 3: Installs dependencies via `pip install -e .`
- Step 4: Downloads all 6 Piper voices (3 default + 3 alternative)
  with download progress bars
- Step 5: Downloads faster-whisper model (configurable: tiny/base/small)
- Step 6: Verifies Silero VAD model presence
- Step 7: Downloads openWakeWord models
- Step 8: Checks Ollama availability and installed models
- Final smoke test run via `jarvis/dev/smoke_test.py`
- **Idempotent**: skips already-downloaded assets, safe to rerun
- **Offline capable**: works without internet when everything cached
- **Fault-tolerant**: never crashes on a single step failure, reports
  all issues and continues
- **Windows-native**: handles UTF-8 console, PowerShell/cmd compatible
- CLI flags: `--whisper-model`, `--skip-smoke-test`

### Modified Files (4)

#### `jarvis/audio/voice_registry.py` — Alternative voices restored
- Added 3 alternative voices to `BUILTIN_VOICES`:
  - `en_US-lessac-medium` — American English male (alternative to Alan)
  - `ru_RU-irina-medium` — Russian female (alternative to Dmitri)
  - `uk_UA-lada-x_low` — Ukrainian female (alternative to Mykyta)
- **NOTE**: Lada uses `x_low` quality (no medium model on HuggingFace)
- Default assignments unchanged: alan/dmitri/mykyta remain defaults
- Custom voice templates (baranov, pecherytsya) unchanged

#### `packaging/download_assets.py` — All 6 voices
- Expanded `PIPER_VOICES` list from 3 to 6 entries
- New voices: en_US-lessac-medium, ru_RU-irina-medium, uk_UA-lada-x_low
- Each voice downloads `.onnx` + `.onnx.json` files
- URL pattern: `huggingface.co/rhasspy/piper-voices/resolve/main/{lang}/{locale}/{speaker}/{quality}/`

#### `pyproject.toml` — AEC optional dependency
- Added `[project.optional-dependencies] aec` group
- Contains `pywebrtc-audio` for WebRTC echo cancellation
- Install via: `pip install -e ".[aec]"`
- NOT a required dependency — AEC mode gracefully falls back to
  half-duplex when the library is absent

#### `README.md` — Quick Setup section
- Added "Quick Setup (development)" section with `setup_jarvis.py` usage
- Placed before "Build from source" for discoverability
- Documents CLI flags and expected output

### Architecture Notes
- **setup_jarvis.py** is a standalone script with zero imports from the
  jarvis package itself — it runs before dependencies are installed.
  Uses only stdlib (urllib, subprocess, json, pathlib).
- Voice downloads use urllib (not httpx) for the same reason — httpx
  is a jarvis dependency that may not be installed yet.
- Each step that calls jarvis code (Whisper, Silero, openWakeWord)
  spawns a subprocess with the venv Python to isolate failures.
- The script outputs Russian-language progress for the target user
  while keeping all code comments and variable names in English.
- `uk_UA-lada-x_low` quality level was verified against the Piper
  voices HuggingFace repository — no medium quality model exists for
  this voice.

### How to use
```powershell
# First time setup
python setup_jarvis.py

# With a different Whisper model
python setup_jarvis.py --whisper-model small

# Skip smoke test
python setup_jarvis.py --skip-smoke-test

# Install AEC support (optional)
.venv\Scripts\pip install -e ".[aec]"
```

### Backward Compatibility
- ✅ No existing defaults changed
- ✅ voice_registry.py DEFAULTS dict unchanged (alan/dmitri/mykyta)
- ✅ pywebrtc-audio is optional (aec extras group)
- ✅ All existing tests should pass unchanged

---

## 2026-10-09 — T3.3 AEC / Barge-in Integration

### Summary
Integrated Acoustic Echo Cancellation (AEC) and barge-in management from
the Phase 2-3 kit into the main repository. Adds a configurable
`BargeInManager` with three operating modes: half-duplex (mute mic during
SPEAKING), AEC (WebRTC echo cancellation for full-duplex), and headphones
(no-op). Graceful degradation: AEC → half-duplex → headphones. Fully
backward compatible — if no AEC library is installed, everything works
exactly as before.

### New Files (2)

#### `jarvis/audio/aec.py` — BargeInManager
- `BargeInMode` enum: HALF_DUPLEX, AEC, HEADPHONES
- `BargeInManager` class:
  - `on_tts_start()` / `on_tts_end()` — half-duplex mute/unmute
  - `process_audio(mic_chunk, speaker_chunk)` — AEC echo removal
  - `check_barge_in(mic_chunk)` — barge-in detection via AEC VAD
  - `get_status()` — diagnostic snapshot for dashboard
- AEC backend: tries `pywebrtc_audio.AudioProcessingModule` first,
  then `echoff.EchoCanceller`, falls back to HALF_DUPLEX with warning
- Callbacks: on_mic_mute, on_mic_unmute, on_barge_in
- Properties: mode, is_mic_muted, tts_playing, aec_available
- English docstrings, type hints, repo logging style

#### `tests/audio/test_aec.py` — 25 tests
- TestHalfDuplex: mute/unmute on TTS, idempotency, no-callback safety
- TestHeadphones: no mute on TTS, tts_playing tracking
- TestAECFallback: graceful degradation without AEC library
- TestProcessAudio: passthrough in all modes without AEC backend
- TestCheckBargeIn: no barge-in without TTS, half-duplex/headphones
- TestGetStatus: all keys present, correct values, state changes
- TestBargeInMode: enum coverage, invalid mode rejection
- TestProperties: mode, aec_available, tts_playing
- TestBargeInCallback: trigger resets state and fires callback

### Modified Files (4)

#### `jarvis/audio/pipeline.py` — AEC integration into frame loop
- Added `barge_in_manager` optional parameter to `AudioPipeline.__init__`
- Import `BargeInManager, BargeInMode` from `jarvis.audio.aec`
- When AEC manager is present and in AEC mode: auto-enables barge_in,
  disables half_duplex boolean (manager handles both)
- `_dispatch_speaking()`: AEC-aware path processes frame through
  `process_audio()` before VAD feed, checks `check_barge_in()`
- `_run_response_chain()`: calls `barge_in_manager.on_tts_start()` when
  first LLM chunk transitions to SPEAKING
- `_run_response_chain()` finally block: calls
  `barge_in_manager.on_tts_end()` to ensure cleanup
- Updated module docstring: AEC section replaces Phase 6 backlog note
- Fully backward compatible: `barge_in_manager=None` preserves all
  existing behaviour (boolean half_duplex flag unchanged)

#### `jarvis/app.py` — Composition root wiring
- Import `BargeInManager` from `jarvis.audio.aec`
- Step 5d in `_build_audio_stack()`: creates `BargeInManager` with
  mode from `self.cfg.pipeline.barge_in_mode`
- Passes `barge_in_manager=self.barge_in_manager` to `AudioPipeline`

#### `jarvis/core/config.py` — New config field
- Added `barge_in_mode: str = "half_duplex"` to `PipelineConfig`
- Documented values: "half_duplex", "aec", "headphones"
- Default "half_duplex" matches existing behaviour

#### `requirements.lock` — Optional AEC dependency note
- Added comment block documenting optional `pywebrtc-audio` / `echoff`
  install instructions for AEC mode
- NOT added as a pinned dependency (optional, system works without it)

### Architecture Notes
- **Backward compatible**: `barge_in_mode` defaults to "half_duplex";
  existing configs work unchanged, no schema migration needed
- **Graceful degradation**: AEC mode without library → auto-fallback to
  half-duplex with warning log. Pipeline never fails to start.
- **No cloud dependencies**: all processing is local (WebRTC APM or
  echoff are both offline DSP libraries)
- **Separation of concerns**: `BargeInManager` owns echo/mute logic;
  `AudioPipeline` owns frame routing and state transitions. The manager
  is a passive component called by the pipeline at the right moments.
- **Config-driven**: mode selectable via `pipeline.barge_in_mode` in
  `config.json` or Settings UI (when wired)

### How to test
```bash
# Unit tests (no hardware needed)
pytest tests/audio/test_aec.py -v

# With AEC library installed
pip install pywebrtc-audio
# Then set barge_in_mode: "aec" in config.json
```

### Integration with existing Phase 3 code
- The boolean `half_duplex` flag in pipeline.py is preserved. When a
  BargeInManager is provided in AEC mode, the manager overrides it.
  When no manager is provided (or mode is half_duplex), the existing
  `if self._half_duplex: return` gate in `_dispatch_speaking` works
  exactly as before.

---

## TODO: Custom Voice Distribution

After training custom Piper voices (Baranov for RU, Pecherytsya for UK):
1. Upload `.onnx` + `.onnx.json` files to HuggingFace:
   - Create repo: `huggingface.co/serhii/jarvis-custom-voices`
   - Upload: `ru_RU-baranov-custom.onnx`, `uk_UA-pecherytsya-custom.onnx` + configs
2. Add download URLs to `setup_jarvis.py` (in `PIPER_VOICES` list or separate `CUSTOM_VOICES` list)
3. Add download URLs to `packaging/download_assets.py` (same pattern as standard voices)
4. Update `voice_registry.py` defaults: `ru → ru_RU-baranov-custom`, `uk → uk_UA-pecherytsya-custom`
5. Other users get custom voices automatically via `python setup_jarvis.py`

---

## 2026-10-09 — TTS Voice Fix: RU + UK voice downloads

### Summary
Fixed critical gap: `download_assets.py` only downloaded the English Piper
voice (alan). Russian (dmitri) and Ukrainian (mykyta) voices were registered
in `voice_registry.py` but never downloaded — meaning JARVIS would fall back
to reading Russian/Ukrainian text with an English voice.

### Modified Files (2)

#### `packaging/download_assets.py`
- Changed from single `PIPER_VOICE` to `PIPER_VOICES` list with 3 entries
- Downloads: `en_GB-alan-medium`, `ru_RU-dmitri-medium`, `uk_UA-mykyta-medium`
- Each voice = `.onnx` + `.onnx.json` (~60 MB per voice)

#### `jarvis/audio/voice_registry.py`
- Removed unused alternative voices (lessac, ryan, irina, lada)
- Kept only one voice per language: alan (EN), dmitri (RU), mykyta (UK)
- Custom voice templates (baranov, pecherytsya) remain for future use

---

## 2026-10-09 — Phase 2 Gaps Fix (T2.2 / T2.3)

### Summary
Closed the two remaining gaps in Phase 2 (Multilingual STT) found during
the full roadmap audit:
1. **T2.2 gap**: No voice tool for manual language switching — user could
   not say "speak english" or "говори українською" to lock the language.
2. **T2.3 gap**: No formal test phrase corpus for RU/UK/EN accuracy measurement.

### Context — Audit Results
A full repository audit against the Roadmap (Phases 0–4.5) was performed.
Phase 2 was ~90% complete; these two files close the gap to ~100%.
Audit report saved in session artifacts: `jarvis_audit_report.md`.

### New Files (3)

#### `jarvis/tools/local/switch_language.py` — T2.2 Manual Language Switch
- **SwitchLanguageTool** with VoicePatterns for 3 languages + auto:
  - EN: "speak english", "switch to english", "english please"
  - RU: "говори по-русски", "переключи на русский", "на русском"
  - UK: "говори українською", "переключи на українську", "по-українськи"
  - Auto: "auto detect", "автоопределение", "автовизначення"
- `execute()` sets `stt.language` (Whisper forced mode) AND calls
  `tts_manager.set_language()` (voice switch)
- Updates hotwords to match the new language
- Confirmation phrases in the TARGET language (all 3 × 4 combinations)
- Graceful degradation: works without STT or TTS manager
- **Dependencies**: `audio.stt` (HOTWORDS_BY_LANG), `core.phrases`

#### `tests/tools/local/test_switch_language.py` — 20 tests
- Voice pattern matching: 16 patterns across EN/RU/UK/auto + no-match
- Execution: STT language set, TTS manager called, hotwords updated
- Edge cases: no STT, no TTS, TTS failure → partial result
- Confirmation phrases: all language combos exist

#### `tests/audio/test_stt_phrases.py` — T2.3 Phrase Corpus
- **36 phrases total**: 12 per language (EN/RU/UK)
- Categories: short, command, long, numbers, mixed
- Each phrase tagged with: text, language, category, notes
- Brand names in each language (YouTube/Ютуб, GitHub/ГітХаб, etc.)
- **Corpus validation tests** (no model needed):
  - ≥10 phrases per language
  - All categories covered
  - No duplicates, no empty text
  - Brand names present
- **Language detection cross-check**: runs `phrases.detect_language()`
  on all 36 phrases, validates correct detection
- **Integration test placeholder**: for WER measurement with real WAV files
  (to be recorded at home)

### Modified Files (1)

#### `jarvis/tools/__init__.py` — Wiring
- Added `SwitchLanguageTool` import
- Added `stt` and `tts_manager` parameters to `setup_local_tools()`
- Registered `SwitchLanguageTool(stt=stt, tts_manager=tts_manager)`

#### `jarvis/app.py` — Wiring
- `setup_local_tools()` call updated: added `stt=self.stt, tts_manager=self.tts_manager`
- SwitchLanguageTool is now fully wired — no manual integration needed

### Integration Notes for Home Amazon Q
- **Everything is wired** — no manual steps needed
- The tool is fully backward compatible: `setup_local_tools()` without
  `stt`/`tts_manager` registers the tool with `None` — it won't crash,
  just won't switch anything.
- To record test audio fixtures: record 36 WAV files (16kHz mono),
  one per phrase, in `tests/audio/fixtures/{lang}/`.

### TODO at Home
- **T2.3 WER measurement**: Run `pytest tests/audio/test_stt_phrases.py -m integration -v`
  with faster-whisper model installed. The integration test class `TestSTTAccuracy` needs
  WAV fixtures (36 files, 16kHz mono, one per phrase from `ALL_PHRASES`).
  Record them into `tests/audio/fixtures/{en,ru,uk}/` and implement the test body.
- Assert average WER < 30% per language as the pass criterion.

### Backward Compatibility
- ✅ No existing files broken
- ✅ No new dependencies
- ✅ setup_local_tools() signature is backward compatible (new args have defaults)

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


---

## 2026-10-07 — Phase 3 Integration (T3.3 / T3.4)

### Summary
Pipeline Runner with thinking phrases, per-stage timeouts, and half-duplex mode.
User never hears silence > 1-2 seconds. Desktop speakers work without AEC.

### Modified Files (4)

#### `jarvis/audio/pipeline.py` — T3.3/T3.4 Pipeline Runner
- **Constructor**: 5 new params (thinking_phrases_enabled, half_duplex, stt/llm/tts_timeout_s)
- **STT timeout** (T3.4): `asyncio.wait_for(stt.transcribe(), timeout=10s)` — if timeout, speaks "error_stt_timeout" and returns to IDLE
- **Thinking phrases** (T3.4): after STT success, before LLM — speaks random phrase via Piper CPU ("One moment, sir" / "Сейчас проверю, сэр" / "Зараз перевірю, сер")
- **Half-duplex** (T3.3): `_dispatch_speaking()` returns immediately when enabled — no VAD processing during SPEAKING, prevents speaker-to-mic false triggers. Wake-word interrupt still works.
- `import random` added for phrase selection

#### `jarvis/core/config.py` — PipelineConfig (T3.3/T3.4)
- **New class PipelineConfig**: stt_timeout_s (10.0), llm_timeout_s (20.0), tts_timeout_s (15.0), thinking_phrases_enabled (True), half_duplex (True), max_recovery_attempts (3)
- **JarvisConfig**: added `pipeline: PipelineConfig` field
- **config.py restored**: file was accidentally truncated during edit — rebuilt from original + all Phase 2+3 changes applied cleanly

#### `jarvis/core/phrases.py` — Thinking & timeout phrases
- **6 thinking phrases** on 3 languages: thinking_general_1-3, thinking_check_1-2, thinking_long_1
- **2 timeout phrases** on 3 languages: error_stt_timeout, error_llm_timeout

#### `jarvis/app.py` — Wiring
- `AudioPipeline()` now receives all 5 PipelineConfig fields from `self.cfg.pipeline.*`

### New Files (1)

#### `tests/audio/test_pipeline_phase3.py` — 12 tests
- Thinking phrases: all keys exist, 3 languages, short enough for TTS, not empty
- Timeout phrases: exist with 3 languages
- PipelineConfig: defaults, custom values, present in JarvisConfig
- AudioPipeline: constructor accepts all Phase 3 params

### How It Works (User Perspective)
1. User says "Hey Jarvis, what's the weather?"
2. STT transcribes (with 10s timeout)
3. **Immediately**: JARVIS says "Сейчас проверю, сэр" (thinking phrase via Piper CPU — ~100ms)
4. LLM generates response (with 20s timeout)
5. TTS speaks the response (with 15s timeout)
6. During SPEAKING: mic input is dropped (half-duplex) — no false triggers
7. Wake-word ("Hey Jarvis") still works during SPEAKING for interrupt


---

## 2026-10-08 -- Phase 4 Integration (T4.1 / T4.2 / T4.3)

### Summary
Persistent memory subsystem. JARVIS can now remember facts, search memory,
forget on command, and log all conversations with quarterly rotation.

### New Files (6)

#### `jarvis/memory/__init__.py` -- Module package
#### `jarvis/memory/store.py` -- T4.1 MemoryStore
- **SQLite** database with FTS5 full-text search (LIKE fallback if FTS5 unavailable)
- **Fact** dataclass: id, text, category, source, language, tags, access_count, is_permanent
- **CRUD**: add(), get(), delete(), update() -- all async via to_thread
- **Search**: FTS5 match + filter by category/tag, fallback to LIKE
- **Stats**: count(), categories() for memory status reporting
- **WAL mode** for concurrent reads, foreign keys enabled
- **Tags** stored lowercase, indexed
- Data path: `%APPDATA%/Jarvis/memory/memory.db`

#### `jarvis/memory/conversation.py` -- T4.2 ConversationMemory
- **SQLite** per-quarter database with rotation
- **ConversationTurn** dataclass: id, user_text, assistant_text, language, session_id
- **log_turn()**: records every user-assistant exchange
- **Quarterly rotation**: current.db + archive/2026_Q4.db -- auto-rotates on quarter change
- **Search**: LIKE match on user_text and assistant_text
- **Session tracking**: each app launch gets a unique session_id
- Data path: `%APPDATA%/Jarvis/memory/conversations/current.db`

#### `jarvis/memory/tools.py` -- T4.3 Memory Voice Tools
- **RememberTool**: "Remember that..." / "Запомни..." / "Запам'ятай..." -- stores fact with category
- **RecallTool**: "What do you know about..." / "Що ти знаєш про..." -- FTS5 search, returns top 3
- **ForgetTool**: "Forget about..." / "Забудь..." -- searches + deletes (requires_confirmation=True!)
- **MemoryStatusTool**: "How many things do you remember?" -- count + categories
- All tools have **voice patterns** for pattern-layer routing (bypass LLM for common phrases)
- All tools support **i18n** (en/ru/uk)
- All tools handle None memory_store gracefully (return "Memory not available")

#### `tests/memory/test_store.py` -- 17 tests
- open, add, get, delete, update, search, search_by_category, search_by_tag
- count, count_by_category, categories, access_count, permanent_flag, tags_lowercase

#### `tests/memory/test_conversation.py` -- 7 tests
- open, log_turn, recent, search, count, session_id

### Modified Files (2)

#### `jarvis/app.py` -- Full wiring
- **Imports**: MemoryStore, ConversationMemory
- **Class attributes**: memory_store, conversation_memory
- **_build_audio_stack()**: creates MemoryStore() and ConversationMemory()
- **_audio_main()**: opens both stores at boot (non-fatal if fails)
- **_audio_main signature**: added memory_store + conversation_memory params
- **_audio_main call site**: passes self.memory_store + self.conversation_memory
- **setup_local_tools()**: passes memory_store=self.memory_store

#### `jarvis/tools/__init__.py` -- Memory tools registration
- **setup_local_tools()**: added memory_store parameter
- Registers RememberTool, RecallTool, ForgetTool, MemoryStatusTool when memory_store is provided

### How It Works (User Perspective)
- "Hey Jarvis, remember that my favorite game is Stalker 2" -> stored in memory.db
- "Hey Jarvis, what do you know about my games?" -> FTS5 search, speaks results
- "Hey Jarvis, forget about my old address" -> confirms, then deletes
- "Hey Jarvis, how much do you remember?" -> "I have 42 facts, sir. Categories: games: 15, personal: 12..."
- All conversations are automatically logged in conversations/current.db
- Every quarter, old conversations move to archive/

### Backward Compatibility
- All memory features are **opt-in**: if MemoryStore fails to open, tools return "not available"
- No existing tools or behavior changed
- No new dependencies (uses stdlib sqlite3)


---

## 2026-10-08 -- Phase 4 Extended (full memory architecture)

### Summary
Expanded Phase 4 from MVP to full memory architecture: ShardManager,
AutoTagger, SmartSearch with scoring, and 3 cache layers.

### New Files (4 modules + 3 test files)

#### `jarvis/memory/shard_manager.py` -- Category-based sharding
- **ShardManager**: per-category SQLite databases (games.db, tech.db, personal.db...)
- **normalize_topic()**: maps text to canonical shard names across languages
  - "diablo", "cuphead", "baldurs gate", "капхед", "балдурсгейт" all -> "games"
  - Cyrillic patterns for RU/UK: "игр", "кіберпанк", "відьмак"
- **Topic rules**: games, tech, personal, work, media (extensible)
- **shard_stats()**: {shard_name: fact_count} for diagnostics

#### `jarvis/memory/auto_tagger.py` -- Automatic tag/category extraction
- **AutoTagger**: pure regex + keywords, < 1ms per call, no LLM needed
- **detect_language()**: uk/ru/en from character analysis
- **Tag rules**: 30+ patterns across games, tech, personal, media
  - Games: stalker_2, dark_souls, elden_ring, cyberpunk_2077, witcher_3, skyrim, minecraft, diablo, gta + achievements, saves
  - Tech: python, javascript, docker, ollama, whisper, gpu, linux, github
  - Personal: name, birthday, favorites, family, friends, schedule
  - Media: anime, manga, ranobe, movies, series, books, music
- **TagResult**: tags, category, language, sub_category, full_category (hierarchical e.g. "games/stalker_2")
- **Extensible**: AutoTagger(extra_rules=[...]) for custom patterns

#### `jarvis/memory/smart_search.py` -- Multi-signal ranked search
- **SmartSearch**: 5 scoring components, all tunable:
  - **Text relevance** (40%): exact match + word overlap
  - **Frequency boost** (20%): access_count / 50 cap
  - **Time decay** (20%): exponential with 90-day half-life (permanent facts = always 1.0)
  - **Language boost** (10%): matching language = 1.0, other = 0.3
  - **Category boost** (10%): matching category = 1.0, other = 0.5
- **ScoredFact**: fact + total score + per-component scores
- No ML dependencies (FAISS can be added as parallel search path later)

#### `jarvis/memory/cache.py` -- Three cache layers
- **ResponseCache**: LRU (100 entries) + TTL (5 min). Skip LLM for repeated queries.
- **IntentCache**: persistent JSON file. "open youtube" -> open_url without LLM. Survives restarts.
- **ShortTermMemory**: last 10 turns in RAM. Context injection into LLM prompts across wake-word resets.
- Stubs for EmbeddingCache and PredictivePreload (future FAISS + game detection)

### Modified Files (2)

#### `jarvis/memory/store.py` -- AutoTagger integration
- MemoryStore now creates AutoTagger instance
- **add()** auto-enriches: detects language, extracts tags from text, refines category
- User-provided tags merged with auto-detected tags

#### `jarvis/memory/tools.py` -- SmartSearch integration
- RecallTool now uses SmartSearch.rank() instead of raw FTS5 results
- Fetches 20 raw results -> re-ranks with time decay/frequency/language -> returns top 5

### New Tests (3 files, 35 tests)

#### `tests/memory/test_auto_tagger.py` -- 12 tests
- Language detection (en/ru/uk/empty/mixed)
- Game/tech/personal/media detection
- Multiple tags, hierarchical category, category hint

#### `tests/memory/test_smart_search.py` -- 10 tests
- Empty facts, exact match, frequency boost, recency boost
- Permanent facts, language boost, category boost, limit, score range

#### `tests/memory/test_cache.py` -- 13 tests
- ResponseCache: put/get, miss, TTL expiry, LRU eviction, normalization, clear
- IntentCache: learn/get, miss, forget, normalization, save/load, clear
- ShortTermMemory: add/recent, max capacity, context string, empty, clear


---

## 2026-10-09 -- Phase 3 TTS Voice Registry + Phase 4 Complete

### Summary
1. **Phase 3**: Added voice_registry + tts_manager for per-language voice switching.
   Standard Piper voices for EN/RU/UK now, custom Baranov/Pecherytsya = just config change later.
2. **Phase 4**: Added all remaining modules from memory_kit_v2 that don't need GPU/FAISS.

### Phase 3 — TTS Voice Management (2 new files)

#### `jarvis/audio/voice_registry.py` -- Voice profiles + per-language assignments
- **VoiceProfile**: frozen dataclass (name, language, backend, display_name, is_custom, reference_audio)
- **VoiceBackend**: enum — PIPER (CPU), QWEN3_TTS (GPU future), SYSTEM (OS fallback)
- **BUILTIN_VOICES**: 7 stock voices (alan/lessac/ryan for EN, dmitri/irina for RU, mykyta/lada for UK)
- **CUSTOM_VOICE_TEMPLATES**: placeholders for baranov-custom, pecherytsya-custom, baranov-cloned
- **VoiceRegistry**: register/unregister/set_voice/get_voice, list_voices, list_available (checks .onnx), is_downloaded
- **Defaults**: EN=alan-medium, RU=dmitri-medium, UK=mykyta-medium
- **To swap to custom voice later**: `registry.set_voice("ru", "ru_RU-baranov-custom")` — one line, done

#### `jarvis/audio/tts_manager.py` -- Multilingual TTS with auto-switching
- **TTSManager**: wraps PiperTTS + VoiceRegistry
- **speak(text, language="ru")**: auto-switches voice when language changes
- **_switch_voice()**: unloads current Piper voice, loads new one (~200ms)
- **available_voices()**: returns list for settings UI (name, downloaded, active)
- Only reloads when language actually changes (not every speak() call)

#### `jarvis/core/config.py` -- TTSConfig extended
- New fields: `voices_en`, `voices_ru`, `voices_uk` (per-language voice names)
- New field: `backend` ("piper" / "qwen3_tts" / "auto")

### Phase 4 — Memory Complete (5 new modules)

#### `jarvis/memory/dynamic_shard_manager.py` -- Auto-promotion to dedicated shards
- **DynamicShardManager**: wraps ShardManager, auto-creates shards when topic reaches threshold
- **check_and_promote()**: scans general.db, counts normalized topics, promotes >= 10 facts
- **_promote()**: creates shard .db + migrates matching facts from general
- **Registry persistence**: shard_registry.json survives restarts
- **force_create_shard()**: manual shard creation
- **Fragmentation protection**: normalize_topic() maps all game titles to "games"

#### `jarvis/memory/auto_cleanup.py` -- Deduplication + archival
- **deduplicate()**: removes exact text duplicates, keeps highest access_count
- **archive_old()**: removes non-permanent facts > 365 days with 0 access
- **compact()**: VACUUM after cleanup
- No FAISS dependency (exact text match only)

#### `jarvis/memory/predictive_preload.py` -- Context-aware preloading
- **add_rule("stalker2.exe", category="games", tags=["stalker_2"])**
- **check_and_preload()**: checks Windows tasklist, loads/unloads matching facts
- **search()**: instant RAM search across preloaded facts (~0.01ms vs ~5ms SQLite)
- No FAISS dependency

#### `jarvis/memory/knowledge_store.py` -- Read-only YAML knowledge base
- Loads from `knowledge/base/` and `knowledge/shared/`
- **search()**: keyword search across all indexed YAML facts
- **get_system_info/get_commands/get_faq/get_behavior_patterns()**
- Falls back gracefully if PyYAML not installed (tries JSON)

#### `jarvis/memory/memory_manager.py` -- Unified facade
- **MemoryManager**: single entry point for all memory subsystems
- Coordinates: MemoryStore, ConversationMemory, KnowledgeStore, SmartSearch, Caches, AutoCleanup
- **remember()**: auto-tagged insertion
- **search()**: SmartSearch-ranked results
- **check_response_cache/cache_response()**: skip LLM for repeated queries
- **check_intent_cache/learn_intent()**: skip LLM classification for known commands
- **log_turn()**: conversation + short-term memory
- **run_cleanup()**: periodic dedup + archival
- **stats()**: diagnostics across all subsystems

### Knowledge Content (5 YAML files + 2 personal_starter files)
- `jarvis/knowledge/base/system_facts.yaml` — who is JARVIS, rules, capabilities
- `jarvis/knowledge/base/commands.yaml` — command → tool mapping
- `jarvis/knowledge/base/faq.yaml` — 54 questions × 3 languages (9 categories)
- `jarvis/knowledge/base/behavior_patterns.yaml` — 20 behavioral patterns
- `jarvis/knowledge/templates/game_template.yaml` — 11-section game database template
- `jarvis/personal_starter/my_preferences.yaml` — user preferences (Serhii)
- `jarvis/personal_starter/import_starter.py` — script to import preferences into SQLite

### New Tests (4 files, 32 tests)
- `tests/audio/test_voice_registry.py` — 13 tests (defaults, set_voice, custom, download check)
- `tests/memory/test_dynamic_shard.py` — 7 tests (resolve, create, persistence, counting)
- `tests/memory/test_cleanup.py` — 6 tests (dedup, archive, permanent protection, full)
- `tests/memory/test_knowledge_store.py` — 7 tests (load, search, system_info, commands, empty)
