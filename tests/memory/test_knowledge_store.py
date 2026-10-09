"""Tests for jarvis.memory.knowledge_store."""

from __future__ import annotations

from pathlib import Path

from jarvis.memory.knowledge_store import KnowledgeStore


def _make_knowledge(tmp_path: Path) -> Path:
    """Create a test knowledge directory with YAML files."""
    base = tmp_path / "knowledge" / "base"
    base.mkdir(parents=True)
    shared = tmp_path / "knowledge" / "shared"
    shared.mkdir(parents=True)

    # system_facts.yaml
    (base / "system_facts.yaml").write_text(
        "identity: JARVIS is a local AI voice assistant\n"
        "creator: Serhii\n"
        "languages:\n"
        "  - English\n"
        "  - Russian\n"
        "  - Ukrainian\n",
        encoding="utf-8",
    )

    # commands.yaml
    (base / "commands.yaml").write_text(
        "open_youtube:\n"
        "  tool: open_url\n"
        "  aliases:\n"
        "    - open youtube\n"
        "    - открой ютуб\n"
        "    - відкрий ютуб\n",
        encoding="utf-8",
    )

    return tmp_path / "knowledge"


class TestKnowledgeStore:
    def test_load(self, tmp_path) -> None:
        kdir = _make_knowledge(tmp_path)
        ks = KnowledgeStore(knowledge_dir=kdir)
        assert ks.fact_count > 0

    def test_system_info(self, tmp_path) -> None:
        kdir = _make_knowledge(tmp_path)
        ks = KnowledgeStore(knowledge_dir=kdir)
        info = ks.get_system_info()
        assert "identity" in info
        assert "JARVIS" in info["identity"]

    def test_commands(self, tmp_path) -> None:
        kdir = _make_knowledge(tmp_path)
        ks = KnowledgeStore(knowledge_dir=kdir)
        cmds = ks.get_commands()
        assert "open_youtube" in cmds

    def test_search(self, tmp_path) -> None:
        kdir = _make_knowledge(tmp_path)
        ks = KnowledgeStore(knowledge_dir=kdir)
        results = ks.search("JARVIS")
        assert len(results) > 0
        assert "JARVIS" in results[0]["content"]

    def test_search_no_results(self, tmp_path) -> None:
        kdir = _make_knowledge(tmp_path)
        ks = KnowledgeStore(knowledge_dir=kdir)
        results = ks.search("quantum physics")
        assert len(results) == 0

    def test_empty_dir(self, tmp_path) -> None:
        ks = KnowledgeStore(knowledge_dir=tmp_path / "nonexistent")
        assert ks.fact_count == 0

    def test_none_dir(self) -> None:
        ks = KnowledgeStore(knowledge_dir=None)
        assert ks.fact_count == 0
