"""Game Mode — intelligent VRAM management when games are running.

When a resource-heavy game is detected, JARVIS transitions to lite mode:
1. Evicts Ollama LLM from VRAM (keep_alive=0)
2. Switches STT from GPU to CPU (tiny model for minimal wake-word + basic commands)
3. Preloads game-specific memories into RAM for instant answers
4. Monitors game process — restores full mode when game exits

Game Classification:
  MAIN: full 3D games that need GPU VRAM (Elden Ring, Stalker 2, Cyberpunk)
  IDLE: lightweight clickers/idle games that use minimal GPU (Cookie Clicker, Auto Clicker)

MAIN games trigger game mode. IDLE games are ignored.

Detection: periodic process scan (every 30s) matching against:
  1. Steam library (already scanned at startup in windows_apps.py)
  2. Known IDLE game patterns (configurable blacklist)
  3. Custom MAIN game executables (user-configured)

Preloaded Game Context:
  When game mode activates, search long-term memory for ALL facts
  related to the game title. Store as a preloaded text block that
  can be used for answering questions WITHOUT the LLM — just
  pattern-matched retrieval from the preloaded facts.

  For complex questions that need LLM reasoning, temporarily load
  Ollama, answer, and unload again (slower but possible).

Integration:
  - app.py creates GameMode after scanning installed apps
  - IntentRouter checks game_mode.is_active and injects preloaded_context
  - AudioPipeline uses game mode thinking phrases for the slow LLM path
  - VRAMManager.release_all() is leveraged for full GPU eviction

Thread safety:
  All public methods are async. The scan loop runs as an asyncio.Task
  on the audio event loop. psutil process iteration runs in
  asyncio.to_thread to avoid blocking the frame loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.audio.stt import FasterWhisperSTT
    from jarvis.core.vram_manager import VRAMManager
    from jarvis.llm.ollama_client import OllamaClient
    from jarvis.memory.store import MemoryStore

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Game classification
# ---------------------------------------------------------------------------

class GameCategory(Enum):
    """How much GPU VRAM a game is expected to consume."""
    MAIN = "main"        # Full 3D game, needs VRAM — triggers game mode
    IDLE = "idle"        # Lightweight clicker/idle — ignored
    UNKNOWN = "unknown"  # Not recognized as a game


@dataclass
class DetectedGame:
    """A running game process."""
    name: str               # Process name (e.g. "eldenring.exe")
    title: str              # Friendly name (e.g. "Elden Ring")
    category: GameCategory
    pid: int
    started_at: datetime = field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Known IDLE/clicker game patterns — matched against process stem (lowercase)
# ---------------------------------------------------------------------------

DEFAULT_IDLE_GAME_PATTERNS: frozenset[str] = frozenset({
    "cookie", "clicker", "idle", "autoclicker", "auto_clicker",
    "autoclick", "clickerheroes", "adventurecapitalist",
    "realm_grinder", "ngu_idle", "antimatter", "melvor",
    "idleminer", "idle_slayer", "taptitans", "egg_inc",
})


# ---------------------------------------------------------------------------
# GameMode
# ---------------------------------------------------------------------------

class GameMode:
    """Manages game mode lifecycle: detection, activation, deactivation.

    Implements a Loadable-compatible interface (name, is_loaded, load,
    unload) so it can optionally participate in LifecycleManager, but
    it is NOT registered as a Loadable by default — its own start/stop
    monitoring is driven by app.py independently.
    """

    name: str = "game_mode"

    def __init__(
        self,
        *,
        vram_manager: VRAMManager | None = None,
        ollama_client: OllamaClient | None = None,
        stt: FasterWhisperSTT | None = None,
        memory_store: MemoryStore | None = None,
        scan_interval_seconds: float = 30.0,
        steam_games: dict | None = None,
        idle_game_patterns: frozenset[str] | None = None,
        custom_main_games: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        self._vram = vram_manager
        self._ollama = ollama_client
        self._stt = stt
        self._memory = memory_store
        self._scan_interval = scan_interval_seconds
        # steam_games: dict[normalized_name, SteamGame] from scan_steam_games()
        self._steam_games: dict = steam_games or {}
        self._idle_patterns: frozenset[str] = (
            idle_game_patterns if idle_game_patterns is not None
            else DEFAULT_IDLE_GAME_PATTERNS
        )
        # Custom MAIN game process names (lowercase stems without .exe)
        # for standalone games not in Steam (e.g. "cyberpunk2077", "witcher3").
        self._custom_main_games: set[str] = {
            g.lower().replace(".exe", "").strip()
            for g in (custom_main_games or [])
        }
        self._enabled = enabled

        # --- runtime state ---
        self._active = False
        self._current_game: DetectedGame | None = None
        self._preloaded_context: str = ""
        self._game_start_time: datetime | None = None
        self._scan_task: asyncio.Task | None = None

        # State before game mode (to restore)
        self._pre_game_stt_device: str = "auto"
        self._pre_game_stt_model: str = "small"
        self._pre_game_stt_compute: str = "auto"

    # -- public properties --------------------------------------------------

    @property
    def is_active(self) -> bool:
        """True when game mode is engaged (models evicted, lite STT)."""
        return self._active

    @property
    def current_game(self) -> DetectedGame | None:
        return self._current_game

    @property
    def preloaded_context(self) -> str:
        """Game-specific memories preloaded for instant answers."""
        return self._preloaded_context

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        if not value:
            self.stop_monitoring()

    # -- monitoring lifecycle -----------------------------------------------

    def start_monitoring(self) -> None:
        """Start background game detection loop."""
        if not self._enabled:
            log.info("game mode: disabled in config, skipping monitor start")
            return
        if self._scan_task is None or self._scan_task.done():
            self._scan_task = asyncio.create_task(
                self._monitor_loop(), name="game-mode-monitor"
            )
            log.info(
                "game mode: monitoring started (scan every %.0fs)",
                self._scan_interval,
            )

    def stop_monitoring(self) -> None:
        """Stop background monitoring."""
        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
            log.info("game mode: monitoring stopped")

    # -- force activate / deactivate (voice commands) -----------------------

    async def force_activate(self, game_title: str = "Manual") -> None:
        """Force-activate game mode without process detection.

        Used by voice command: "включи игровой режим" / "game mode on".
        """
        if self._active:
            log.info("game mode: already active")
            return
        game = DetectedGame(
            name="manual",
            title=game_title,
            category=GameCategory.MAIN,
            pid=0,
        )
        await self._activate(game)

    async def force_deactivate(self) -> None:
        """Force-deactivate game mode.

        Used by voice command: "выключи игровой режим" / "game mode off".
        """
        if not self._active:
            log.info("game mode: not active")
            return
        await self._deactivate()

    # -- monitoring loop ----------------------------------------------------

    async def _monitor_loop(self) -> None:
        """Periodically scan for running games."""
        while True:
            try:
                await self._scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("game mode: scan failed", exc_info=True)
            await asyncio.sleep(self._scan_interval)

    async def _scan_once(self) -> None:
        """One scan iteration: detect games, activate/deactivate as needed.

        Process iteration is CPU-bound (psutil walks /proc or
        NtQuerySystemInformation), so it runs in a thread to avoid
        blocking the audio frame loop.
        """
        running_games = await asyncio.to_thread(self._scan_processes)

        if running_games and not self._active:
            # Pick the first detected MAIN game
            main_game = running_games[0]
            await self._activate(main_game)
        elif self._active and self._current_game is not None:
            # Check if the current game is still running
            still_running = any(
                g.pid == self._current_game.pid for g in running_games
            )
            if not still_running:
                # Also check by name in case PID recycled
                still_running = any(
                    g.name.lower() == self._current_game.name.lower()
                    for g in running_games
                )
            if not still_running:
                await self._deactivate()

    def _scan_processes(self) -> list[DetectedGame]:
        """Synchronous process scan. Runs in asyncio.to_thread."""
        try:
            import psutil
        except ImportError:
            log.debug("psutil not available; game detection disabled")
            return []

        running_games: list[DetectedGame] = []

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = proc.info.get("name", "")
                if not name:
                    continue
                name_lower = name.lower()
                stem = name_lower.replace(".exe", "")

                category = self._classify_process(stem)

                if category == GameCategory.IDLE:
                    continue  # Skip idle games entirely

                if category == GameCategory.MAIN:
                    title = self._resolve_game_title(stem)
                    running_games.append(DetectedGame(
                        name=name,
                        title=title,
                        category=category,
                        pid=proc.info["pid"],
                    ))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return running_games

    # -- classification -----------------------------------------------------

    def _classify_process(self, stem: str) -> GameCategory:
        """Classify a process stem as MAIN, IDLE, or UNKNOWN.

        Priority:
          1. IDLE pattern match → IDLE (never triggers game mode)
          2. Custom MAIN games list → MAIN
          3. Steam library match → MAIN
          4. Otherwise → UNKNOWN (not a game)
        """
        # 1. Check IDLE patterns first — they always win
        for pattern in self._idle_patterns:
            if pattern in stem:
                return GameCategory.IDLE

        # 2. Custom MAIN games (user-configured standalone executables)
        if stem in self._custom_main_games:
            return GameCategory.MAIN

        # 3. Check against Steam library
        #    steam_games is dict[normalized_name, SteamGame]
        for _key, game in self._steam_games.items():
            game_name = getattr(game, "name", "")
            if not game_name:
                continue
            # Compare process stem against the game name (spaces stripped)
            game_normalized = game_name.lower().replace(" ", "").replace("-", "").replace("_", "")
            stem_normalized = stem.replace("-", "").replace("_", "")
            if (
                stem_normalized in game_normalized
                or game_normalized in stem_normalized
            ):
                return GameCategory.MAIN

        return GameCategory.UNKNOWN

    def _resolve_game_title(self, stem: str) -> str:
        """Try to get a friendly game title from process stem."""
        # Check custom games first
        if stem in self._custom_main_games:
            return stem.replace("_", " ").replace("-", " ").title()

        # Check Steam library
        for _key, game in self._steam_games.items():
            game_name = getattr(game, "name", "")
            if not game_name:
                continue
            game_normalized = game_name.lower().replace(" ", "").replace("-", "").replace("_", "")
            stem_normalized = stem.replace("-", "").replace("_", "")
            if stem_normalized in game_normalized or game_normalized in stem_normalized:
                return game_name

        # Fallback: capitalize process stem
        return stem.replace("_", " ").replace("-", " ").title()

    # -- activation ---------------------------------------------------------

    async def _activate(self, game: DetectedGame) -> None:
        """Enter game mode: evict models from VRAM, switch STT to CPU, preload memories."""
        log.info(
            "game mode: ACTIVATING for '%s' (pid=%d, category=%s)",
            game.title, game.pid, game.category.value,
        )

        self._current_game = game
        self._game_start_time = datetime.now()
        self._active = True

        # 1. Evict Ollama from VRAM (keep_alive=0)
        if self._ollama is not None:
            try:
                await self._ollama.unload()
                log.info("game mode: Ollama evicted from VRAM")
            except Exception:
                log.warning("game mode: failed to evict Ollama", exc_info=True)

        # 2. Switch STT to CPU tiny model (minimal wake-word + basic commands)
        if self._stt is not None:
            try:
                # Save current state for restoration
                self._pre_game_stt_device = getattr(
                    self._stt, "_configured_device", "auto"
                )
                self._pre_game_stt_model = self._stt.model_size
                self._pre_game_stt_compute = getattr(
                    self._stt, "compute_type", "auto"
                )

                # Unload current GPU model
                await self._stt.unload()

                # Reconfigure for CPU tiny
                self._stt.model_size = "tiny"
                self._stt._configured_device = "cpu"
                self._stt.compute_type = "int8"

                # Load the tiny CPU model
                await self._stt.load()
                log.info("game mode: STT switched to CPU tiny (int8)")
            except Exception:
                log.warning("game mode: STT switch failed", exc_info=True)

        # 3. Release tracked VRAM consumers (GPU memory for games)
        if self._vram is not None:
            try:
                await self._vram.release_all()
                log.info("game mode: VRAM consumers released")
            except Exception:
                log.warning("game mode: VRAM release failed", exc_info=True)

        # 4. Preload game memories into RAM (fire-and-forget friendly)
        asyncio.create_task(
            self._preload_game_context(game.title),
            name=f"game-preload-{game.title}",
        )

    # -- deactivation -------------------------------------------------------

    async def _deactivate(self) -> None:
        """Exit game mode: restore STT, log session to memory."""
        if self._current_game is None:
            return

        game = self._current_game
        duration = datetime.now() - (self._game_start_time or datetime.now())
        total_seconds = duration.total_seconds()
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)

        log.info(
            "game mode: DEACTIVATING after %dh %dm (game: %s)",
            hours, minutes, game.title,
        )

        # 1. Restore STT to pre-game configuration
        if self._stt is not None:
            try:
                await self._stt.unload()
                self._stt.model_size = self._pre_game_stt_model
                self._stt._configured_device = self._pre_game_stt_device
                self._stt.compute_type = self._pre_game_stt_compute
                await self._stt.load()
                log.info(
                    "game mode: STT restored to %s/%s/%s",
                    self._pre_game_stt_model,
                    self._pre_game_stt_device,
                    self._pre_game_stt_compute,
                )
            except Exception:
                log.warning("game mode: STT restore failed", exc_info=True)

        # 2. Ollama will lazy-load on next request (warm() is called
        #    automatically on first inference). No explicit load needed.
        #    The VRAM request will happen through the VRAMManager when
        #    the OllamaClient triggers its next inference.

        # 3. Log game session to long-term memory (fire-and-forget)
        if self._memory is not None:
            try:
                session_text = f"Played {game.title} for {hours}h {minutes}m"
                await self._memory.add(
                    text=session_text,
                    category="action",
                    tags=["game_session", game.title.lower().replace(" ", "_")],
                    source="system",
                )
                log.info("game mode: session logged to memory")
            except Exception:
                log.warning("game mode: session log failed", exc_info=True)

        # 4. Reset state
        self._active = False
        self._current_game = None
        self._preloaded_context = ""
        self._game_start_time = None

    # -- memory preloading --------------------------------------------------

    async def _preload_game_context(self, game_title: str) -> None:
        """Load all memories about the current game into RAM.

        Search memory for everything related to game_title and compile
        into a text block that can be used for answering questions
        without the LLM (pattern-matched retrieval from preloaded facts).
        """
        if self._memory is None:
            return

        try:
            results = await self._memory.search(game_title, limit=50)

            if not results:
                self._preloaded_context = ""
                log.info("game mode: no memories found for '%s'", game_title)
                return

            lines = [f"[Game context for {game_title}:]"]
            for fact in results:
                text = getattr(fact, "text", str(fact))
                lines.append(f"- {text}")

            self._preloaded_context = "\n".join(lines)

            # Inject event-triggered reminders for this game
            try:
                from jarvis.memory.reminders import check_reminders

                reminders = await check_reminders(self._memory, game_title)
                if reminders:
                    self._preloaded_context += "\n\n[REMINDERS for this game:]"
                    for r in reminders:
                        self._preloaded_context += f"\n⚠ REMINDER: {r}"
            except Exception:
                log.warning("game mode: reminder check failed (non-fatal)", exc_info=True)

            log.info(
                "game mode: preloaded %d facts for '%s' (%d chars)",
                len(results), game_title, len(self._preloaded_context),
            )
        except Exception:
            log.warning("game mode: preload failed", exc_info=True)
            self._preloaded_context = ""

    # -- on-demand LLM query (slow path) -----------------------------------

    async def on_demand_llm_query(self, question: str) -> str | None:
        """For questions that need LLM — temporarily load, answer, unload.

        This is the SLOW path for game mode. Used when the preloaded
        context doesn't have the answer. The pipeline speaks a thinking
        phrase ("Один момент, sir, загружаю модель...") before this.

        Returns the LLM response text, or None if unavailable.
        """
        if self._ollama is None:
            return None

        try:
            log.info("game mode: on-demand LLM query (temporary load)")
            # Warm Ollama so it loads the model
            await self._ollama.warm()

            # Build a minimal prompt with preloaded context + question
            messages: list[dict] = []
            if self._preloaded_context:
                messages.append({
                    "role": "system",
                    "content": self._preloaded_context,
                })
            messages.append({"role": "user", "content": question})

            # Collect the full response
            response_parts: list[str] = []
            async for chunk in self._ollama.stream_chat(messages):
                if chunk.content:
                    response_parts.append(chunk.content)
                if chunk.done:
                    break

            # Evict again to free VRAM for the game
            await self._ollama.unload()

            answer = "".join(response_parts).strip()
            log.info("game mode: on-demand query answered (%d chars)", len(answer))
            return answer if answer else None

        except Exception:
            log.warning("game mode: on-demand query failed", exc_info=True)
            # Try to evict anyway
            try:
                await self._ollama.unload()
            except Exception:
                log.debug("game mode: ollama unload failed (best-effort)", exc_info=True)
            return None

    async def on_demand_vision_query(self, image_b64: str, question: str, model: str) -> str | None:
        """Temporarily load vision model, answer, unload.

        Mirror of on_demand_llm_query but for vision/multimodal requests.
        Used by SeeScreenTool when game mode is active — the vision model
        is not resident in VRAM, so we do a temp load → answer → unload
        cycle.  Slower than the normal path but functional.

        Returns the vision model's description, or None on failure.
        """
        if self._ollama is None:
            return None

        try:
            log.info("game mode: on-demand vision query (temporary load)")
            description = await self._ollama.vision_chat(
                model=model,
                image_b64=image_b64,
                prompt=question,
            )
            # Unload vision model after answering
            await self._ollama.unload()
            return description
        except Exception:
            log.warning("game mode: on-demand vision query failed", exc_info=True)
            try:
                await self._ollama.unload()
            except Exception:
                log.debug("game mode: ollama unload failed (best-effort)", exc_info=True)
            return None

    # -- status info --------------------------------------------------------

    def get_game_session_info(self) -> str | None:
        """Summary of current game session for TTS / voice command."""
        if not self._active or self._current_game is None:
            return None
        duration = datetime.now() - (self._game_start_time or datetime.now())
        minutes = int(duration.total_seconds() / 60)
        return (
            f"Game mode active: {self._current_game.title}, "
            f"{minutes} minutes played. "
            f"Preloaded {len(self._preloaded_context)} chars of game context."
        )
