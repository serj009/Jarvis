# CREDITS — Third-Party Dependencies

This file lists the third-party open-source components used by JARVIS,
their licenses, and how each is used in the project.

---

## faster-whisper

| | |
|---|---|
| **URL** | <https://github.com/SYSTRAN/faster-whisper> |
| **License** | MIT |
| **Usage** | Speech-to-text (STT) engine. Provides GPU-accelerated multilingual transcription (UA/RU/EN) via CTranslate2. Configured in `jarvis/audio/stt.py`; model size and device set in `config.stt`. |

## piper-tts

| | |
|---|---|
| **URL** | <https://github.com/rhasspy/piper> |
| **License** | MIT |
| **Usage** | Text-to-speech (TTS) backend. Synthesises spoken responses using lightweight ONNX voice models. Per-language voices are selected automatically based on detected language (`jarvis/audio/tts.py`). |

## Silero VAD

| | |
|---|---|
| **URL** | <https://github.com/snakers4/silero-vad> |
| **License** | MIT |
| **Usage** | Voice Activity Detection. Detects when the user starts and stops speaking so JARVIS records only intentional speech, not ambient noise (`jarvis/audio/vad.py`). |

## openwakeword

| | |
|---|---|
| **URL** | <https://github.com/dscripka/openwakeword> |
| **License** | Apache 2.0 |
| **Usage** | Wake-word detection engine. Listens for the "Hey Jarvis" trigger phrase to activate the assistant from standby mode (`jarvis/audio/wakeword.py`). |

## Ollama

| | |
|---|---|
| **URL** | <https://github.com/ollama/ollama> |
| **License** | MIT |
| **Usage** | Local LLM inference server. Hosts the conversational model (default: `qwen3:8b`) and vision model (`llava:7b`). JARVIS communicates with Ollama over its HTTP API (`jarvis/llm/ollama_client.py`). |

## PySide6

| | |
|---|---|
| **URL** | <https://doc.qt.io/qtforpython-6/> |
| **License** | LGPLv3 |
| **Usage** | GUI framework. Provides the main application window, system tray icon, settings panel, overlay HUD, research panel, and all other UI elements (`jarvis/ui/`). |

## pynput

| | |
|---|---|
| **URL** | <https://github.com/moses-palmer/pynput> |
| **License** | LGPLv3 |
| **Usage** | Global hotkey listener. Captures keyboard shortcuts (mute toggle, push-to-talk, open settings, command palette) without requiring window focus (`jarvis/platform/hotkeys.py`). |

## sentence-transformers

| | |
|---|---|
| **URL** | <https://github.com/UKPLab/sentence-transformers> |
| **License** | Apache 2.0 |
| **Usage** | Embedding model for the memory subsystem. Encodes stored facts into dense vectors for semantic search via FAISS. Uses the `paraphrase-multilingual-MiniLM-L12-v2` model (`jarvis/memory/`). |

## FAISS

| | |
|---|---|
| **URL** | <https://github.com/facebookresearch/faiss> |
| **License** | MIT |
| **Usage** | Vector similarity search library. Indexes memory embeddings for fast approximate nearest-neighbour lookup when retrieving contextually relevant facts (`jarvis/memory/`). |

## pyautogui

| | |
|---|---|
| **URL** | <https://github.com/asweigart/pyautogui> |
| **License** | BSD (3-Clause) |
| **Usage** | Desktop automation. Powers the `type_into_active_window` tool for simulating keystrokes and the `see_screen` screenshot capture (`jarvis/tools/`). |

## httpx

| | |
|---|---|
| **URL** | <https://github.com/encode/httpx> |
| **License** | BSD (3-Clause) |
| **Usage** | Async HTTP client. Used for Ollama API communication, web research page fetching, weather API calls, and MCP server connections throughout the project. |

## psutil

| | |
|---|---|
| **URL** | <https://github.com/giampaolo/psutil> |
| **License** | BSD (3-Clause) |
| **Usage** | System process monitoring. Detects running games for Game Mode VRAM management, checks if Ollama is already running, and monitors system resource usage (`jarvis/tools/`, `jarvis/core/`). |

## yt-dlp

| | |
|---|---|
| **URL** | <https://github.com/yt-dlp/yt-dlp> |
| **License** | Unlicense |
| **Usage** | YouTube audio/video extraction. Powers the `play_youtube_music` tool — resolves search queries to YouTube URLs and streams audio playback (`jarvis/tools/`). |

## pytesseract

| | |
|---|---|
| **URL** | <https://github.com/madmaze/pytesseract> |
| **License** | Apache 2.0 |
| **Usage** | OCR (Optical Character Recognition). Extracts text from screenshots and images for the `see_screen` tool's text-extraction fallback path (`jarvis/tools/`). |

---

## Upstream Fork

This project is a fork and further development of:

- **ndunl075/Jarvis** — <https://github.com/ndunl075/Jarvis> (MIT License)
