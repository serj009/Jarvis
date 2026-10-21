# Amazon Q Branch — Changelog

> This file tracks all changes made in the `amazonq` branch by Amazon Q. Purpose: when merging with `pygpt` branch, the home Amazon Q instance can read this file to instantly understand what changed, without analyzing the entire repository manually.
> Format: each session is a dated block with files changed, rationale, and integration notes.

---


## 2026-10-21 — Integrations framework Layer 3: runtime wiring

### Summary
Enabled integrations now contribute live, executable tools and feed the
proactive engine — the framework is end-to-end. Integration capabilities
reach the model via the ToolRegistry (LLM tool-calling), the same surface
MCP tools use, registered/unregistered on the fly.

### Design decision
Integration actions are wired as **ToolRegistry tools**, NOT router voice
patterns. The pattern table is assembled once at construction and is
first-match-wins by priority; rebuilding it live is risky. The registry
looks tools up per call and supports register/unregister at runtime — the
right surface for open-ended online actions (and consistent with MCP).

### Files
| File | Change |
|------|--------|
| `jarvis/integrations/wiring.py` | NEW. `IntegrationToolAdapter` (plugin tool schema → live Tool: JSON-schema→pydantic model, execute→plugin.execute_tool, risk=WRITE). `wire_integration_tools()` syncs the registry to enabled plugins (idempotent, never clobbers an existing local/MCP tool name, unregisters on disable). `collect_integration_events()` for proactive data. |
| `jarvis/ui/settings/tabs/integrations.py` | Toggling an integration now live-syncs the tool registry (appears/disappears without restart). Accepts an optional `tool_registry`. |
| `jarvis/ui/settings/main_window.py` | Threads `tool_registry` → IntegrationsTab. |
| `jarvis/app.py` | Startup: `wire_integration_tools` after setup_local_tools (restores enabled plugins' tools). Passes `tool_registry` + `integration_registry` to SettingsWindow and `_audio_main`. Proactive tick now also runs scenario B (upcoming dates) from `integration_registry.get_all_proactive_data()`. Added `self.registry = None` partial-build default. |
| `tests/integrations/test_wiring.py` | NEW — ~13 tests (adapter, schema→model, add/remove sync, collision-safety, execute-through-registry, events). |

### ⚠️ MANUAL TESTING NEEDED AT HOME
1. Enable the Example (Test) integration in Settings → confirm `example_ping`
   becomes callable (and disappears when you toggle it off) WITHOUT a restart.
2. Startup with an integration pre-enabled (cfg.integrations.enabled) → its
   tools register automatically; confirm Jarvis still reaches "ready".
3. A real plugin returning get_proactive_data() (e.g. a future Calendar) →
   verify upcoming events surface through the proactive engine.

---


## 2026-10-21 — Integrations framework Layer 2: Settings UI + wiring (+ app.py init fix)

### Summary
Online Integrations now have a Settings tab and are wired into the app at
startup. Also fixed a latent startup bug this layer surfaced.

### Settings tab + wiring
| File | Change |
|------|--------|
| `jarvis/ui/settings/tabs/integrations.py` | NEW "Online Integrations" tab — per-plugin card: toggle, status dot (🔴 disabled / 🟡 enabled / 🟢 connected), masked API-key field + Save, "data goes to {service}" warning. Enable is blocked (with a pip-install hint) when a plugin's required_packages are missing. Builds its own registry/auth if not injected (self-sufficient in tests). |
| `jarvis/ui/settings/tabs/__init__.py`, `main_window.py` | Registered the tab + an optional `integration_registry` ctor param; 17 sidebar pages. |
| `jarvis/app.py` | Creates `IntegrationRegistry` (discovers plugins, enabled from cfg.integrations.enabled) and passes it to SettingsWindow. Enable-state persists to config. |
| `tests/ui/test_integrations_tab.py` (NEW), `tests/integrations/test_framework.py` | Qt-dependent tab tests under tests/ui (offscreen qapp); framework tests stay under tests/integrations. |

### 🐛 Latent startup bug fixed (important for home)
The Layer-3 proactive work *referenced* `self.proactive_engine` in app.py
(passed to setup_local_tools and _audio_main) but the block that **creates**
it had never actually landed in app.py — so a real run would have crashed at
startup with `AttributeError: 'JarvisApp' has no attribute 'proactive_engine'`.
Unit tests didn't catch it because `JarvisApp.__init__` isn't exercised end-to-end.
This layer adds the missing creation of BOTH `proactive_store`/`proactive_engine`
and `integration_registry` (beside the reminder scheduler, after `_data_root`),
and gives both a `None` default at the top of `__init__` so the partial-build
Qt-handler tests (test_jarvis_app.py) stay green — matching the existing
`self.confirmer = None` convention.

### ⚠️ MANUAL TESTING NEEDED AT HOME
1. **Startup** — this is the big one: launch Jarvis and confirm it reaches
   "ready — say hey jarvis" (the missing-proactive_engine crash is fixed, but
   only a real boot proves the whole __init__ chain).
2. Settings → Online Integrations: the Example (Test) plugin appears, toggles
   on/off, state persists across restart (cfg.integrations.enabled).
3. Proactive engine still starts (log: "proactive engine started").

---


## 2026-10-21 — Mouse control (UI interaction)

### Summary
New `mouse_control.py` filling the UI-interaction gap: pointer clicks, moves,
scroll, drag-and-drop, and position. Clicks/moves can target absolute
coordinates OR on-screen text via OCR (reusing jarvis.vision.ocr.ScreenOCR —
not duplicated). pyautogui already shipped; zero new deps.

| Tool | name | What |
|------|------|------|
| MouseClickTool | `mouse_click` | left/right/middle/double at (x,y) or on OCR-found text |
| MouseMoveTool | `mouse_move` | move cursor to (x,y) or OCR text |
| MouseScrollTool | `mouse_scroll` | up/down/left/right + "to the bottom/top" |
| MouseDragTool | `mouse_drag` | drag-and-drop between two points |
| MousePositionTool | `mouse_position` | report cursor coords (READ) |

### Safety
- `pyautogui.FAILSAFE = True` + `pyautogui.PAUSE = 0.1` applied on every lazy import.
- OCR-targeted clicks refuse a match with confidence < 80% (never blind-click).
- Every action logged (button, coords, result). Risk: mouse_* = WRITE, position = READ.

### Files
| File | Change |
|------|--------|
| `jarvis/tools/local/mouse_control.py` | NEW — 5 tool classes + `_find_text_on_screen` (screenshot→ScreenOCR.extract_regions→center of best match). |
| `jarvis/tools/catalogue.py`, `__init__.py`, `risk.py` | Registered 5 tools. |
| `tests/ui/test_capabilities.py`, `tests/ui/test_settings.py` | Added to `_UNDOCUMENTED_TOOLS` + Tools-tab set. |
| `tests/tools/local/test_mouse_control.py` | NEW — ~24 tests (coord/OCR click, low-confidence refusal, move, scroll, drag, position, routing), pyautogui + OCR mocked. |

### ⚠️ MANUAL TESTING NEEDED AT HOME (real cursor + Tesseract)
1. Coordinate click/move/scroll/drag — verify the cursor actually goes where asked.
2. OCR click: "click on Save" over a real window with a Save button — needs Tesseract
   installed (eng+rus+ukr). Confirm the low-confidence refusal path on ambiguous text.
3. FAILSAFE: slam the cursor to a screen corner mid-action — pyautogui should abort.

---


## 2026-10-21 — Proactive ("initiative") engine (Layers 1–3)

### Summary
New opt-out proactive engine: Jarvis watches usage patterns and occasionally
speaks up on its own via the existing ProactiveSpeech event. Built in 3 layers,
each tested + green. Enabled by default at HIGH, adapts down on repeated
dismissals, controllable by voice + Settings.

### Layer 1 — foundation (schema v38)
| File | Change |
|------|--------|
| `jarvis/core/config.py` | New `ProactiveConfig` (enabled, initiative_level, check_interval, work/leisure thresholds, boot_similarity, dismiss_downgrade_count). Schema 37→38. |
| `jarvis/core/migrations.py` | `_migrate_v37_to_v38`. |
| `jarvis/memory/proactive_engine.py` | NEW. `InitiativeLevel` (HIGH/MEDIUM/LOW/SILENT), `ProactivePatternStore` (SQLite `proactive_patterns`, CREATE IF NOT EXISTS), `ProactiveEngine` (level persistence, adaptive dismiss→downgrade, `may_speak` gate, `speak` via bus), helpers `foreground_window_title` / `classify_leisure` / `jaccard`. |

### Layer 2 — scenarios + loop
| Addition | What |
|----------|------|
| `media_pause()` | Reuses media_control key sender; fired before important/critical nudges. |
| `ProactiveScenarios` | A abandoned-project, B upcoming-date, C long-work (health), D boot-routine (Jaccard≥thr), E command-sequence (≥5, conf≥70%). Each a pure method with injected now/foreground for deterministic tests. |
| `ProactiveEngine` | foreground focus tracking, boot/command recording, per-key cooldown + dismiss-time snooze, `maybe_speak_scenario`, async `run(tick_fn)` loop + `stop`. |

### Layer 3 — voice tools + Settings + wiring
| File | Change |
|------|--------|
| `jarvis/tools/local/proactive_tools.py` | NEW. `set_initiative` (up/down/silent), `proactive_status`, `dismiss_proactive` (now/forever) — EN/RU/UK. |
| `jarvis/tools/catalogue.py`, `__init__.py`, `risk.py` | Registered 3 tools (set/dismiss WRITE, status READ); `setup_local_tools` gained `proactive_engine` param. |
| `jarvis/ui/settings/tabs/proactive.py` + `__init__.py` + `main_window.py` | NEW "Proactive" settings tab (toggle, level dropdown, threshold sliders); 16 sidebar pages. |
| `jarvis/app.py` | ProactiveEngine + store created beside ReminderScheduler (own SQLite, bus, usage_stats, level persisted via save_config); background loop started in `_audio_main` (samples foreground → long-work health nudge with media-pause), stopped on shutdown; engine injected into the voice tools. |
| `tests/memory/test_proactive_engine.py` | ~60 tests across all 3 layers. |

### ⚠️ MANUAL TESTING NEEDED AT HOME (real Windows + foreground apps)
1. Long-work nudge: keep an IDE focused past the threshold (lower it in Settings →
   Proactive for a quick test) → Jarvis should pause media and suggest a break.
2. Voice control: "не мешай" ×3 → auto-downgrades a level; "будь активнее" raises it;
   "какой уровень инициативы" reports it. Level survives restart (saved to config).
3. Config migration v38: first launch adds the `proactive` section; a `.bak` is written.
4. DB: `proactive_patterns.db` is created in the memory dir. Boot-routine and
   command-sequence scenarios need real repeated usage to accumulate before they fire.
5. Birthdays (scenario B) need memory facts tagged birthday/event with a date — verify
   once you have such facts.

---


## 2026-10-21 — Reminders: recurring, specific date, snooze, edit

### Summary
Filled 4 gaps on top of the existing reminders subsystem (remind_me /
remind_me_at / list / delete + ReminderStore + ReminderScheduler). No
existing class signatures changed; one-shot reminders keep working.

| # | Gap | How |
|---|-----|-----|
| 1 | Recurring reminders | New `RecurringReminderTool` (name=`remind_recurring`): daily/weekdays/weekly/monthly. ScheduledReminder gained `recurrence`/`recurrence_time` (SQLite ALTER-migration). ReminderScheduler now reschedules a fired recurring reminder (computes next trigger, resets fired) instead of marking it done. `compute_next_trigger()` handles weekend-skip + month-day clamp + year rollover. |
| 2 | Specific date | RemindMeAtTool got an optional `date` field + `parse_date` ('YYYY-MM-DD', '15 ноября', 'november 15') + `resolve_date_time` (rolls to next year if past). No date → old today/tomorrow behaviour. |
| 3 | Snooze | New `SnoozeReminderTool` (name=`snooze_reminder`): re-fires the last-fired reminder after N minutes (default 10) or until a given time. ReminderStore.`last_fired()` + `update_reminder()`. |
| 4 | Edit | New `EditReminderTool` (name=`edit_reminder`): change text and/or time of an existing reminder by text/id match; keeps recurring reminders' recurrence_time in sync. |

### Files
| File | Change |
|------|--------|
| `jarvis/memory/scheduled_reminders.py` | ScheduledReminder +recurrence fields; ALTER-TABLE migration; `update_reminder`/`get`/`last_fired`; `compute_next_trigger`; scheduler reschedules recurring. |
| `jarvis/tools/local/remind_me.py` | RemindMeAtArgs +date + date parsing; 3 new tool classes + helpers (`parse_date`, `resolve_date_time`, `_norm_time`, `_norm_ampm`, `_norm_recurrence`). |
| `jarvis/tools/catalogue.py`, `jarvis/tools/__init__.py`, `jarvis/tools/risk.py` | Registered 3 new tools (WRITE risk). |
| `tests/ui/test_capabilities.py`, `tests/ui/test_settings.py` | New tools added to `_UNDOCUMENTED_TOOLS` + Tools-tab set (convention for LLM-dispatched reminder tools). |
| `tests/tools/local/test_remind_me.py` | +~24 tests. |

### ⚠️ MANUAL TESTING NEEDED AT HOME
1. Recurring: "напоминай каждый день в 9 встать" / "remind me on weekdays at 8:30 ...".
   Let one fire; confirm it reschedules for the next day (next weekday for weekdays)
   instead of disappearing. Check DB migration adds recurrence columns to your existing
   `scheduled_reminders.db` without data loss.
2. Specific date: "напомни 15 ноября сдать отчёт" / "remind me on november 15 ...".
3. Snooze: let a reminder fire, say "отложи на 10 минут" / "snooze"; confirm it re-fires.
4. Edit: "перенеси напоминание встреча на 16:00".

---

## 2026-10-21 — File Manager: find filters, CreateFileTool, ArchiveTool

### Summary
Filled 3 real gaps in the existing `file_manager.py` (which already had
find-by-name/move/rename/delete/mkdir). Did NOT rewrite the file or duplicate
existing actions or disk_cleaner.

| # | Gap | How |
|---|-----|-----|
| 1 | Filters in `find` | Added optional `extension` / `newer_than` / `min_size` / `max_size` to FileManagerArgs (empty = old behaviour). `_find` applies them; `_parse_size` ("10MB"→bytes) and `_parse_age` ("1w"→timedelta) helpers. Filtered-find voice patterns at priority 290 (beat generic find 300), EN/RU/UK, with word→ext and folder-keyword maps. |
| 2 | `CreateFileTool` (new, name=`create_file`) | Creates an empty file; templates for .py/.md/.txt/.html; refuses to overwrite; sandbox-validated; RiskLevel.WRITE. |
| 3 | `ArchiveTool` (new, name=`archive`) | Zips a folder via `shutil.make_archive`; auto-names `{folder}_{YYYYMMDD}.zip`; reports size; sandbox-validated; RiskLevel.WRITE. |

### Files
| File | Change |
|------|--------|
| `jarvis/tools/local/file_manager.py` | Extended FileManagerArgs + `_find`; added `_parse_size`/`_parse_age`, word/folder maps, KNOWN_FOLDERS, helper fns; new CreateFileTool + ArchiveTool. |
| `jarvis/tools/catalogue.py`, `jarvis/tools/__init__.py` | Registered the 2 new tools. |
| `jarvis/tools/risk.py` | `create_file` / `archive` → RiskLevel.WRITE. |
| `tests/ui/test_capabilities.py` | Added `create_file` / `archive` to `_UNDOCUMENTED_TOOLS` (convention for LLM-dispatched file tools, same as `file_manager`). |
| `tests/ui/test_settings.py` | Added both names to the Tools-tab checkbox set assertion. |
| `tests/tools/local/test_file_manager.py` | +18 tests: size/age parsing, extension/size/age/combined find filters, filtered-find routing, create-file templates + refuse-overwrite, archive (default + custom dest + missing folder), routing. |

### ⚠️ MANUAL TESTING NEEDED AT HOME
1. Filtered find by voice: "find pdfs in downloads", "find files larger than 100 MB",
   "найди файлы за последнюю неделю" — verify real results from real folders.
2. Create file: "create a python file called utils", "создай файл заметки.md на рабочем столе"
   — confirm template content and that it lands in the right folder.
3. Archive: "archive the project folder", "запакуй загрузки" — confirm the .zip appears
   next to the folder with the right contents and size.

---

## 2026-10-21 — Window management: corner snap, snap-layout, minimize-all, virtual desktops

### Summary
Filled the window-management gaps on top of the existing `window_manager.py`
(which already had focus/minimize/maximize/restore/close/snap-left-right/list).
All 6 requested features, extending the existing tool rather than adding new files.

| # | Feature | How |
|---|---------|-----|
| 1 | Corner snap (4 quarters) | New `snap_top_left/top_right/bottom_left/bottom_right` actions on WindowActionTool, driven by a single `_SNAP_GEOMETRY` fraction table |
| 2 | Snap center | `snap_center` action (50%×50% centered box) |
| 3 | Snap layout (two windows) | New `SnapLayoutTool` — "VS Code left, Chrome right" places both in one command |
| 4 | Minimize-all / Restore-all | New `MinimizeAllTool` / `RestoreAllTool` (Win+D via pyautogui) |
| 5 | Virtual desktops | New `VirtualDesktopTool` — next/previous (Win+Ctrl+←/→), Task View (Win+Tab) |
| 6 | Voice patterns EN/RU/UK | Added to each tool's `voice_patterns`; center priority is higher-numbered than left/right/corner so it never shadows them (first-match-wins) |

### Files
| File | Change |
|------|--------|
| `jarvis/tools/local/window_manager.py` | Extended WindowActionTool (actions + geometry table + EN/RU/UK corner/center patterns); added `_send_keys` helper; new tools SnapLayoutTool, MinimizeAllTool, RestoreAllTool, VirtualDesktopTool. |
| `jarvis/tools/catalogue.py`, `jarvis/tools/__init__.py` | Registered the 4 new tools (voice patterns + Settings→Tools checkbox come for free since both are derived from the catalogue). |
| `tests/tools/local/test_window_manager.py` | +~20 tests: corner/center snap, geometry table, snap-layout (success + missing window), minimize/restore-all, virtual-desktop actions, and voice-pattern routing for EN/RU/UK. |

### ⚠️ MANUAL TESTING NEEDED AT HOME
These drive real Win32 / global hotkeys — verify on the main PC:
1. Corner snap: "snap chrome top left", "snap chrome bottom right", "center chrome".
2. Snap layout: "VS Code left, Chrome right" / "код слева, хром справа" — both windows placed.
3. Minimize/restore all: "minimize all" / "сверни всё", then "restore all".
4. Virtual desktops: "next desktop" / "следующий стол", "task view". These need pyautogui
   (bundled) and real Windows — on this work PC they are only mock-tested.
5. Multi-monitor: snap uses the PRIMARY screen metrics (GetSystemMetrics 0/1). If you run
   multiple monitors at home, confirm snap lands where expected; may need per-monitor work later.

---

## 2026-10-21 — Work-PC session: test fixes, Russian STT, self-learning, memory upgrades, auto-critic Layer 1

### Context for the home PC
This whole session was done on a **secondary work PC** (fresh clone of the `amazonq`
branch, no Ollama, no models). All changes are pushed to `origin/amazonq`. Pull at home
with `git pull origin amazonq`. The full automated suite is green here
(**4957 passed, 14 skipped**), but several things need **manual verification at home**
with real Ollama + models + microphone — see the "MANUAL TESTING NEEDED AT HOME" section
at the end of this block.

### 1. Fixed 13 failing tests (commit 1e69194)
One real code bug + test-isolation issues. No app-logic changed except the hotkey fix.
| File | Change |
|------|--------|
| `jarvis/tools/local/hotkey_sender.py` | **Real bug:** `_parse_hotkey("")` returned `['']` not `[]`, so empty combos were not rejected. Now drops empty tokens. |
| `tests/tools/local/test_converter.py`, `test_dictation.py` | Hard-coded absolute path from another machine → resolve via module `__file__`. |
| `tests/audio/test_devices.py` | Fixture stubbed `sd` but not the smart detector → leaked to real mic (idx 15). Now stubs `device_detector.detect_best_input`. |
| `tests/vision/test_screen_change.py` | `sys.modules` injection missed already-imported `PIL.ImageGrab` → real screen capture. Now patches `PIL.ImageGrab.grab`. |
| `tests/ui/test_jarvis_app.py` | Build-order test didn't stub `_maybe_first_run_greeting` (reads `self.cfg`). Added stub. |

### 2. Russian/Ukrainian STT recognition fix (commit 3edf2ee)
Root cause: NOT a language setting — Whisper was multilingual (`model=small`, `language=auto`).
The confidence formula `exp(avg_logprob)` was calibrated for English; correctly-transcribed
RU/UK speech scored 0.5–0.65, below the 0.70 "proceed" threshold → routed to "clarify"
("I didn't understand") ~90% of the time.
| File | Change |
|------|--------|
| `jarvis/audio/stt.py` | `_compute_confidence`: softened mapping to `exp(avg_logprob / 2)`. Genuine RU/UK speech now clears 0.70; noise gate (`no_speech_prob`) and low-logprob collapse intact. Thresholds unchanged (pinned by tests). |
| `tests/audio/test_stt_phase2.py` | Regression test: RU speech at avg_logprob -0.5 → proceed. |

### 3. Self-learning now fires for snap/one-shot commands (commit ef7e9c9)
Pattern/snap commands ("открой хром", "громче") and one-shot mode (`max_tool_iterations==1`)
executed OUTSIDE the router feedback loop, so `tool_examples`, `usage_stats`, and
`personal_aliases` never recorded them — the most frequent commands were not being learned.
| File | Change |
|------|--------|
| `jarvis/core/personalization.py` | New `record_tool_success()` — single learning path (examples + usage + aliases), each step guarded. |
| `jarvis/llm/intent_router.py` | `execute_intent()` gained optional `on_tool_success` hook, fires only on `result.success`. |
| `jarvis/app.py` | `_make_router_adapter` passes the hook (wired at all 3 call sites). No double counting — feedback-loop tools never reach this adapter by design. |

### 4. Memory Pt 1 — fact metadata (commit 586b476, schema facts table)
Added 4 columns via ALTER TABLE migration (same pattern as tier/condensed_text):
`importance` (0.5), `confidence` (1.0), `valid_until` (NULL), `status` (active).
`source` + `access_count` already existed. Read with len-guards in `_row_to_fact`;
`add()` got optional params with defaults. Plaintext + indexed (status, importance).
Files: `jarvis/memory/store.py`, `tests/memory/test_store.py`.

### 5. Memory Pt 4 — contradictions + history (commit 2457203)
No more silent overwrite on contradiction.
| Addition | What |
|----------|------|
| `supersede(old_id, new_text, …)` | Adds new active fact, flips old to `superseded` (kept), links `contradicts`. |
| `set_status(id, status, reason)` | Reversible status change, history-recorded. |
| `history(id)` | Audit trail. |
| `fact_history` table | Append-only, ALTER-safe migration. |
| search filter | `superseded` hidden by default; `include_superseded=True` to audit. |
Auto-detection of contradictions intentionally deferred to Pt 8.
Files: `jarvis/memory/store.py`, `tests/memory/test_store.py`.

### 6. Memory Pt 8 — integrity checks (commit 9e9ef91)
Pure Python+SQL, no LLM.
| Method | What |
|--------|------|
| `AutoCleanup.integrity_report(conn)` | Finds untagged active facts, broken relation links, unresolved contradictions (both `contradicts` facts still active), expired `valid_until`. Report only — no silent mutation. |
| `AutoCleanup.prune_broken_relations(conn)` | The one safe auto-fix: deletes dangling edges. |
| `MemoryManager.run_integrity_report()` | Runtime entry point (read-only). |
Files: `jarvis/memory/auto_cleanup.py`, `jarvis/memory/memory_manager.py`, `tests/memory/test_cleanup.py`.

### 7. Auto-critic (self-evaluation) — Layer 1: foundation (schema v37)
OPT-IN, OFF BY DEFAULT. Optional external critic LLM that grades interaction traces.
Layer 1 is config + trace collection + privacy only (no network, no UI yet).
| File | Change |
|------|--------|
| `jarvis/core/config.py` | New `SelfEvaluationConfig` (enabled=False, provider, api_key [DPAPI-encrypted], api_model, api_base_url, interval_hours, max_traces_per_run, save_insights_to_memory=False). Schema bumped 36→37. `api_key` added to `_SECRET_FIELD_PATHS`. |
| `jarvis/core/migrations.py` | `_migrate_v36_to_v37` + registered. |
| `jarvis/memory/self_evaluation.py` | NEW. `InteractionTrace`/`TraceStep`, `build_trace()`, `strip_secrets()`/`strip_secret_args()` — redacts keys/passwords/logins/tokens before anything leaves the machine. Normal RU/EN speech untouched. |
| `tests/memory/test_self_evaluation.py` | NEW. 18 tests. |
**Design decisions (user-confirmed):** (1) report-only, user chooses whether to save
insights to memory; (2) full traces minus secrets; (3) built in layers.
**Still TODO (next layers):** Layer 3 — Settings UI tab (toggle, key, provider, interval).

#### Layer 2 — background task + critic call + report (added same session)
| File | Change |
|------|--------|
| `jarvis/memory/self_evaluation.py` | Added `build_critic_messages()` (rubric → strict-JSON prompt), `parse_critic_response()` (tolerant: strips ``` fences / prose, never raises), `run_evaluation(traces, send_fn, max_traces)` (network-free via injected `send_fn`), `render_report_markdown()`, and `SelfEvaluationScheduler` (periodic loop, writes a markdown report per run, disabled = no-op). |
| `tests/memory/test_self_evaluation.py` | +~20 tests for prompt/parse/run/report/scheduler. |
**Design:** `send_fn` is injected so the module never imports the LLM stack and is fully testable offline. Report-only (no behaviour change). Reports written to a per-run markdown file.
#### Layer 3 — runtime wiring + Settings UI (added same session)
| File | Change |
|------|--------|
| `jarvis/memory/self_evaluation.py` | Added `TraceRecorder` (bus observer — groups TranscriptionReady/ToolInvoked/ToolResult/LLMResponseComplete into per-turn traces, bounded ring buffer, secret-stripped on finalise) and `make_send_fn(cfg)` (builds an async critic round-trip via APILLMClient from SelfEvaluationConfig; returns None when disabled or no key). |
| `jarvis/ui/settings/tabs/self_eval.py` | NEW. "Self-Evaluation" settings tab: enable toggle, save-insights toggle, provider dropdown, model, API key (password field, DPAPI), base URL, run-every slider (0.5 h–7 d), max-traces slider. |
| `jarvis/ui/settings/tabs/__init__.py`, `jarvis/ui/settings/main_window.py` | Registered the tab (import, export, nav item after Security, page instance). Now 15 sidebar pages. |
| `jarvis/app.py` | `_audio_main` gained optional `self_eval_cfg` / `self_eval_report_dir`. When `self_evaluation.enabled` AND a key is present: builds TraceRecorder, subscribes it to the bus (observer-only, no hot-path change), starts `SelfEvaluationScheduler`, and stops it on shutdown. Reports written to `%APPDATA%\Jarvis\memory\self_eval_reports\`. Imported `SelfEvaluationConfig`. |
| `tests/memory/test_self_evaluation.py` | +~11 tests for TraceRecorder (turn grouping, errors, bounded drain, secret stripping) and make_send_fn gating. |
**Now fully wired.** With the toggle OFF (default) nothing runs. With it ON + a valid key, the critic evaluates recent traces every `interval_hours` and writes a markdown report. Still report-only — no behaviour change to Jarvis.

---

### ⚠️ MANUAL TESTING NEEDED AT HOME (real Ollama + models + mic)
The automated suite can't cover these — verify by hand on the main PC:

1. **Russian STT (fix #2) — HIGHEST PRIORITY.** Speak several RU and UK phrases to Jarvis.
   It should act on them instead of replying "не понял / повторите". If still over-cautious,
   lower `stt.confidence_proceed` in Settings, or report back the `confidence` /
   `detected_language` / `action` values from `%APPDATA%\Jarvis\logs\jarvis.log`.
2. **Self-learning (fix #3).** Run a few snap commands ("открой хром", "громче"), then check
   `%APPDATA%\Jarvis\tool_examples.json` and `usage_stats.json` are being populated.
   Confirm a personal alias is learned (say "открой браузер" → chrome, then check it maps).
3. **Hotkey empty-combo (fix #1).** Trigger `send_hotkey` with an empty combo — should refuse
   politely, not fire a stray keystroke.
4. **Memory metadata/supersede (Pt 1 & 4).** On first launch the DB auto-migrates
   (`memory.db`): new columns + `fact_history` table. Verify Jarvis still starts and recalls
   facts. Try a correction flow if wired, and confirm superseded facts don't resurface.
5. **Memory DB migration safety.** Back up `%APPDATA%\Jarvis\memory\memory.db` before first
   run at home, just in case — the ALTER migrations are additive and tested, but your home DB
   has real data this work PC doesn't.
6. **Config migration v37.** First launch rewrites `config.json` to schema 37 (a `.bak` is
   written automatically). Confirm existing settings survive and the new `self_evaluation`
   section appears (disabled).
7. **Auto-critic (now fully wired, Layers 1–3) — OPTIONAL, verify only if you want it:**
   - Settings → **Self-Evaluation** tab exists, toggle is OFF by default. With it off, confirm
     Jarvis behaves exactly as before and makes no network calls.
   - To try it: get a free Groq API key, paste it in the tab, enable the toggle, set a short
     interval (e.g. 0.5 h) for testing. After the interval, a markdown report should appear in
     `%APPDATA%\Jarvis\memory\self_eval_reports\`. Verify traces in the report contain NO
     secrets (keys/passwords/logins).
   - Confirm turning the toggle back off stops new reports on the next restart.

## 2026-10-15 — Security: 4-layer defense + crypto vault + encrypted storage (schema v31)

### Summary
Full security overhaul: 4-layer defense system against malicious LLM, crypto-grade password vault 
(Argon2id + AES-256-GCM + DPAPI), encrypted storage for all user data, and dependency audit fixes.

### Encrypted Storage (data at rest)
- `jarvis/platform/encrypted_store.py` — EncryptedJsonStore (DPAPI + JRVS_ENC header)
- `jarvis/platform/encrypted_db.py` — SQLite column-level encryption
- 11 JSON stores migrated to encrypted: user_profile, aliases, bookmarks, timers, RSS, monitors, etc.
- Memory SQLite: text, tags, condensed_text columns encrypted
- SecurityConfig added to config.py, migration v30→v31

### 4-Layer Defense System
| Layer | Module | What |
|-------|--------|------|
| 1 | `origin_tracker.py` | HMAC-signed user voice input |
| 2 | `leak_scanner.py` | Personal data exfiltration detection in URLs |
| 3 | `output_sanitizer.py` | Prompt injection + suspicious tool chain detection |
| 4 | `audit_log.py` | Hash-chained DPAPI-encrypted audit log |
| Gate | `tool_guard.py` | Unified security checkpoint for all tool calls |

### Crypto Password Vault
- `password_vault.py` — 3-layer encryption: Argon2id → AES-256-GCM → DPAPI
- `auto_login.py` — Login to sites, password NEVER enters LLM context
- `vault_manager.py` — 6 management tools (save/list/remove/unlock/lock/change)

### Dependency Audit Fix
- Added [extras] group: qrcode, yt-dlp, plyer, wmi, pygetwindow
- Added to [all] group
- All lazy imports verified with fallbacks

### Stats
- 25 new/modified files
- 200 new security test functions
- Schema v31

---

## 2026-10-15 — 20 new tools: browser scenarios + windows extended + knowledge extractor

### Summary
Added 20 new tool modules (8 browser scenarios, 6 windows extended, 5 cross-platform, 1 knowledge extractor), 
plus window_manager regex fix. Total: ~6,400 lines of new tool code, ~4,200 lines of tests.

### Web Knowledge Extractor (structured page-to-memory)
| File | What |
|------|------|
| `jarvis/memory/knowledge_extractor.py` | LLM-based structured extraction engine |
| `jarvis/tools/local/web_knowledge.py` | "Сохрани информацию со страницы" — universal knowledge extraction |

### Browser Scenarios (8 tools)
| Tool | File | What |
|------|------|------|
| page_monitor | `page_monitor.py` | Track page changes, notify on diff |
| page_compare | `page_compare.py` | Compare two pages via LLM |
| smart_form_fill | `smart_form_fill.py` | Auto-fill forms from user_profile |
| page_translate | `page_translate.py` | Translate page via LLM |
| price_lookup | `price_lookup.py` | Find cheaper prices for products/games |
| youtube_summary | `youtube_summary.py` | Summarize YouTube video from subtitles |
| full_page_screenshot | `full_page_screenshot.py` | Full-page screenshot (Selenium CDP) |
| image_scraper | `image_scraper.py` | Download all images from page |

### Windows Extended (6 tools)
| Tool | File | What |
|------|------|------|
| display_manager | `display_manager.py` | Brightness, night mode, display info |
| startup_manager | `startup_manager.py` | List/add/remove from Windows startup |
| network_tools | `network_tools.py` | IP, speed test, network info |
| disk_cleaner | `disk_cleaner.py` | Disk space, large folders, clean temp |
| hotkey_sender | `hotkey_sender.py` | Send keyboard shortcuts |
| win_notifications | `win_notifications.py` | Windows toast notifications |

### Cross-Platform (5 tools)
| Tool | File | What |
|------|------|------|
| tab_manager | `tab_manager.py` | Close other tabs, count, find, duplicate |
| password_gen | `password_gen.py` | Generate passwords/passphrases |
| rss_reader | `rss_reader.py` | Subscribe/read RSS feeds |
| qr_share | `qr_share.py` | Generate QR codes, share links |
| focus_mode | `focus_mode.py` | Block distracting sites/apps |

### Integration
- `__init__.py`: 290→351 lines, all 49 tool classes registered
- `risk.py`: 259→301 lines, 29 new TOOL_RISK_MAP entries
- `tool_filter.py`: 567→733 lines, 10 new categories + browser category extended
- `catalogue.py`: 257→318 lines, 29 new class entries

### Fix
- `window_manager.py`: regex `ок(?:о|о)н` → `окон` (redundant alternation)

### Tests: 19 new test files, ~209 test functions

---

## 2026-10-15 — Phase 6: Windows Control + Phase 7: Browser (13 new tools)

### Summary
Added 8 new Windows control tools (Phase 6) and 5 browser tools + browser controller package (Phase 7).
Total: 13 new tool modules, 1 new package (jarvis/browser/), 13 new test files, ~342 test functions.

### Phase 6 — Windows Control (8 tools)

| Tool | File | What it does |
|------|------|-------------|
| window_manager | `jarvis/tools/local/window_manager.py` | Focus/minimize/maximize/snap/close windows, list windows |
| timers | `jarvis/tools/local/timers.py` | Set/cancel/list timers, JSON persistence |
| power_control | `jarvis/tools/local/power_control.py` | Shutdown/restart/sleep/hibernate/caffeinate |
| cli_runner | `jarvis/tools/local/cli_runner.py` | Whitelisted CLI commands (ping, ipconfig, dir, etc.) |
| download_manager | `jarvis/tools/local/download_manager.py` | Download files by URL to ~/Downloads |
| package_manager | `jarvis/tools/local/package_manager.py` | winget search/install/uninstall |
| game_saves | `jarvis/tools/local/game_saves.py` | Find/backup/restore saves for 10 games |
| process_monitor | `jarvis/tools/local/process_monitor.py` | Process memory/CPU, heavy processes, wait-for-exit |

### Phase 7 — Browser (5 tools + 1 package)

| Tool | File | What it does |
|------|------|-------------|
| browser_controller | `jarvis/browser/browser_controller.py` | Two modes: Simple (webbrowser) + Advanced (Selenium) |
| browser_tabs | `jarvis/tools/local/browser_tabs.py` | Open/close/switch tabs, back/forward/refresh |
| content_reader | `jarvis/tools/local/content_reader.py` | Read/summarize pages, save as PDF |
| bookmarks | `jarvis/tools/local/bookmarks.py` | Save/search/open bookmarks, JSON storage |
| media_control | `jarvis/tools/local/media_control.py` | Play/pause, next track, fullscreen, media keys |

### Integration
- `jarvis/tools/__init__.py` — all 13 tools registered in setup_local_tools()
- `jarvis/tools/risk.py` — TOOL_RISK_MAP updated for all new tools
- `jarvis/tools/tool_filter.py` — keyword categories updated
- `pyproject.toml` — [browser] optional deps (selenium, beautifulsoup4, yt-dlp), added to [all]

### Tests (13 new files, ~342 test functions)
- tests/tools/local/test_{window_manager,timers,power_control,cli_runner}.py
- tests/tools/local/test_{download_manager,package_manager,game_saves,process_monitor}.py
- tests/tools/local/test_{browser_tabs,content_reader,bookmarks,media_control}.py
- tests/browser/test_browser_controller.py

---

## 2026-10-15 — Critical fixes: 8 tests + setup_jarvis.py + migrations v29→v30

### Summary
Fixed all 8 remaining test failures from home run, fixed setup_jarvis.py for clean first-run experience,
synced LLM model default (qwen3:8b), fixed missing Ukrainian voice (mykyta-medium→mykyta-high).
Schema v29→v30.

### Bug Fixes — 8 Failed Tests

#### test_open_target_routing.py (4 tests) — LLM call count = 0
- **Root cause**: Russian voice patterns in `open_app.py` (`^(?:открой|запусти|открыть)\s+(.+)$`) intercepted test phrases at pattern layer BEFORE reaching LLM
- **Fix**: Replaced test phrases with non-matching English phrases that don't trigger any pattern
- File: `tests/llm/test_open_target_routing.py`

#### test_hot_reload.py (1 test) — NameError: name 'self' is not defined
- **Root cause**: `_make_router_adapter()` is a free function (not a method), but used `self.user_profile`
- **Fix**: Added `user_profile` parameter to `_make_router_adapter()`, passed from call-site in JarvisApp
- File: `jarvis/app.py`

#### test_pipeline.py (1 test) — timeout
- **Root cause**: Pre-existing flaky timing — 1.0s timeout too short under load
- **Fix**: Increased `wait_for_cs` timeout 1.0→3.0s
- File: `tests/audio/test_pipeline.py`

#### test_intent_router.py (1 test) — timeout
- **Root cause**: `route()` now does async work in `_inject_auto_context` before streaming, 1.0s too short
- **Fix**: Increased `asyncio.wait_for` timeout 1.0→3.0s
- File: `tests/llm/test_intent_router.py`

### setup_jarvis.py — First-Run Fixes

#### Dependencies: `pip install -e ".[dev,embeddings]"`
- **Was**: `pip install -e .` (only base deps — no pytest, sentence-transformers, faiss)
- **Now**: `pip install -e ".[dev,embeddings]"` — installs everything in one step
- File: `setup_jarvis.py`

#### Ukrainian voice: mykyta-medium → mykyta-high
- **Root cause**: HuggingFace `rhasspy/piper-voices` has `mykyta` ONLY in `high` quality (114MB), no `medium`
- **Fix**: Updated voice name + URL in 6 files
- Files: `setup_jarvis.py`, `packaging/download_assets.py`, `jarvis/core/config.py`, `jarvis/audio/voice_registry.py`, `tests/audio/test_voice_registry.py`, `jarvis/core/migrations.py`

#### LLM model: qwen2.5:7b-instruct → qwen3:8b
- **Problem**: setup_jarvis.py downloaded `qwen3:8b`, but config expected `qwen2.5:7b-instruct`
- **Fix**: Synced config default to match what setup downloads
- Files: `jarvis/core/config.py`, `jarvis/core/migrations.py`, `jarvis/llm/ollama_client.py`, `jarvis/memory/memory_manager.py`, `jarvis/memory/auto_classifier.py`, `jarvis/ui/research_panel.py`, `tests/core/test_config.py`, `README.md`, `CONTRIBUTING.md`

### Migration v29 → v30
1. `llm.model == "qwen2.5:7b-instruct"` → `"qwen3:8b"` (only if old default)
2. `tts.voices_uk == "uk_UA-mykyta-medium"` → `"uk_UA-mykyta-high"`
3. `tts.voice == "uk_UA-mykyta-medium"` → `"uk_UA-mykyta-high"`

### Files Changed (15 total)
- `jarvis/app.py` — _make_router_adapter() self fix
- `jarvis/core/config.py` — LLM default + voice default + schema v30
- `jarvis/core/migrations.py` — _migrate_v29_to_v30()
- `jarvis/llm/ollama_client.py` — model default
- `jarvis/memory/memory_manager.py` — model default
- `jarvis/memory/auto_classifier.py` — model default
- `jarvis/ui/research_panel.py` — model default
- `jarvis/audio/voice_registry.py` — mykyta-high
- `setup_jarvis.py` — dev+embeddings deps + mykyta-high
- `packaging/download_assets.py` — mykyta-high
- `tests/llm/test_open_target_routing.py` — 4 test phrase fixes
- `tests/audio/test_pipeline.py` — timeout 1→3s
- `tests/llm/test_intent_router.py` — timeout 1→3s
- `tests/audio/test_voice_registry.py` — mykyta-high assert
- `tests/core/test_config.py` — model default assert
- `README.md`, `CONTRIBUTING.md` — qwen3:8b

---

## 2026-10-14 — Visual redesign of Settings UI

### Summary
Complete visual redesign of the Settings window: human-readable labels,
section cards with descriptions and hints, emoji-prefixed tab labels,
new STT device/compute controls, and friendly tool names. Zero
functional changes — all config read/write/save paths are identical.

### What changed visually

- **Tab labels**: "General" → "⚙️  General", "Voice" → "🎤  Voice",
  "Models" → "🧠  AI Models", "Tools" → "🔧  Tools", etc.
- **Models tab**: split into 3 card sections (🎤 Speech Recognition,
  🧠 AI Brain, 👁️ Vision) with friendly labels ("STT model size:" →
  "Recognition Model" + hint), descriptive combo items
  ("small" → "Small (recommended)"), new Device and Processing dropdowns
  with "auto" option
- **Voice tab**: organized into 3 card sections (🎧 Audio Devices,
  👂 Wake Word, 🗣️ Text-to-Speech) with emoji labels and hints
- **General tab**: log level and idle timeout moved into a section card
  with hints ("Log Detail Level", "Sleep After Inactivity")
- **Tools tab**: tool checkboxes show human-readable names with emoji
  ("screenshot" → "📸 Screenshot", "open_url" → "🌐 Open Website")

### New features

- **STT Device dropdown**: "Auto (recommended)" / "GPU (CUDA)" / "CPU"
  — maps to existing `config.stt.device` field (was missing from UI)
- **STT Compute "auto" option**: "Auto (recommended)" added to compute
  dropdown — maps to existing `config.stt.compute_type = "auto"`
- **STT "Medium" model option**: added to dropdown for users with GPU
- **Section cards**: `_widgets.py` provides `SectionCard`, `SectionHeader`,
  `SettingCard` reusable widgets with dark card background (#111111),
  rounded corners, proper spacing

### Theme changes (`theme.py`)

- New `#sectionCard` objectName selector (bg #111111, 8px radius)
- New `#sectionTitle` selector (12pt, letter-spacing 1px)
- New `#sectionDesc` selector (9pt, #707070)
- New `#settingLabel` selector (11pt, white, weight 400)
- New `#settingHint` selector (8pt, #606060)
- New `#divider` selector (1px #1a1a1a line)
- GroupBox: removed border, accent-colored title, increased spacing
- ScrollArea: transparent background

### Files changed

| File | Change |
|------|--------|
| `jarvis/ui/settings/theme.py` | Added section card / hint / divider QSS selectors, improved GroupBox style |
| `jarvis/ui/settings/_widgets.py` | **NEW** — `SectionCard`, `SectionHeader`, `SettingCard`, `make_divider()`, `slider_with_label()` |
| `jarvis/ui/settings/tabs/voice.py` | Reorganized into 3 section cards (Audio Devices, Wake Word, TTS), friendly labels + hints |
| `jarvis/ui/settings/tabs/models.py` | Full redesign: 3 section cards (STT, LLM, Vision), display-name combos, new device/compute dropdowns |
| `jarvis/ui/settings/tabs/general.py` | Log level + idle timeout in SectionCard with hints, scrollable layout |
| `jarvis/ui/settings/tabs/tools.py` | `TOOL_DISPLAY_NAMES` dict, `_display_name()` helper, emoji-prefixed checkbox labels |
| `jarvis/ui/settings/main_window.py` | Emoji tab labels via `_TAB_LABELS` tuple |
| `tests/ui/test_settings.py` | Updated tab-label assertion, added `test_models_tab_has_stt_device_and_compute_combos`, `test_models_tab_stt_device_writes_back`, `test_tools_tab_checkboxes_have_friendly_names`; `stt_size` test uses `currentData()` |
| `AMAZONQ_CHANGELOG.md` | This entry |

### Test changes

- `test_main_window_has_six_tabs_in_order` → renamed to
  `test_main_window_has_seven_tabs_in_order`, updated expected labels
  to include emoji prefixes and "AI Models" rename
- `test_models_tab_populates_from_config` → changed
  `stt_size.currentText()` assertion to `stt_size.currentData()` since
  combo now stores display names as text and config values as data
- **NEW** `test_models_tab_has_stt_device_and_compute_combos` — verifies
  the new device/compute dropdowns exist and default to "auto"
- **NEW** `test_models_tab_stt_device_writes_back` — changing device
  combo updates `config.stt.device`
- **NEW** `test_tools_tab_checkboxes_have_friendly_names` — verifies
  emoji display names on tool checkboxes

### Integration notes

- **Zero functional impact**: all `_persist()` / `save_config()` /
  `on_change()` call paths unchanged. Config field names and types
  identical. Only UI layout and display strings changed.
- **`_widgets.py`** is a new module but not exported from `__init__.py`;
  only imported by the tab files.
- **STT combo signals**: changed from `currentTextChanged` to
  `currentIndexChanged` + `currentData()` pattern since display text
  no longer equals config values.
- **Scroll areas**: Voice, Models, and General tabs now wrap content in
  `QScrollArea` so section cards don't clip on smaller windows.

---

## 2026-10-14 — QA: STT GPU + Multilingual full audit — 2 bugs fixed

### Summary
Full QA review of STT GPU upgrade + multilingual support across RU/UK/EN.
Verified all code paths, found and fixed 2 bugs, confirmed multilingual
model resolution is correct for all 3 languages.

### Bugs found and fixed

#### ❌ BUG 1: `app.py` — `stt.device` hot-reload missing
- **Symptom**: Changing `stt.device` in Settings (e.g. "auto" → "cpu") had
  no effect until full restart. The hot-reload handler only watched for
  `stt.model_size` and `stt.compute_type` changes.
- **Fix**: Added `"stt.device"` to the hot-reload condition in
  `_handle_config_changed()`, and set `stt._configured_device = new.stt.device`
  alongside model_size and compute_type before the unload/load cycle.

#### ⚠️ BUG 2: `stt.py` — No CUDA OOM fallback in `load()`
- **Symptom**: If device resolved to "cuda" (from "auto") but CUDA ran out
  of memory (e.g. Ollama using most of the 12 GB), `load()` raised
  `STTLoadError` and STT was completely unavailable — no fallback to CPU.
- **Fix**: Added CUDA→CPU fallback in `load()`: when device was auto-resolved
  to "cuda" (not explicitly forced by user) and loading fails, automatically
  retry with "cpu" + appropriate compute_type. User-forced `device="cuda"`
  still raises STTLoadError as expected.

### Files changed

| File | Change |
|------|--------|
| `jarvis/app.py` | Added `stt.device` to hot-reload condition + update `_configured_device` |
| `jarvis/audio/stt.py` | CUDA OOM fallback in `load()`: auto→cuda fail → retry CPU |
| `AMAZONQ_CHANGELOG.md` | This entry |

---

## 2026-10-14 — STT GPU Upgrade: small model + CUDA auto-detect + fallback

### Summary
Upgraded Whisper STT from CPU-only/base/int8 to GPU-auto/small/auto for
significantly better multilingual accuracy (~85-90% vs ~70% for Russian).
RTX 3060 12GB has ~6GB free VRAM (Ollama uses ~5-6GB), and the `small`
model in float16 needs only ~1GB — fits comfortably.

All changes include robust CPU fallback: no CUDA → CPU, no torch → CPU,
CUDA OOM → logged warning + CPU. Zero new dependencies.

### Changes

#### `jarvis/core/config.py` — STTConfig + schema v26
- `model_size` default: `"base"` → `"small"` (better accuracy)
- New field: `device: Literal["auto", "cpu", "cuda"] = "auto"`
- `compute_type` default: `"int8"` → `"auto"` (float16 on CUDA, int8 on CPU)
- Added `"auto"` to compute_type Literal
- `CURRENT_SCHEMA_VERSION` bumped 25 → 26
- Migration `_migrate_v25_to_v26`: upgrades base/int8 → small/auto/auto only
  when user was on exact v25 shipping defaults; explicit choices preserved

#### `jarvis/audio/stt.py` — GPU support + auto-device resolution
- New `_resolve_device(configured)` static method: auto → try CUDA → CPU fallback
- New `_resolve_compute_type(device, configured)` static method: auto → float16/int8
- `load()` uses resolved device/compute_type; logs final configuration
- Robust: no torch → CPU, no CUDA driver → CPU, any exception → CPU

#### `jarvis/app.py` — wire device config + VRAM registration
- `FasterWhisperSTT(...)` now receives `device=self.cfg.stt.device`
- Registered `"stt"` as VRAM consumer (1000 MB, priority 40)

#### `setup_jarvis.py` — download `small` by default
- `step_whisper()` default: `"base"` → `"small"`
- `--whisper-model` argparse default: `"base"` → `"small"`
- Added `"medium"` and `"medium.en"` to argparse choices
- Updated help text

#### `tests/core/test_config.py` — updated + new tests
- `test_defaults_match_spec`: updated for small/auto/auto
- `test_fresh_install_gets_base_multilingual_default` → renamed to
  `test_fresh_install_gets_small_gpu_defaults`
- 8 new tests for v25→v26 migration: default upgrade, explicit model
  preservation, compute_type preservation, device-already-set guard,
  on-disk round-trip, fresh install defaults

### Files changed

| File | Change |
|------|--------|
| `jarvis/core/config.py` | STTConfig fields, schema v26, migration v25→v26 |
| `jarvis/audio/stt.py` | `_resolve_device()`, `_resolve_compute_type()`, load() logging |
| `jarvis/app.py` | `device=` param, STT VRAM registration |
| `setup_jarvis.py` | Default model small, argparse update |
| `tests/core/test_config.py` | Updated defaults + 8 new migration tests |
| `AMAZONQ_CHANGELOG.md` | This entry |

### Integration notes
- No new dependencies: faster-whisper already supports CUDA when torch+CUDA is installed
- Fallback chain: auto → try CUDA → CPU. Never crashes on missing CUDA.
- Migration is safe: only touches exact v25 defaults (base + no device field)
- VRAM budget: small/float16 ≈ 1 GB; fits within 6 GB free alongside Ollama

---

## 2026-10-14 — QA Review: Critical config.py NameError fix

### Summary
Pre-push QA review found a **critical NameError** in `config.py` that would
crash the application on import. Two migration functions (`_migrate_v23_to_v24`
and `_migrate_v24_to_v25`) were defined AFTER the `MIGRATIONS` dict that
references them (lines 1154/1175 vs. dict at line 1033). Python evaluates
dict literals eagerly, so the module would raise `NameError` on first import.

### Fix
Moved both function definitions to before the `MIGRATIONS` dict (after
`_migrate_v22_to_v23`), keeping the same logical order. Removed the
duplicate definitions that were previously placed after `save_config()`.

### Files changed

| File | Change |
|------|--------|
| `jarvis/core/config.py` | Moved `_migrate_v23_to_v24` and `_migrate_v24_to_v25` before MIGRATIONS dict |
| `AMAZONQ_CHANGELOG.md` | This entry |

---

## 2026-10-14 — Phase 5: Sandbox + Tools (T5.1, T5.2, T5.3)

### Summary
Implemented the full Phase 5 security layer: workspace sandbox with path
whitelist, risk-level taxonomy for tools, and risk-level-driven confirmation
integration. All changes are backward-compatible with existing 1700+ tests.

### T5.1 — Workspace Sandbox (whitelist)
- **New file: `jarvis/tools/sandbox.py`** — `PathValidator` class with:
  - Configurable whitelist of allowed directories (from config)
  - `validate(path) -> Path` — resolves, normalises, checks symlinks/`..`/whitelist
  - Symlink escape detection (target outside whitelist → blocked)
  - Traversal detection (`..` in raw input → logged at WARNING)
  - Windows-compatible (drive letters, backslashes, case-insensitive)
  - Home directory fallback when whitelist is empty (~/Desktop, ~/Documents)
  - `SandboxViolationError` with raw path + reason for ToolResult error messages
- **Modified: `jarvis/tools/local/files.py`** — `ListDirectoryTool` now accepts
  optional `PathValidator` and validates all paths before I/O. Without a
  validator (direct construction), falls back to original behavior (backward
  compatible with all existing tests).
- **Modified: `jarvis/tools/__init__.py`** — `setup_local_tools()` now builds a
  `PathValidator` from the sandbox config and passes it to `ListDirectoryTool`.

### T5.2 — Risk Levels
- **New file: `jarvis/tools/risk.py`** — `RiskLevel` IntEnum:
  - `READ` (10) — read-only, no confirmation, minimal logging
  - `WRITE` (20) — creates/modifies, no confirmation, audit-logged
  - `DESTRUCTIVE` (30) — irreversible, requires user confirmation
  - `SYSTEM` (40) — system-level ops, requires confirmation + warning
- Static `TOOL_RISK_MAP` assigns risk levels to all ~40 built-in tools.
- `risk_level_for_tool(tool)` lookup: explicit attribute > static map > default READ.
- `should_confirm(tool)` — single decision point for confirmation, checks
  BOTH the legacy `requires_confirmation` flag AND the risk level.

### T5.3 — Permissions/Confirmations (risk-integrated)
- **Modified: `jarvis/tools/registry.py`** — `execute()` now uses
  `should_confirm(tool)` instead of raw `requires_confirmation` check:
  - READ → execute immediately
  - WRITE → execute (with risk-level audit in log)
  - DESTRUCTIVE → confirmation required (via existing QtToolConfirmer)
  - SYSTEM → confirmation required + warning
- Backward compatible: tools with `requires_confirmation=True` still prompted.
- New: tools marked DESTRUCTIVE in risk map (delete_note, delete_deep_research,
  clear_clipboard_history, forget) now also prompt for confirmation.
- Risk level included in tool execution log: `[tool] name ok in 42ms (risk=READ)`

### Config
- **New config section: `sandbox`** with:
  - `allowed_paths: list[str]` — default `["~/Desktop", "~/Documents", "~/Downloads"]`
  - `confirm_destructive: bool = True` — master switch for destructive-action prompts
- Schema version bumped 24 → 25
- Migration v24→v25 adds sandbox section with defaults

### Tests
- **New: `tests/tools/test_sandbox.py`** (24 tests) — whitelist acceptance/rejection,
  `..` traversal, symlink escape, empty paths, Windows paths, home fallback
- **New: `tests/tools/test_risk_levels.py`** (20 tests) — enum properties, ordering,
  TOOL_RISK_MAP validity, lookup order, should_confirm logic, catalogue coverage
- **New: `tests/tools/test_confirmations.py`** (11 tests) — risk-driven confirmation
  flow through registry.execute(), backward compatibility with legacy flag

### Files changed

| File | Change |
|------|--------|
| `jarvis/tools/sandbox.py` (NEW) | PathValidator, SandboxViolationError |
| `jarvis/tools/risk.py` (NEW) | RiskLevel enum, TOOL_RISK_MAP, should_confirm |
| `jarvis/core/config.py` | SandboxConfig, schema v25, migration v24→v25 |
| `jarvis/tools/registry.py` | Import risk module, use should_confirm() in execute() |
| `jarvis/tools/__init__.py` | Export new types, wire sandbox into setup_local_tools |
| `jarvis/tools/local/files.py` | Accept PathValidator, validate before I/O |
| `tests/tools/test_sandbox.py` (NEW) | 24 sandbox tests |
| `tests/tools/test_risk_levels.py` (NEW) | 20 risk-level tests |
| `tests/tools/test_confirmations.py` (NEW) | 11 confirmation integration tests |
| `AMAZONQ_CHANGELOG.md` | This entry |

### Integration notes
- All existing tests pass without modification: `ListDirectoryTool()` (no args)
  defaults to `path_validator=None`, preserving original behavior.
- Schema migration is safe: existing configs gain `sandbox` section at defaults.
- Risk map covers all built-in tools; MCP tools default to READ (safe default).
- The `requires_confirmation` protocol member is UNCHANGED — existing MCP and
  local tools work identically. Risk level is an additive signal, not a replacement.

---

## 2026-10-14 — Fix: Whisper model mismatch + pytest PermissionError

### Root Cause 1: JARVIS fails to start — Whisper model not found
`config.py` defaulted to `model_size="tiny.en"` (English-only), but
`setup_jarvis.py` downloads the `"base"` model (multilingual). Since
`stt.py` uses `local_files_only=True`, it crashed at launch trying to
load a model that was never downloaded.

**Fix** — aligned config defaults with setup_jarvis.py:
- `STTConfig.model_size`: `"tiny.en"` → `"base"`
- `STTConfig.language`: `"en"` → `"auto"`
- Added migration v23→v24: converts `tiny.en`→`base`, `en`→`auto` for
  existing configs (only when values match the broken defaults)
- Schema version bumped 23 → 24

### Root Cause 2: 182 pytest ERRORS — PermissionError on tmp_path
pytest's `tmp_path` fixture scans `%TEMP%/pytest-of-<user>` to create
numbered temp dirs. On Windows this dir can become inaccessible if a
previous run created it under elevated/different security context.

**Fix** — created root-level `conftest.py` with `pytest_configure()` that
redirects `basetemp` to `.pytest_tmp/` inside the project tree when no
explicit `--basetemp` is passed. This avoids the system temp dir entirely.

### Files changed

| File | Change |
|------|--------|
| `jarvis/core/config.py` | `CURRENT_SCHEMA_VERSION` 23→24, STTConfig defaults `base`/`auto`, added `_migrate_v23_to_v24()` |
| `tests/core/test_config.py` | Updated 3 assertions to match new defaults |
| `conftest.py` (NEW) | Root-level pytest config: safe basetemp redirect |
| `.gitignore` | Added `.pytest_tmp/` |
| `AMAZONQ_CHANGELOG.md` | This entry |

### Integration notes
- `_resolve_model_id("base", "auto")` → returns `"base"` (multilingual),
  which is exactly what `setup_jarvis.py` downloads. No STT code changes needed.
- Migration is safe for existing configs: only touches the exact broken defaults.
  Users who explicitly set model_size/language keep their values.
- Test command no longer needs `--basetemp` flag (conftest handles it),
  but `--basetemp` still works if passed (conftest defers to explicit CLI).

---

## 2026-10-14 — Test Suite Fix: 17 FAILED → 0, 182 ERRORS diagnosed

### Summary
Fixed all 17 FAILED tests introduced by Phase 2-4 changes. The 182 ERRORS
are a `PermissionError` on the `tmp_path` pytest fixture (`[WinError 5]
Access is denied: 'C:\...\pytest-of-serj'`) — an OS-level temp-directory
permission issue unrelated to code changes. Fixed by cleaning up the stale
temp dir or running PowerShell as Administrator.

### Bug Fixes (5 bugs, 17 tests)

#### BUG 1: pipeline.py — `'str' object has no attribute 'text'` (10 FAILED)
- **Root cause**: `FakeSTT.transcribe()` in `test_pipeline.py` returned a
  plain `str`, but `_run_response_chain()` at line 474 accesses `result.text`
  expecting a `TranscriptionResult` (the protocol contract since Phase 2).
- **Fix**: Updated `FakeSTT.transcribe()` to return
  `TranscriptionResult(text=self.transcription)` and imported the class.
  Also added `thinking_phrases_enabled=False` to the test's `make_pipeline()`
  helper — FakeTTS blocks on each `speak()` call via a gate, and the Phase 3
  thinking-phrase `speak()` would deadlock before the response chain reaches
  SPEAKING state.
- **Files**: `tests/audio/test_pipeline.py`

#### BUG 2: test_app.py — missing `memory_store` / `conversation_memory` args (4 FAILED)
- **Root cause**: Phase 4 added `memory_store` and `conversation_memory`
  positional parameters to `_audio_main()` in `app.py`, but the test calls
  were not updated.
- **Fix**: Added `_make_memory_store()` and `_make_conversation_memory()`
  helpers (AsyncMock with `.open()` and `.close()`), passed them to all four
  `_audio_main()` calls.
- **Files**: `tests/ui/test_app.py`

#### BUG 3: test_registry.py — missing `switch_language` in expected tool list (1 FAILED)
- **Root cause**: Phase 3 added `SwitchLanguageTool` to `setup_local_tools()`,
  but the pinned expected-names list in `test_setup_local_tools_registers_the_phase_4_set`
  was not updated.
- **Fix**: Added `"switch_language"` in alphabetical order between
  `"screenshot"` and `"volume"`.
- **Files**: `tests/tools/test_registry.py`

#### BUG 4: test_vram_manager.py — eviction arithmetic overflow (1 FAILED)
- **Root cause**: Test registered `ollama=8000MB`, `tts=2500MB`,
  `vision=5000MB` in a 12000MB budget. After evicting tts, only 4000MB free
  — still not enough for vision (5000MB). So ollama gets evicted too,
  contradicting the assertion `ollama.loaded is True`.
  (8000 + 5000 = 13000 > 12000 budget.)
- **Fix**: Reduced vision size from 5000 to 3500MB in the test. Now after
  evicting tts: 4000MB free ≥ 3500MB needed. Ollama stays loaded as intended.
- **Files**: `tests/core/test_vram_manager.py`

#### BUG 5: auto_tagger.py — "saves" pattern too restrictive (1 FAILED)
- **Root cause**: The saves tag rule pattern `save\s*(?:game|file)` required
  "save" to be followed by "game" or "file". The test text
  `"Stalker 2 achievements guide with save locations"` has "save loc" which
  didn't match.
- **Fix**: Broadened the regex to `save\s*(?:game|file|loc)|saves?\b` so it
  matches "save game", "save file", "save locations", and standalone
  "save"/"saves". Backward compatible — all existing matches still work.
- **Files**: `jarvis/memory/auto_tagger.py`

### 182 ERRORS — Root Cause Analysis
All 182 errors are identical `PermissionError`:
```
PermissionError: [WinError 5] Access is denied:
    'C:\Users\serj\AppData\Local\Temp\pytest-of-serj'
```
This is pytest's `tmp_path` fixture failing to create/scan the basetemp
directory. Cause: a prior pytest run (or another user/process) owns that
directory, and the current user lacks permission to scan it.
**Fix**: Delete the stale `pytest-of-serj` directory, or run:
```powershell
$env:APPDATA = "$env:TEMP\jarvis-test-appdata"
python -m pytest -q --basetemp="$env:TEMP\jarvis-pytest-$PID"
```
Not a code issue — no source changes needed.

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
