<p align="center">
  <h1 align="center">🤖 J.A.R.V.I.S. — Local AI Voice Assistant</h1>
  <p align="center">
    Fully offline, multilingual (EN / RU / UK) AI assistant for Windows<br/>
    Powered by <strong>Ollama + Qwen 3 8B</strong> · <strong>Whisper STT</strong> · <strong>Piper TTS</strong> · <strong>PySide6</strong><br/><br/>
    <code>465 source files</code> · <code>131K lines</code> · <code>126+ tools</code> · <code>643 voice patterns</code> · <code>4 374 tests</code>
  </p>
</p>

> **Based on [ndunl075/Jarvis](https://github.com/ndunl075/Jarvis) (MIT License).**
> Significantly extended with multilingual voice control, coding agent, chat panel, universal model provider, long-term memory, game mode, OCR, screen-change detection, sound effects, persistent reminders, and more.

---

## ✨ Features

| | Feature | Details |
|---|---|---|
| 🎤 | **Voice Control** | Whisper STT (GPU auto-detect), wake word "Hey Jarvis", VAD, barge-in, **643 regex voice patterns** across EN / RU / UK — zero-LLM-latency instant commands |
| 🧠 | **AI Brain** | Ollama + **Qwen 3 8B** — fully local, no internet required |
| 🌐 | **Universal Model Provider** | Unified interface: **Ollama + OpenAI + Anthropic + Groq** — plug in GPT-4o, Claude, or Groq via API key |
| 🗣️ | **Voice Response** | Piper TTS, 3 languages (alan / dmitri / mykyta), auto-switches by detected language |
| 💬 | **Chat Panel** | Text-based alternative to voice — full chat UI with history and markdown rendering |
| 💻 | **PC Control** | Open / close apps (206 aliases), system stats, lock screen, volume, dictation, hotkey sender |
| 🛠️ | **Coding Agent** | Autonomous **plan → create → edit → test → fix → git** loop, 16 project templates, 8 languages, voice confirmation for destructive actions |
| 🎮 | **Game Mode** | Automatic VRAM management — unloads models for gaming, restores on exit |
| 🎵 | **Media** | YouTube Music playback, media controls, Steam game launcher |
| 🌤️ | **Weather** | Open-Meteo, any city, no API key needed |
| 🕐 | **Time & Reminders** | Instant time/date response + **persistent time-based reminders** that survive restarts |
| 📋 | **Clipboard** | Read / clear / history panel, pin items, credential-aware filtering |
| 📸 | **Screenshots** | Regular + full-page scrolling screenshots, save + copy to clipboard |
| 👁️ | **Vision** | Screen reading via local multimodal model (LLaVA) |
| 🔍 | **OCR** | Tesseract-based text extraction from screen regions |
| 📡 | **Screen-Change Detector** | Watch screen regions for visual changes, trigger alerts |
| 🔊 | **Sound Effects** | Auditory feedback for actions (listen/think/speak/error states) |
| 🔐 | **Sandbox** | Whitelist paths, risk levels, **voice confirmation for destructive commands** |
| 📝 | **Notes** | Voice-captured markdown notes, append / read / delete by title |
| 🔎 | **Research** | DuckDuckGo + Ollama summary; deep research with planner/worker split; Ultra mode |
| 🧠 | **Long-term Memory** | SQLite + FTS5 full-text search + FAISS vector embeddings |
| 🎨 | **Settings UI** | **Sidebar navigation, 12 pages**: General, Voice, Models, Tools, Coding, Gaming, Commands, Hotkeys, Memory, Security, Advanced, About |
| 🖥️ | **Dashboard** | Live HUD: mode, CPU/RAM, mic level, models, uptime |
| 🎯 | **Command Palette** | `Ctrl+Shift+P` keyboard launcher for any command |
| 🔧 | **126+ Registered Tools** | 82 tool files across 89 modules — from bookmarks to RSS, from QR codes to disk cleaner |

---

## 💻 System Requirements

| | Minimum | Recommended |
|---|---------|-------------|
| **OS** | Windows 10/11 x64 | Windows 11 x64 |
| **Python** | ≥ 3.10 | 3.11+ |
| **RAM** | 8 GB | **12+ GB** |
| **GPU** | None (CPU mode) | NVIDIA RTX 3060+ (**8+ GB VRAM**) |
| **Disk** | ~3 GB | ~10 GB (with Ollama models) |
| **Ollama** | **Required** — [ollama.com](https://ollama.com/download) | |

> 💡 NVIDIA GPU dramatically speeds up both Whisper STT and Ollama inference. CPU-only works but is slower.

---

## 🚀 Quick Start

```powershell
# 1. Install Ollama and pull the model
ollama pull qwen3:8b

# 2. Clone and setup
git clone https://github.com/serj009/Jarvis.git
cd Jarvis
git checkout amazonq

# 3. One-command bootstrap (venv, deps, voices, models)
python setup_jarvis.py

# 4. Run
.\start-jarvis.ps1
# or:
python -m jarvis
```

Say **"Hey Jarvis"** and you're live. 🎙️

### Setup options

```powershell
python setup_jarvis.py --whisper-model small    # Better accuracy (default: base)
python setup_jarvis.py --skip-smoke-test        # Skip verification
```

### Optional: AEC (echo cancellation for speakers)

```powershell
.venv\Scripts\pip install -e ".[aec]"
```

---

## 🗣️ Voice Commands Examples

| Language | Commands |
|----------|----------|
| 🇷🇺 RU | "Открой хром" · "Какая погода?" · "Сколько времени?" |
| 🇷🇺 RU | "Включи музыку джаз" · "Запусти сталкер в стиме" |
| 🇷🇺 RU | "Скриншот" · "Заблокируй экран" · "Открой заметки" |
| 🇷🇺 RU | "Напомни в 15:00 позвонить" · "Создай проект на Python" |
| 🇺🇦 UK | "Відкрий браузер" · "Яка погода?" · "Котра година?" |
| 🇬🇧 EN | "Open Chrome" · "What's the weather?" · "What time is it?" |
| 🇬🇧 EN | "Play some jazz" · "Launch Stalker on Steam" |
| 🇬🇧 EN | "Screenshot" · "Lock screen" · "Take a note about..." |
| 🇬🇧 EN | "Remind me at 3pm to call" · "Create a Python project" |
| 🔎 | "Research quantum computing" · "Deep research on AI trends" |
| 🛠️ | "Code a Flask API" · "Fix the tests" · "Git commit" |

> ⚡ **643 instant voice patterns** match via regex — **zero LLM latency** for time, apps, screenshots, volume, reminders, and more.

---

## 🌐 API Models — Universal Model Provider

Jarvis defaults to **Ollama (local)**, but you can plug in cloud models for the Coding Agent or any subsystem:

| Provider | Example Models | How to Enable |
|----------|---------------|---------------|
| **Ollama** (default) | `qwen3:8b`, `llama3.1`, `codellama` | Runs automatically |
| **OpenAI** | `gpt-4o`, `gpt-4o-mini` | Settings → Models → OpenAI API key |
| **Anthropic** | `claude-3.5-sonnet`, `claude-3-haiku` | Settings → Models → Anthropic API key |
| **Groq** | `llama-3.1-70b-versatile` | Settings → Models → Groq API key |

All providers use **httpx directly** (no vendor SDKs) for minimal dependencies. API keys are encrypted with **Windows DPAPI**.

```
Settings → Models → Coding Model Provider → select provider & model
```

---

## 🛠️ Coding Agent

The built-in Coding Agent operates as an autonomous loop:

```
Plan → Create files → Edit → Run tests → Fix errors → Git commit
```

### Capabilities
- **8 programming languages**: Python, JavaScript, TypeScript, C#, Java, Go, Rust, C/C++
- **16 project templates**: Flask API, FastAPI, React App, Discord Bot, Telegram Bot, CLI Tool, Data Analysis, Pygame Game, Scraper, Automation, C# Console, Go Basic, Rust Basic, JS Node, TS Node, Python Basic
- **Voice confirmation** for destructive actions (file deletion, git force-push)
- Per-language **build / test / lint / validate** toolchain configuration
- Project map generation and code search
- Agent notepad for tracking task state
- Dedicated **Coding Panel** in the UI with real-time progress

---

## 🏗️ Architecture Overview

```
jarvis/                     ← 465 source files, 131K lines
├── audio/                  — STT (Whisper), TTS (Piper), VAD, AEC, sound effects,
│                             wake word, voice registry
├── browser/                — Playwright browser automation
├── coding/                 — Coding Agent, 16 templates, 8-language toolchain,
│                             project map, code search, voice confirm
├── core/                   — Config (schema v33), lifecycle, VRAM manager,
│                             game mode, state machine, events, migrations
├── dev/                    — Developer utilities
├── knowledge/              — Knowledge base management
├── llm/                    — Ollama client, intent router (643 patterns),
│                             conversation history, universal model provider
├── memory/                 — SQLite + FTS5 + FAISS vector embeddings
├── personal_starter/       — Personalization & onboarding
├── platform/               — Windows integration, app discovery (206 aliases)
├── security/               — Sandbox, path whitelist, risk levels
├── tools/                  — 82 tool files, 126+ registered tools, MCP client,
│   └── local/                risk levels, tool filter
├── ui/                     — PySide6: overlay orb, chat panel, coding panel,
│   └── settings/             dashboard, notes, research, deep research,
│       └── tabs/ (12)        clipboard history, command palette, help,
│                             onboarding, logs, settings (12 sidebar pages)
└── vision/                 — OCR (Tesseract), screen-change detector
```

### Key Subsystems

| Subsystem | Description |
|-----------|-------------|
| **Intent Router** | Hybrid pattern + LLM: 643 regex patterns checked first (instant), fallback to LLM for unmatched queries |
| **VRAM Manager** | Monitors GPU memory, auto-unloads models for Game Mode, restores on exit |
| **Tool Registry** | MCP-compatible protocol, per-tool risk levels, sandbox confirmations |
| **Config v33** | Pydantic schema with automatic migrations across 33 versions |
| **Audio Pipeline** | Whisper → VAD → Wake Word → STT → TTS pipeline with optional AEC |

---

## 🔐 Privacy & Security

- **Everything runs locally** — voice audio never leaves your PC
- **No analytics, no telemetry, no crash reporting**
- API keys (optional) encrypted with Windows DPAPI
- Notes, research, screenshots, logs — all stored locally in `%APPDATA%\Jarvis\`

| What goes online | When |
|---|---|
| DuckDuckGo search | Only during `research` / `deep research` commands |
| Open-Meteo weather | Only when you ask for weather |
| OpenAI / Anthropic / Groq | Only if you configure API keys and select a cloud model |
| Brave / Jina | Only in Ultra research mode with your own API keys |

---

## 🧪 Testing

```powershell
# Run the full test suite (4374 tests)
pytest

# Run with coverage
pytest --cov=jarvis

# Quick smoke test
python smoke_test.py
```

---

## 🛠️ Development

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m jarvis          # Run
pytest                    # Test (4374 tests)
ruff check .              # Lint
```

### Build portable zip

```powershell
pip install -e ".[dev]"
.\build.ps1
# → dist\Jarvis-<version>-windows-x64.zip
```

---

## 📄 License

MIT License.

Based on [ndunl075/Jarvis](https://github.com/ndunl075/Jarvis) — original project by [@ndunl075](https://github.com/ndunl075).

See [LICENSE](LICENSE) for details.
