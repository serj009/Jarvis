"""IntegrationRegistry — discover, enable/disable, and collect from plugins.

Discovery scans ``jarvis/integrations/plugins/`` for modules, imports them,
and instantiates every concrete ``BaseIntegration`` subclass found. Enable
state is driven by config (``integrations.enabled`` list) so it survives a
restart. Collection methods aggregate tools / voice patterns / proactive
data from the *enabled* plugins only — the hooks the rest of the app calls.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class IntegrationRegistry:
    def __init__(self, *, enabled_names: list[str] | None = None) -> None:
        # Names the user has enabled (from config). Discovery marks matching
        # plugins enabled.
        self._enabled_names: set[str] = set(enabled_names or [])
        self._plugins: dict[str, BaseIntegration] = {}

    # -- discovery -------------------------------------------------------

    def discover_plugins(self) -> list[BaseIntegration]:
        """Import every plugin module and instantiate its integrations.

        Idempotent: re-running refreshes the registry. Import/》instantiation
        errors are logged and skipped so one bad plugin can't break the set.
        """
        import jarvis.integrations.plugins as plugins_pkg

        self._plugins.clear()
        for mod_info in pkgutil.iter_modules(plugins_pkg.__path__):
            if mod_info.name.startswith("_"):
                continue
            mod_name = f"{plugins_pkg.__name__}.{mod_info.name}"
            try:
                module = importlib.import_module(mod_name)
            except Exception:
                log.warning("[integrations] failed to import %s", mod_name, exc_info=True)
                continue
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, BaseIntegration)
                    and obj is not BaseIntegration
                    and not inspect.isabstract(obj)
                    and obj.__module__ == mod_name  # defined here, not imported
                ):
                    try:
                        inst = obj()
                    except Exception:
                        log.warning("[integrations] could not instantiate %s",
                                    obj.__name__, exc_info=True)
                        continue
                    if not inst.name:
                        log.warning("[integrations] %s has no name; skipping", obj.__name__)
                        continue
                    inst.enabled = inst.name in self._enabled_names
                    self._plugins[inst.name] = inst
        log.info("[integrations] discovered %d plugin(s)", len(self._plugins))
        return list(self._plugins.values())

    # -- access ----------------------------------------------------------

    def all(self) -> list[BaseIntegration]:
        return list(self._plugins.values())

    def get(self, name: str) -> BaseIntegration | None:
        return self._plugins.get(name)

    def get_enabled(self) -> list[BaseIntegration]:
        return [p for p in self._plugins.values() if p.enabled]

    # -- enable / disable ------------------------------------------------

    def enable(self, name: str) -> bool:
        """Mark a plugin enabled. Returns False if unknown or deps missing.

        Does NOT connect — connection (auth) is a separate, explicit step.
        """
        plugin = self._plugins.get(name)
        if plugin is None:
            return False
        missing = plugin.missing_packages()
        if missing:
            log.warning(
                "[integrations] cannot enable %s — missing packages: %s",
                name, ", ".join(missing),
            )
            return False
        plugin.enabled = True
        self._enabled_names.add(name)
        log.info("[integrations] enabled %s", name)
        return True

    def disable(self, name: str) -> bool:
        plugin = self._plugins.get(name)
        if plugin is None:
            return False
        plugin.enabled = False
        plugin.connected = False
        self._enabled_names.discard(name)
        log.info("[integrations] disabled %s", name)
        return True

    @property
    def enabled_names(self) -> list[str]:
        """Current enabled set, for persisting back to config."""
        return sorted(self._enabled_names)

    # -- collection (enabled plugins only) ------------------------------

    def get_all_tools(self) -> list[dict]:
        tools: list[dict] = []
        for p in self.get_enabled():
            try:
                tools.extend(p.get_tools())
            except Exception:
                log.warning("[integrations] get_tools failed for %s", p.name, exc_info=True)
        return tools

    def get_all_voice_patterns(self) -> list[tuple]:
        patterns: list[tuple] = []
        for p in self.get_enabled():
            try:
                patterns.extend(p.get_voice_patterns())
            except Exception:
                log.warning("[integrations] get_voice_patterns failed for %s",
                            p.name, exc_info=True)
        return patterns

    def get_all_proactive_data(self) -> list[dict]:
        data: list[dict] = []
        for p in self.get_enabled():
            try:
                d = p.get_proactive_data()
            except Exception:
                log.warning("[integrations] get_proactive_data failed for %s",
                            p.name, exc_info=True)
                continue
            if d:
                data.extend(d)
        return data

    async def execute_tool(self, tool_name: str, args: dict) -> str | None:
        """Route a tool call to the enabled plugin that owns it.

        Returns the result string, or None if no enabled plugin claims the
        tool (so the caller can fall through to local tools).
        """
        for p in self.get_enabled():
            try:
                names = {t.get("function", {}).get("name") for t in p.get_tools()}
            except Exception:
                continue
            if tool_name in names:
                return await p.execute_tool(tool_name, args)
        return None
