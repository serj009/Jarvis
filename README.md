<p align="center">
  <h1 align="center">🤖 J.A.R.V.I.S. — Local AI Voice Assistant</h1>
  <p align="center">
    Local-first, multilingual (EN / RU / UK) AI assistant for Windows — cloud providers, web research and integrations work only if you enable them<br/>
    Powered by <strong>Ollama + Qwen 3 8B</strong> · <strong>Whisper STT</strong> · <strong>Piper TTS</strong> · <strong>PySide6</strong><br/><br/>
    <code>500+ source files (incl. tests)</code> · <code>150K+ lines (incl. tests)</code> · <code>237+ tools</code> · <code>700+ voice patterns</code> · <code>5 321 tests</code>
  </p>
</p>

> **Based on [ndunl075/Jarvis](https://github.com/ndunl075/Jarvis) (MIT License).**
> Significantly extended with multilingual voice control, coding agent, chat panel, universal model provider, long-term memory, game mode, OCR, screen-change detection, sound effects, persistent reminders, self-learning from successful commands, tiered memory with contradiction tracking, and an optional self-evaluation critic.

---

## ✨ Features

| | Feature | Details |
|---|---|---|
| 🎤 | **Voice Control** | Whisper STT (GPU auto-detect), wake word "Hey Jarvis", VAD, barge-in, **700+ regex voice patterns** across EN / RU / UK — zero-LLM-latency instant commands |
| 🧠 | **AI Brain** | Ollama + **Qwen 3 8B** — fully local, no internet required |
| 🌐 | **Universal Model Provider** | Unified interface: **Ollama + OpenAI + Anthropic + Groq** — plug in GPT-4o, Claude, or Groq via API key |
| 🗣️ | **Voice Response** | Piper TTS, 3 languages (alan / dmitri / mykyta), auto-switches by detected language |
| 🎤 | **Multilingual STT** | Whisper `small` multilingual + confidence calibrated for RU/UK (not English-only) — Russian/Ukrainian speech is acted on, not endlessly "clarified" |
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
| 🛡️ | **4-Layer Defense** | Origin tracking (HMAC-signed voice input), personal-data leak scanner, prompt-injection / suspicious-tool-chain sanitizer, and a hash-chained encrypted audit log — unified at a single tool-call security gate |
| 🔑 | **Password Vault** | Crypto-grade credential store (Argon2id → AES-256-GCM → DPAPI). Auto-login to sites where the password **never enters the LLM context** |
| 🗄️ | **Encrypted Storage** | All user data at rest is DPAPI-encrypted — 11 JSON stores plus memory SQLite columns (text, tags, condensed) |
| 🧩 | **33 Integration Plugins** | Google Calendar, Gmail, Contacts, Drive, Telegram, GitHub, Spotify, YouTube, WhatsApp, Monobank, Notion, Trello, Slack, Home Assistant, Philips Hue, and 18 more — **111 integration tools**, all OFF by default, enable-gated on SDK presence |
| 🛠️ | **SkillForge** | Auto-generated skills from repeated actions: observe → analyze → propose → generate. 5-level sandbox (file isolation, AST validator, circuit breaker). 9 voice tools |
| 📜 | **Protocols** | User-defined voice macros — "Вечеринка" → play music + volume max. Simple regex + LLM modes. Checked BEFORE intent patterns |
| 📝 | **Notes** | Voice-captured markdown notes, append / read / delete by title |
| 🔎 | **Research** | DuckDuckGo + Ollama summary; deep research with planner/worker split; Ultra mode |
| 📄 | **Web Knowledge Extractor** | "Сохрани информацию со страницы" — LLM-structured extraction of any web page straight into long-term memory |
| 🌐 | **Browser Automation** | Playwright-driven scenarios: tabs, bookmarks, auto-login, page monitor/compare, translate, smart form-fill |
| 🧠 | **Long-term Memory** | SQLite + FTS5 full-text + optional FAISS vectors · **tiered** (hot/warm/cold/archive) · per-fact metadata (importance, confidence, source, status, expiry) · **contradiction tracking** (supersede + history, no silent overwrite) · integrity self-checks |
| 🎓 | **Self-Learning** | Learns from every successful command — frequent utterances become few-shot examples, usage stats, and personal aliases ("браузер" → chrome). Works for instant snap-commands too |
| 🧪 | **Self-Evaluation (opt-in)** | Optional external critic LLM periodically grades interaction traces and writes a report. **Off by default**, API key required, secrets stripped before anything leaves the PC — the local pipeline stays the default |
| 🎨 | **Settings UI** | **Sidebar navigation, 12 pages**: General, Voice, Models, Tools, Coding, Gaming, Commands, Hotkeys, Memory, Security, Advanced, About |
| 🖥️ | **Dashboard** | Live HUD: mode, CPU/RAM, mic level, models, uptime |
| 🎯 | **Command Palette** | `Ctrl+Shift+P` keyboard launcher for any command |
| 🔧 | **237+ Registered Tools** | 126+ local tools + 111 integration tools across 100+ modules — from bookmarks to RSS, from QR codes to Home Assistant |

---

## 💻 System Requirements

| | Minimum | Recommended |
|---|---------|-------------|
| **OS** | Windows 10/11 x64 | Windows 11 x64 |
| **Python** | ≥ 3.11 | 3.11+ |
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

> ⚡ **700+ instant voice patterns** match via regex — **zero LLM latency** for time, apps, screenshots, volume, reminders, and more.

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
jarvis/                     ← 300+ package files (500+ repo-wide), 150K+ lines
├── audio/                  — STT (Whisper), TTS (Piper), VAD, AEC, sound effects,
│                             wake word, voice registry
├── browser/                — Playwright browser automation
├── coding/                 — Coding Agent, 16 templates, 8-language toolchain,
│                             project map, code search, voice confirm
├── core/                   — Config (schema v39), lifecycle, VRAM manager,
│                             game mode, state machine, events, migrations
├── dev/                    — Developer utilities
├── integrations/           — 33 plugins (Google, Telegram, GitHub, Spotify,
│                             Home Assistant, etc.), registry, auth, wiring
├── knowledge/              — Knowledge base management
├── llm/                    — Ollama client, intent router (700+ patterns),
│                             conversation history, universal model provider
├── memory/                 — SQLite + FTS5 + optional FAISS · tiered storage,
│                             metadata, contradiction/history, integrity checks,
│                             self-learning, optional self-evaluation
├── personal_starter/       — Personalization & onboarding
├── platform/               — Windows integration, app discovery (206 aliases)
├── security/               — Sandbox + path whitelist + risk levels,
│                             4-layer defense (origin tracker, leak scanner,
│                             output sanitizer, audit log, tool guard),
│                             Argon2id/AES-256-GCM password vault
├── skills/                 — SkillForge (auto-generated skills), Protocols
│                             (user voice macros), sandbox, decomposer
├── tools/                  — 100+ tool files, 237+ registered tools, MCP client,
│   └── local/                risk levels, tool filter
├── ui/                     — PySide6: overlay orb, chat panel, coding panel,
│   └── settings/             dashboard, notes, research, deep research,
│       └── tabs/ (17)        clipboard history, command palette, help,
│                             onboarding, logs, settings (17 sidebar pages)
└── vision/                 — OCR (Tesseract), screen-change detector
```

### Key Subsystems

| Subsystem | Description |
|-----------|-------------|
| **Intent Router** | Hybrid pattern + LLM: 700+ regex patterns checked first (instant), fallback to LLM for unmatched queries |
| **VRAM Manager** | Monitors GPU memory, auto-unloads models for Game Mode, restores on exit |
| **Tool Registry** | MCP-compatible protocol, per-tool risk levels, sandbox confirmations |
| **Config v39** | Pydantic schema with automatic migrations across 39 versions |
| **Audio Pipeline** | Whisper → VAD → Wake Word → STT → TTS pipeline with optional AEC |

---

## 🔐 Privacy & Security

- **Everything runs locally** — voice audio never leaves your PC
- **No analytics, no telemetry, no crash reporting**
- **4-layer defense** against a manipulated/compromised model: HMAC-signed voice
  origin, personal-data leak scanning on outbound URLs, prompt-injection and
  suspicious-tool-chain sanitizing, and a hash-chained encrypted audit log — all
  enforced at one tool-call security gate
- **Password vault**: Argon2id → AES-256-GCM → DPAPI; decrypted passwords live only
  in RAM and never enter the LLM context
- **Encrypted at rest**: all user data (11 JSON stores + memory SQLite columns) and
  any optional API keys are encrypted with Windows DPAPI
- Notes, research, screenshots, logs — all stored locally in `%APPDATA%\Jarvis\`

| What goes online | When |
|---|---|
| DuckDuckGo search | Only during `research` / `deep research` commands |
| Open-Meteo weather | Only when you ask for weather |
| OpenAI / Anthropic / Groq | Only if you configure API keys and select a cloud model |
| Brave / Jina | Only in Ultra research mode with your own API keys |
| Self-evaluation critic | Only if you explicitly enable it in Settings and supply an API key — interaction traces (secrets stripped) are sent to the critic you choose. Off by default |

---

## 🧪 Testing

```powershell
# Run the full test suite (5321 tests)
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
pytest                    # Test (5321 tests)
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
