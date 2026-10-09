"""KnowledgeStore: read-only YAML-based knowledge base.

Loads structured knowledge from YAML files:
    knowledge/
    ├── base/               ← system facts (developer-provided)
    │   ├── system_facts.yaml
    │   ├── commands.yaml
    │   ├── faq.yaml
    │   └── behavior_patterns.yaml
    ├── shared/             ← shared databases (game achievements etc.)
    │   └── example_game_achievements.yaml
    └── templates/          ← templates for new databases
        └── game_template.yaml

This data is read-only at runtime and safe to share via GitHub.
No personal data here — personal facts go in MemoryStore (SQLite).

Falls back gracefully if PyYAML is not installed (tries JSON).

Usage:
    ks = KnowledgeStore(knowledge_dir=Path(".../knowledge"))
    info = ks.get_system_info()
    cmds = ks.get_commands()
    results = ks.search("how to reset settings")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _load_yaml(path: Path) -> dict:
    """Load a YAML file. Falls back to JSON if PyYAML unavailable."""
    try:
        import yaml
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # Fallback: try as JSON
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    except Exception:
        log.warning("failed to load %s", path, exc_info=True)
        return {}


class _IndexedFact:
    """An indexed fact for keyword search."""
    __slots__ = ("key", "content", "source", "data")

    def __init__(self, key: str, content: str, source: str, data: dict | None = None):
        self.key = key
        self.content = content
        self.source = source
        self.data = data


class KnowledgeStore:
    """Read-only knowledge base from YAML files.

    Three sections:
    - base/: system facts, commands, FAQ, behavior patterns
    - shared/: game achievements, tips databases
    - templates/: empty templates for creating new databases
    """

    def __init__(self, knowledge_dir: Path | None = None) -> None:
        self._dir = knowledge_dir
        self._base: dict[str, dict] = {}
        self._shared: dict[str, dict] = {}
        self._facts: list[_IndexedFact] = []
        if self._dir and self._dir.exists():
            self._load_all()

    def _load_all(self) -> None:
        """Load all YAML files from knowledge/."""
        assert self._dir is not None

        # Base knowledge
        base_dir = self._dir / "base"
        if base_dir.exists():
            for f in sorted(base_dir.glob("*.yaml")):
                data = _load_yaml(f)
                self._base[f.stem] = data
                self._index_facts(data, source=f"base/{f.name}")

        # Shared knowledge
        shared_dir = self._dir / "shared"
        if shared_dir.exists():
            for f in sorted(shared_dir.glob("*.yaml")):
                data = _load_yaml(f)
                self._shared[f.stem] = data
                self._index_facts(data, source=f"shared/{f.name}")

        log.info(
            "knowledge loaded: %d base, %d shared, %d indexed facts",
            len(self._base), len(self._shared), len(self._facts),
        )

    def _index_facts(self, data: dict | list, source: str, prefix: str = "") -> None:
        """Recursively index facts from nested YAML for keyword search."""
        if isinstance(data, dict):
            for key, value in data.items():
                path = f"{prefix}.{key}" if prefix else key
                if isinstance(value, str):
                    self._facts.append(_IndexedFact(path, value, source))
                elif isinstance(value, (dict, list)):
                    self._index_facts(value, source, path)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    self._facts.append(_IndexedFact(prefix, item, source))
                elif isinstance(item, dict):
                    text = (
                        item.get("text")
                        or item.get("description")
                        or item.get("answer")
                        or item.get("content")
                        or str(item)
                    )
                    self._facts.append(_IndexedFact(prefix, text, source, item))

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Keyword search across all indexed knowledge facts."""
        words = set(query.lower().split())
        if not words:
            return []

        scored: list[tuple[int, _IndexedFact]] = []
        for fact in self._facts:
            content_lower = fact.content.lower()
            score = sum(1 for w in words if w in content_lower)
            if score > 0:
                scored.append((score, fact))

        scored.sort(key=lambda x: -x[0])
        return [
            {
                "key": f.key,
                "content": f.content,
                "source": f.source,
                **({"data": f.data} if f.data else {}),
            }
            for _, f in scored[:limit]
        ]

    def get_system_info(self) -> dict:
        """Get system_facts.yaml content."""
        return self._base.get("system_facts", {})

    def get_commands(self) -> dict:
        """Get commands.yaml content."""
        return self._base.get("commands", {})

    def get_faq(self) -> dict:
        """Get faq.yaml content."""
        return self._base.get("faq", {})

    def get_behavior_patterns(self) -> dict:
        """Get behavior_patterns.yaml content."""
        return self._base.get("behavior_patterns", {})

    def get_shared(self, name: str) -> dict:
        """Get a shared database by name."""
        return self._shared.get(name, {})

    def list_shared(self) -> list[str]:
        """List available shared database names."""
        return sorted(self._shared.keys())

    @property
    def fact_count(self) -> int:
        return len(self._facts)
