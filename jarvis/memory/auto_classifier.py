"""AutoClassifier: LLM-based entity classification for shard routing.

Classifies unknown entities (game titles, movies, tech terms, food, etc.)
into categories using a local Ollama LLM. The classification result
determines which shard the entity's facts are routed to.

Two-level approach:
1. LRU cache (instant, persisted to JSON on disk)
2. LLM classification (offline, ~0.5s via Ollama)

Fully local — no internet required. Graceful degradation: if Ollama is
unavailable, classification silently falls back to "general" shard.

Usage:
    classifier = AutoClassifier(
        ollama_endpoint="http://localhost:11434",
        model="qwen2.5:7b-instruct",
    )
    result = await classifier.classify("Wukong", context="collect all brushes")
    # -> type="game", shard="games", confidence=0.85
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Entity type -> shard mapping
# ---------------------------------------------------------------------------

ENTITY_TYPE_TO_SHARD: dict[str, str] = {
    "game": "games", "video_game": "games", "board_game": "games",
    "tv_series": "tv_media", "movie": "tv_media", "anime": "tv_media",
    "cartoon": "tv_media",
    "book": "books", "music": "music", "band": "music", "song": "music",
    "food": "cooking", "recipe": "cooking", "dish": "cooking",
    "plant": "plants", "flower": "plants",
    "sport": "sport", "person": "personal", "place": "general",
    "technology": "tech", "software": "tech", "programming": "tech",
}


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Immutable result of entity classification."""

    entity: str = ""
    entity_type: str = "unknown"
    full_name: str = ""
    shard: str = "general"
    confidence: float = 0.0
    source: str = "none"
    extra_info: str = ""

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "type": self.entity_type,
            "full_name": self.full_name or self.entity,
            "shard": self.shard,
            "confidence": self.confidence,
            "source": self.source,
            "extra_info": self.extra_info,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ClassificationResult:
        return cls(
            entity=data.get("entity", ""),
            entity_type=data.get("type", "unknown"),
            full_name=data.get("full_name", ""),
            shard=data.get("shard", "general"),
            confidence=data.get("confidence", 0.0),
            source=data.get("source", "none"),
            extra_info=data.get("extra_info", ""),
        )


# ---------------------------------------------------------------------------
# AutoClassifier
# ---------------------------------------------------------------------------

class AutoClassifier:
    """Automatic entity classification: cache -> LLM -> unknown.

    Fully local: uses Ollama for LLM classification. No internet calls.
    If Ollama is unavailable, classify() returns an "unknown" result
    with shard="general" — the caller can still function.
    """

    LLM_PROMPT_TEMPLATE = (
        'Classify the entity "{entity}" into one category.\n'
        'Answer with ONLY a JSON object:\n'
        '{{"type": "game|movie|tv_series|anime|music|book|food|plant|sport'
        '|person|place|technology|other",'
        ' "full_name": "full official name", "brief": "one sentence"}}\n'
        'Entity: "{entity}"\nContext: "{context}"'
    )

    def __init__(
        self,
        *,
        ollama_endpoint: str = "http://localhost:11434",
        model: str = "qwen2.5:7b-instruct",
        cache_path: Path | str | None = None,
        max_cache: int = 5000,
    ) -> None:
        self._endpoint = ollama_endpoint.rstrip("/")
        self._model = model
        self._cache_path = Path(cache_path) if cache_path else None
        self._max_cache = max_cache
        self._cache: OrderedDict[str, ClassificationResult] = OrderedDict()
        self._load_cache()

    # -- Cache persistence --------------------------------------------------

    def _load_cache(self) -> None:
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, entry in data.items():
                self._cache[key] = ClassificationResult.from_dict(entry)
            log.info("classification cache loaded: %d entities", len(self._cache))
        except Exception as e:
            log.warning("classification cache load failed: %s", e)

    def _save_cache(self) -> None:
        if self._cache_path is None:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            data = {k: r.to_dict() for k, r in self._cache.items()}
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning("classification cache save failed: %s", e)

    def _store(self, key: str, result: ClassificationResult) -> None:
        """Store a result in the LRU cache, evicting oldest if full."""
        while len(self._cache) >= self._max_cache:
            self._cache.popitem(last=False)
        self._cache[key] = result
        self._save_cache()

    # -- Public API ---------------------------------------------------------

    async def classify(
        self, entity: str, context: str = ""
    ) -> ClassificationResult:
        """Classify an entity: cache -> LLM -> unknown.

        Returns ClassificationResult with entity_type, shard, and confidence.
        Never raises — returns "unknown" on any failure.
        """
        key = entity.lower().strip()

        # 1. Check LRU cache
        if key in self._cache:
            cached = self._cache[key]
            self._cache.move_to_end(key)
            return ClassificationResult(
                entity=cached.entity,
                entity_type=cached.entity_type,
                full_name=cached.full_name,
                shard=cached.shard,
                confidence=cached.confidence,
                source="cache",
                extra_info=cached.extra_info,
            )

        # 2. LLM classification (local Ollama)
        result = await self._classify_with_llm(entity, context)
        if result is not None and result.confidence >= 0.7:
            self._store(key, result)
            return result

        # 3. Unknown — return general shard
        unknown = ClassificationResult(entity=entity, shard="general", source="none")
        return unknown

    async def _classify_with_llm(
        self, entity: str, context: str
    ) -> Optional[ClassificationResult]:
        """Call local Ollama for entity classification."""
        try:
            import httpx
        except ImportError:
            log.debug("httpx not available; LLM classification disabled")
            return None

        prompt = self.LLM_PROMPT_TEMPLATE.format(entity=entity, context=context)

        try:
            start = time.monotonic()
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
            ) as client:
                resp = await client.post(
                    f"{self._endpoint}/api/generate",
                    json={
                        "model": self._model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 200},
                    },
                )
                resp.raise_for_status()
                response_text = resp.json().get("response", "")

            ms = (time.monotonic() - start) * 1000

            # Parse JSON from LLM response
            data = json.loads(response_text.strip())
            entity_type = data.get("type", "other")
            shard = ENTITY_TYPE_TO_SHARD.get(entity_type, "general")

            result = ClassificationResult(
                entity=entity,
                entity_type=entity_type,
                full_name=data.get("full_name", entity),
                shard=shard,
                confidence=0.85,
                source="llm",
                extra_info=data.get("brief", ""),
            )
            log.info(
                "LLM classified '%s' -> %s (%s) in %.0fms",
                entity, entity_type, shard, ms,
            )
            return result

        except Exception as e:
            log.warning("LLM classify failed for '%s': %s", entity, e)
            return None

    # -- Stats --------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return classifier statistics."""
        return {"cached": len(self._cache)}
