<p align="center">
  <h1 align="center">🤖 J.A.R.V.I.S. — Local AI Voice Assistant</h1>
  <p align="center">
    Fully offline, multilingual (RU / UK / EN) AI assistant for Windows<br/>
    Powered by <strong>Ollama + Qwen 2.5 7B</strong> · <strong>Whisper STT</strong> · <strong>Piper TTS</strong> · <strong>PySide6</strong>
  </p>
</p>

> **Based on [ndunl075/Jarvis](https://github.com/ndunl075/Jarvis) (MIT License).**
> Significantly extended with multilingual support, long-term memory, VRAM management, sandbox, instant commands, settings UI, and more.

---

## ✨ Features

| | Feature | Details |
|---|---|---|
| 🎤 | **Voice Control** | Whisper STT (`small`, GPU auto-detect), wake word "Hey Jarvis", VAD, barge-in |
| 🧠 | **AI Brain** | Ollama + Qwen 2.5 7B — fully local, no internet required |
| 🗣️ | **Voice Response** | Piper TTS, 3 languages (alan / dmitri / mykyta), auto-switches by detected language |
| 💻 | **PC Control** | Open / close apps (206 aliases), system stats, lock screen, volume, dictation |
| 🎵 | **Media** | YouTube Music playback, Steam game launcher |
| 🌤️ | **Weather** | Open-Meteo, any city, no API key needed |
| 🕐 | **Time & Date** | Instant local response, zero LLM latency |
| 📋 | **Clipboard** | Read / clear / history panel, pin items, credential-aware filtering |
| 📸 | **Screenshots** | Save + copy to clipboard |
| 👁️ | **Vision** | Screen reading via local multimodal model (LLaVA) |
| 🔒 | **Sandbox** | Whitelist paths, risk levels, confirmations for dangerous commands |
| 📝 | **Notes** | Voice-captured markdown notes, append / read / delete by title |
| 🔍 | **Research** | DuckDuckGo + Ollama summary; deep research with planner/worker split |
| 🧠 | **Long-term Memory** | SQLite + FTS5 full-text search + FAISS vector embeddings |
| ⚡ | **Instant Commands** | 90+ regex voice patterns across RU / UK / EN — no LLM round-trip |
| 🎨 | **Settings UI** | Card-based panels with hints, emoji, per-tool toggles |
| 🖥️ | **Dashboard** | Live HUD: mode, CPU/RAM, mic level, models, uptime |
| 🎯 | **Command Palette** | `Ctrl+Shift+P` keyboard launcher for any command |

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

```bash
# 1. Install Ollama and pull the model
ollama pull qwen3:8b

# 2. Clone and setup
git clone https://github.com/serj009/Jarvis.git
cd Jarvis
git checkout amazonq

# 3. One-command bootstrap (venv, deps, voices, models)
python setup_jarvis.py

# 4. Run
python -m jarvis
```

Say **"Hey Jarvis"** and you're live. 🎙️

### Setup options

```bash
python setup_jarvis.py --whisper-model small    # Better accuracy (default: base)
python setup_jarvis.py --skip-smoke-test        # Skip verification
```

### Optional: AEC (echo cancellation for speakers)

```bash
.venv\Scripts\pip install -e ".[aec]"
```

---

## 🗣️ Example Commands

| Language | Command |
|----------|---------|
| 🇷🇺 RU | "Открой хром" · "Какая погода?" · "Сколько времени?" |
| 🇷🇺 RU | "Включи музыку джаз" · "Запусти сталкер в стиме" |
| 🇷🇺 RU | "Скриншот" · "Заблокируй экран" · "Открой заметки" |
| 🇺🇦 UK | "Відкрий браузер" · "Яка погода?" · "Котра година?" |
| 🇬🇧 EN | "Open Chrome" · "What's the weather?" · "What time is it?" |
| 🇬🇧 EN | "Play some jazz" · "Launch Stalker on Steam" |
| 🇬🇧 EN | "Screenshot" · "Lock screen" · "Take a note about..." |
| 🔍 | "Research quantum computing" · "Deep research on AI trends" |

> ⚡ Instant commands (time, apps, screenshots, etc.) use regex — **zero LLM latency**.

---

## 📁 Project Structure

```
jarvis/
├── audio/          — STT (Whisper), TTS (Piper), VAD, audio pipeline, AEC
├── core/           — Config, lifecycle, VRAM manager, logging
├── llm/            — Ollama client, intent router, conversation history
├── memory/         — SQLite store, FAISS embeddings, entity relations
├── platform/       — Windows integration, app discovery (206 aliases)
├── tools/          — 20+ tools, sandbox, risk levels, confirmations
├── ui/             — PySide6 overlay, settings, panels, dashboard
└── resources/      — Voices, wake word models, assets
```

---

## 🔒 Privacy & Security

- **Everything runs locally** — voice audio never leaves your PC
- **No analytics, no telemetry, no crash reporting**
- API keys (optional, for Ultra research) encrypted with Windows DPAPI
- Notes, research, screenshots, logs — all stored locally in `%APPDATA%\Jarvis\`

| What goes online | When |
|---|---|
| DuckDuckGo search | Only during `research` / `deep research` commands |
| Open-Meteo weather | Only when you ask for weather |
| Brave / Groq / Jina | Only in Ultra mode with your own API keys |

---

## 🛠️ Development

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m jarvis          # Run
pytest                    # Test
ruff check .              # Lint
```

### Build portable zip

```bash
pip install -e ".[dev]"
.\build.ps1
# → dist\Jarvis-<version>-windows-x64.zip
```

---

## 📄 License

MIT License.

Based on [ndunl075/Jarvis](https://github.com/ndunl075/Jarvis) — original project by [@ndunl075](https://github.com/ndunl075).

See [LICENSE](LICENSE) for details.
