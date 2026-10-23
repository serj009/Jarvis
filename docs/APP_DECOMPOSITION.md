# `app.py` Decomposition Plan (Этап 11 — PLAN ONLY, no code change yet)

> Status: proposal awaiting approval. **No code is to be changed until the
> owner signs off.** This document is the deliverable of Этап 11.

## 1. Current state

`jarvis/app.py` is **5437 lines**. The `JarvisApp` class spans lines
**1890–5294 (~3404 lines, 46 methods)** — the composition root, the Qt
lifecycle, the audio-thread lifecycle, every panel's construction, and all
the UI→audio / audio→UI glue live in this one class.

### 1.1 Method inventory, grouped by responsibility

| Responsibility | Methods | ~lines |
|---|---|---|
| **Construction / lifecycle** | `__init__` (99), `start` (213), `_on_quit` (179) | ~490 |
| **Config** | `_load_config` (63), `_on_config_change` (41) | ~104 |
| **Audio stack build + loop** | `_build_audio_stack` (714), `_boot_audio_stack` (177), `_run_audio_loop` (53) | ~944 |
| **Qt bootstrap** | `_create_qt_app` (33), `_build_confirmer` (48) | ~81 |
| **App-index refresh** | `_start_app_index_refresh` (102) | ~102 |
| **Panel construction** | `_build_ui` (71), `_build_tray_and_orb` (59), `_build_research_panel` (81), `_build_deep_research_panel` (132), `_build_notes_panel` (81), `_build_dashboard_panel` (75), `_build_help_panel` (18), `_build_clipboard_panel` (78), `_build_log_panel` (21), `_build_coding_panel` (71), `_build_agent_panel` (28), `_build_chat_panel` (38), `_build_command_palette` (31), `_build_onboarding_panel` (48), `_build_hotkeys` (49) | ~881 |
| **UI callbacks / glue** | `_on_test_voice`, `_open_settings`(+`_any_thread`), `_request_mode`, `_tray_open`(+palette), `_on_research_panel_width`, `_deep_research_config_provider`, `_set_deep_research_ultra`, `_take_note`, `_dr_counts`, `_notes_count`, `_consume_palette_text`, `_submit_palette_text`, `_consume_chat_text`, `_submit_chat_text`, `_speak_chat_text`, `_toggle_chat_panel`, `_on_onboarding_finished`, `_maybe_first_run_greeting` | ~430 |

Two methods alone — `_build_audio_stack` (714) and `start`/`_on_quit`
(392) — are a third of the class.

### 1.2 Why this is a problem

- **Testability**: UI tests construct a bare `JarvisApp()` and then have to
  hand-set attributes (`cfg`, `voices_dir`, `integration_registry`,
  `registry`) because those only exist after `start()`. That coupling is
  exactly what broke the two `_open_settings` tests in Этап 10.
- **Thread-safety reasoning**: the Qt-thread and the asyncio-audio-thread
  boundaries are implicit — scattered across 46 methods rather than owned by
  a named seam. SPEC.md § Threading is a prose rule, not an enforced one.
- **Change blast radius**: touching audio boot risks UI wiring and vice
  versa because they share `self`.

## 2. Target shape — extract services behind `JarvisApp`

Keep `JarvisApp` as a **thin composition root** that owns the three
long-lived subsystems and delegates. Extract four collaborators, each with
a single responsibility and an explicit constructor (no reliance on
half-initialised `self`):

```
JarvisApp  (composition root; ~300–400 lines)
├── ConfigService         — load / watch / apply config (_load_config, _on_config_change)
├── AudioStackController   — build + boot + run + teardown the asyncio audio thread
│                            (_build_audio_stack, _boot_audio_stack, _run_audio_loop,
│                             _build_confirmer, _run_audio_loop teardown in _on_quit)
├── UiController           — Qt app + all panels + tray + hotkeys + their callbacks
│                            (_create_qt_app, every _build_*_panel, _build_ui,
│                             _build_tray_and_orb, _build_hotkeys, _open_settings, etc.)
└── AppIndexService        — installed-apps index refresh (_start_app_index_refresh)
```

### 2.1 Boundaries and dependencies (one direction only)

```
JarvisApp ──builds──> ConfigService      (no back-reference)
JarvisApp ──builds──> AppIndexService    (depends on ConfigService read-only)
JarvisApp ──builds──> AudioStackController(depends on Config + the ToolRegistry)
JarvisApp ──builds──> UiController        (depends on Config + callbacks into
                                           AudioStackController via a small
                                           Protocol, NOT the whole app)
```

- **UI → audio** calls go through a narrow `AudioCommands` Protocol
  (`request_mode`, `submit_text`, `test_voice`), so `UiController` never
  imports the audio internals — it holds an interface. This also makes the
  Qt↔asyncio hop a single documented place (today: `QMetaObject.invokeMethod`
  / `QTimer.singleShot` scattered in callbacks).
- **audio → UI** callbacks stay as already-scheduled closures but are
  registered through one `UiCallbacks` object passed into the audio
  controller, instead of reaching back into `self.<panel>`.
- `ui/bootstrap.py` (added in Этап 8) is the natural home for
  `_create_qt_app`; `UiController` builds on it.

### 2.2 What explicitly does NOT move

- The `run() -> int` entry point and `__main__.py` restart loop stay.
- Tool registration (`_register_*` helpers) stays co-located with the
  registry build for now — a later, separate pass can extract a
  `ToolRegistryBuilder`. Out of scope for this decomposition to keep the
  blast radius small.

