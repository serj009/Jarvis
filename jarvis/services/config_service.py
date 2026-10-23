"""Pure config helpers extracted from JarvisApp (Этап 11 step 2).

Only the dependency-free config logic lives here. ``_load_config`` stays in
``JarvisApp`` because it is bootstrap wiring of the composition root — it
installs logging, shows a startup error box, and calls ``SystemExit`` on a
broken config; moving it would drag those app-private helpers into a
"service", i.e. a backward dependency the decomposition plan forbids.

What moved: ``compute_changed_fields`` — a pure recursive diff of two
``model_dump()`` dicts. ``_on_config_change`` (the UI reaction: research
panel width, hotkey reload, event bus) stays in the app for now and will
migrate into the UiController in steps 3–4.
"""

from __future__ import annotations


def compute_changed_fields(
    old: dict, new: dict, prefix: str = "",
) -> list[str]:
    """Recursively diff two model_dump() dicts, returning dotted field paths."""
    changed: list[str] = []
    for key in set(old) | set(new):
        old_val = old.get(key)
        new_val = new.get(key)
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(old_val, dict) and isinstance(new_val, dict):
            changed.extend(compute_changed_fields(old_val, new_val, path))
        elif old_val != new_val:
            changed.append(path)
    return changed
