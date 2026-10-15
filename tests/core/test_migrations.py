"""Tests for jarvis.core.migrations: module compiles, re-exports work, migrate() works."""

from __future__ import annotations

import pytest

from jarvis.core.config import CURRENT_SCHEMA_VERSION


# --- module imports compile -----------------------------------------------

def test_migrations_module_imports():
    """The migrations module must import without error (no circular deps)."""
    from jarvis.core import migrations  # noqa: F401


def test_migrations_exports():
    """Key symbols are accessible from migrations."""
    from jarvis.core.migrations import (
        MIGRATIONS,
        ConfigMigrationError,
        Migration,
        migrate,
        _SYSTEM_PROMPT_V3,
        _SYSTEM_PROMPT_V6,
        _SYSTEM_PROMPT_V26,
    )
    assert callable(migrate)
    assert isinstance(MIGRATIONS, dict)
    assert issubclass(ConfigMigrationError, ValueError)
    assert isinstance(_SYSTEM_PROMPT_V3, str)
    assert isinstance(_SYSTEM_PROMPT_V6, str)
    assert isinstance(_SYSTEM_PROMPT_V26, str)


# --- re-exports from config.py still work --------------------------------

def test_config_reexports_migration_symbols():
    """config.py re-exports migrate, MIGRATIONS, ConfigMigrationError."""
    from jarvis.core.config import (
        ConfigMigrationError,
        MIGRATIONS,
        migrate,
    )
    assert callable(migrate)
    assert isinstance(MIGRATIONS, dict)
    assert issubclass(ConfigMigrationError, ValueError)


def test_config_reexports_old_prompts():
    """config.py re-exports the old prompt constants for external consumers."""
    from jarvis.core.config import (
        _SYSTEM_PROMPT_V3,
        _SYSTEM_PROMPT_V6,
        _SYSTEM_PROMPT_V26,
    )
    assert "Jarvis" in _SYSTEM_PROMPT_V3
    assert "Jarvis" in _SYSTEM_PROMPT_V6
    assert "Jarvis" in _SYSTEM_PROMPT_V26


# --- migrate() works from both locations ---------------------------------

def test_migrate_from_migrations_module():
    """migrate() from migrations.py works on a current-version dict."""
    from jarvis.core.migrations import migrate
    from jarvis.core.config import JarvisConfig
    data = JarvisConfig().model_dump(mode="json")
    result = migrate(data)
    assert result["schema_version"] == CURRENT_SCHEMA_VERSION


def test_migrate_from_config_module():
    """migrate() re-exported through config.py works identically."""
    from jarvis.core.config import migrate, JarvisConfig
    data = JarvisConfig().model_dump(mode="json")
    result = migrate(data)
    assert result["schema_version"] == CURRENT_SCHEMA_VERSION


# --- MIGRATIONS dict is complete -----------------------------------------

def test_migration_chain_is_complete():
    """Every version from 1 to CURRENT_SCHEMA_VERSION-1 has a migration."""
    from jarvis.core.migrations import MIGRATIONS
    for v in range(1, CURRENT_SCHEMA_VERSION):
        assert v in MIGRATIONS, f"missing migration for version {v}"


def test_full_migration_chain_runs():
    """A minimal v1 config migrates all the way to current without error."""
    from jarvis.core.migrations import migrate
    data = {"schema_version": 1, "audio": {}, "llm": {}, "stt": {}}
    result = migrate(data)
    assert result["schema_version"] == CURRENT_SCHEMA_VERSION


# --- ConfigMigrationError ------------------------------------------------

def test_config_migration_error_is_value_error():
    """ConfigMigrationError must be a ValueError subclass."""
    from jarvis.core.migrations import ConfigMigrationError
    assert issubclass(ConfigMigrationError, ValueError)
    with pytest.raises(ConfigMigrationError):
        raise ConfigMigrationError("test")