## 3. Safe migration order (small steps, green tests at every step)

Each step is one commit, `ruff` + `pyright` + `pytest` green before the next.
No step changes behaviour — these are pure extractions (move method + keep a
thin delegating wrapper on `JarvisApp` so external callers/tests keep working).

1. **AppIndexService** (smallest, most isolated). Move
   `_start_app_index_refresh` into `jarvis/app_index_service.py`; `JarvisApp`
   holds an instance and delegates. Lowest risk — proves the pattern.
2. **ConfigService**. Move `_load_config` + `_on_config_change`. Everything
   that reads `self.cfg` keeps doing so via a property that forwards to the
   service, so no call site changes in this step.
3. **UiController — panels first**. Move the 15 `_build_*_panel` /
   `_build_tray_and_orb` / `_build_hotkeys` methods into
   `jarvis/ui/controller.py`. They already take explicit args; the risk is
   wiring callbacks, so do it panel-by-panel (one commit per 3–4 panels)
   with the UI tests (`tests/ui/`) green each time.
4. **UiController — callbacks**. Move the UI-glue methods (`_open_settings`,
   `_consume_*`, `_submit_*`, `_toggle_chat_panel`, …). Introduce the
   `AudioCommands` Protocol here so callbacks stop reaching into audio.
5. **AudioStackController**. Move `_build_audio_stack` / `_boot_audio_stack`
   / `_run_audio_loop` / `_build_confirmer`, plus the audio-teardown slice of
   `_on_quit`. This is the largest method (714 lines) — split it internally
   first (one commit) into `_build_input`, `_build_stt`, `_build_tts`,
   `_build_pipeline` sub-builders BEFORE moving, so the move is mechanical.
6. **Slim `JarvisApp`**. `start()` and `__init__` now just instantiate and
   wire the four collaborators; `_on_quit` calls each one's `shutdown()`.

### 3.1 Guardrails

- After each step, `tests/ui/test_jarvis_app.py` and the audio tests must
  stay green. The two `_open_settings` tests fixed in Этап 10 become the
  canary for the UiController extraction.
- Keep the thin delegating wrapper on `JarvisApp` for any method a test
  patches by its `jarvis.app.<name>` path, until the test is migrated in the
  same commit. Never change a test and the code it covers in different
  commits.
- A new `tests/test_architecture_rules.py`-style guard could later assert
  "no panel construction outside ui/controller.py", mirroring the win32 /
  PySide6 guards from Этап 8 — optional, proposed not required.

## 4. Estimated effort

~6–8 commits, each small and reversible. Steps 1–2 are an afternoon; step 3
(panels) is the bulk; step 5 (audio) needs the most care because of the
asyncio/Qt thread boundary. No behaviour change at any point — this is
structural only.

## 5. Open questions for the owner

1. OK to introduce `jarvis/ui/controller.py` and
   `jarvis/app_index_service.py` / `jarvis/config_service.py`, or prefer a
   different module layout (e.g. a `jarvis/app/` package)?
2. Is the `AudioCommands` / `UiCallbacks` Protocol boundary acceptable, or
   should UI↔audio keep using the existing direct-closure style?
3. Should tool-registration extraction (`ToolRegistryBuilder`) be folded in,
   or kept as a separate future effort (current recommendation: separate)?

---

## 6. Execution results (updated after attempting the plan)

Steps were attempted in order. The dependency reality diverged from the
plan's assumption that panel/audio builders "already take explicit args" —
they actually read and write app state through `self`.

### Done (clean extractions, committed)
- **Step 1 — AppIndexService** ✅ `jarvis/services/app_index.py`. Method
  never used `self`; moved verbatim as `start_app_index_refresh()`.
  Commit `4c332f1`.
- **Step 2 — config diff** ✅ `jarvis/services/config_service.py`. Only the
  pure `compute_changed_fields` moved; `_load_config` / `_on_config_change`
  stayed (bootstrap + UI orchestration). Commit `a5c0802`.

Net: `app.py` 5437 → 5314 lines.

### Deferred (NOT clean extractions — would require a back-reference)
A per-method dependency scan showed these are composition-root logic:
- **Step 3 — UI panels**: the 15 `_build_*_panel` methods write
  `self.<panel>` and read each other's panels plus ~15 app callbacks
  (`_on_quit`, `_open_settings`, `_request_mode`, …). A clean `UiController`
  is impossible without a reference to the app writing `app.<panel> = …` —
  the back-reference §2.1 forbids.
- **Step 5 — AudioStackController**: `_build_audio_stack` (714 lines)
  assigns **39** `self` attributes — it builds essentially the entire
  object graph (stt, tts, pipeline, router, memory_store, registry, …);
  `_run_audio_loop` reads 24 of them. This IS the composition root.
- **Step 6 — slim JarvisApp**: moot while 3 and 5 are deferred.

### Recommendation
For the UI/audio builders the honest options are:
1. `_PanelBuildersMixin` / `_AudioBuildersMixin` — physical relocation into
   mixins `JarvisApp` inherits. Removes ~1600 lines with no behaviour change
   and no back-reference, but re-files rather than decouples. Verifiable via
   MRO + import even without PySide6/sounddevice.
2. Leave the builders in `app.py` (Steps 1–2 done). They are cohesive — all
   assemble the app graph.

Deferred pending owner decision; no code moved for Steps 3/5/6.
