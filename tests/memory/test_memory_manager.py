"""MemoryManager: the facade over the memory subsystems."""

from __future__ import annotations


def test_memory_manager_can_be_constructed(tmp_path):
    """It passed db_dir= to ConversationMemory, which takes root_path=, so
    constructing a MemoryManager raised TypeError (found by pyright, M2-1)."""
    from jarvis.memory.memory_manager import MemoryManager

    mm = MemoryManager(data_root=tmp_path)
    assert mm.conversations._root == tmp_path / "conversations"
