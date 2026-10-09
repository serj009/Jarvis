"""Tests for jarvis.memory.dynamic_shard_manager."""

from __future__ import annotations

from jarvis.memory.dynamic_shard_manager import DynamicShardManager, STANDARD_SHARDS
from jarvis.memory.shard_manager import ShardManager


class TestDynamicShardManager:
    def test_resolve_standard(self, tmp_path) -> None:
        sm = ShardManager(tmp_path / "shards")
        dsm = DynamicShardManager(sm, registry_path=tmp_path / "reg.json")
        assert dsm.resolve_shard("general", []) == "general"
        assert dsm.resolve_shard("personal", []) == "personal"

    def test_resolve_with_tags(self, tmp_path) -> None:
        sm = ShardManager(tmp_path / "shards")
        dsm = DynamicShardManager(sm, registry_path=tmp_path / "reg.json")
        # "games" is a standard shard via normalize_topic
        result = dsm.resolve_shard("general", ["stalker_2", "achievement"])
        assert result in STANDARD_SHARDS or result == "games"

    def test_force_create_shard(self, tmp_path) -> None:
        sm = ShardManager(tmp_path / "shards")
        dsm = DynamicShardManager(sm, registry_path=tmp_path / "reg.json")
        dsm.force_create_shard("cooking", "User cooks a lot")
        assert "cooking" in dsm.dynamic_shards

    def test_registry_persistence(self, tmp_path) -> None:
        sm = ShardManager(tmp_path / "shards")
        reg = tmp_path / "reg.json"

        dsm1 = DynamicShardManager(sm, registry_path=reg)
        dsm1.force_create_shard("music")
        assert "music" in dsm1.dynamic_shards

        dsm2 = DynamicShardManager(sm, registry_path=reg)
        assert "music" in dsm2.dynamic_shards

    def test_on_fact_added_counting(self, tmp_path) -> None:
        sm = ShardManager(tmp_path / "shards")
        dsm = DynamicShardManager(sm, registry_path=tmp_path / "reg.json")
        # Should not crash even without a general shard
        for _ in range(25):
            dsm.on_fact_added()

    def test_all_shards_info(self, tmp_path) -> None:
        sm = ShardManager(tmp_path / "shards")
        dsm = DynamicShardManager(sm, registry_path=tmp_path / "reg.json")
        dsm.force_create_shard("anime")
        info = dsm.all_shards_info()
        assert "standard" in info
        assert "dynamic" in info
        assert "anime" in info["dynamic"]

    def test_standard_shards_not_created(self, tmp_path) -> None:
        sm = ShardManager(tmp_path / "shards")
        dsm = DynamicShardManager(sm, registry_path=tmp_path / "reg.json")
        dsm.force_create_shard("general")  # Should be skipped
        assert "general" not in dsm.dynamic_shards
