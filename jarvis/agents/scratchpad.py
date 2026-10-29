"""AgentScratchpad — working memory for the autonomous agent.

Each step can read / write named variables.  The full scratchpad is
injected into the LLM context so the model knows what data is already
available from earlier steps.

Example::

    pad = AgentScratchpad()
    pad.set("weather", "sunny, 24 °C")
    pad.set("found_files", "report.pdf, summary.docx")

    context = pad.to_context()
    # → "Available data:\n  weather = sunny, 24 °C\n  found_files = ..."
"""

from __future__ import annotations

import copy
import logging

logger = logging.getLogger(__name__)


class AgentScratchpad:
    """Working memory for agent — stores intermediate results between steps.

    Each step can read/write named variables.
    Injected into LLM context so the model knows what data is available.
    """

    # Maximum characters per value before truncation
    _MAX_VALUE_LEN: int = 2000

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    # -- public API ---------------------------------------------------------

    def set(self, key: str, value: str) -> None:
        """Store a result for later steps.

        Args:
            key:   Descriptive name, e.g. ``"found_files"`` or ``"step_3_result"``.
            value: Text representation of the result.  Truncated to
                   :attr:`_MAX_VALUE_LEN` chars when stored.
        """
        if not key:
            raise ValueError("Scratchpad key must be a non-empty string.")
        if not isinstance(value, str):
            value = str(value)
        if len(value) > self._MAX_VALUE_LEN:
            value = value[: self._MAX_VALUE_LEN] + "… (truncated)"
        self._data[key] = value
        logger.debug("scratchpad[%s] = %s", key, value[:80])

    def get(self, key: str) -> str | None:
        """Retrieve a stored result.

        Returns:
            The value or ``None`` if the key was never set.
        """
        return self._data.get(key)

    def keys(self) -> list[str]:
        """Return all stored keys."""
        return list(self._data.keys())

    def snapshot(self) -> dict[str, str]:
        """Return a shallow copy of the internal data."""
        return copy.copy(self._data)

    def to_context(self) -> str:
        """Format for LLM injection.

        Returns a human-readable block listing every key/value pair so the
        LLM can decide which data to reuse in the next step.

        Returns:
            ``""`` when the scratchpad is empty, otherwise a formatted
            multiline string.
        """
        if not self._data:
            return ""
        lines = ["Available data from previous steps:"]
        for key, value in self._data.items():
            # Indent multi-line values
            indented = value.replace("\n", "\n    ")
            lines.append(f"  {key} = {indented}")
        return "\n".join(lines)

    def clear(self) -> None:
        """Reset for new task."""
        self._data.clear()
        logger.debug("scratchpad cleared")

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __repr__(self) -> str:
        keys = ", ".join(self._data.keys()) if self._data else "empty"
        return f"AgentScratchpad({keys})"
