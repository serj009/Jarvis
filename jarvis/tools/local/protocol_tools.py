"""Voice tools for user-defined protocols (macros).

Seven tools let the user manage protocols entirely by voice:

  create_protocol    — create a new protocol from a description
  run_protocol       — execute a protocol by name
  list_protocols     — list all protocols
  delete_protocol    — delete a protocol by name
  edit_protocol      — replace steps of a protocol
  enable_protocol    — re-enable a disabled protocol
  disable_protocol   — disable a protocol without deleting it

All tools delegate to ``ProtocolEngine`` for storage and execution.
The engine is injected at construction; tests pass a fake.

Voice patterns use priority 8 — higher than most tools (10+) because
protocol create/run/list/delete commands must be intercepted before
generic catch-all patterns like ``open_app``'s ``^open\s+(.+)$``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

if TYPE_CHECKING:
    from jarvis.skills.protocols import ProtocolEngine

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class CreateProtocolArgs(BaseModel):
    name: str = Field(description="Name for the new protocol (1-64 chars).")
    description: str = Field(
        description=(
            "Natural-language description of what the protocol does. "
            "Example: 'turn on music and set volume to max'."
        ),
    )


class RunProtocolArgs(BaseModel):
    name: str = Field(description="Name of the protocol to run.")


class ListProtocolsArgs(BaseModel):
    pass


class DeleteProtocolArgs(BaseModel):
    name: str = Field(description="Name of the protocol to delete.")


class EditProtocolArgs(BaseModel):
    name: str = Field(description="Name of the protocol to edit.")
    description: str = Field(
        description="New description of the protocol steps.",
    )


class EnableProtocolArgs(BaseModel):
    name: str = Field(description="Name of the protocol to enable.")


class DisableProtocolArgs(BaseModel):
    name: str = Field(description="Name of the protocol to disable.")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

class CreateProtocolTool:
    name: str = "create_protocol"
    description: str = (
        "Creates a new user-defined voice protocol (macro). "
        "The user names it and describes the steps in natural language. "
        "Example: name='Party', description='turn on music and set volume to max'. "
        "(RU: создай протокол / UK: створи протокол)"
    )
    args_schema: type[BaseModel] = CreateProtocolArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "create protocol <name> — <description>"
        VoicePattern(
            regex=(
                r"^create\s+protocol\s+([\w\s\-]+?)"
                r"\s*[-–—:]\s*(.+)$"
            ),
            priority=8,
            args=lambda m: {
                "name": m.group(1).strip(),
                "description": m.group(2).strip(),
            },
        ),
        # RU: "создай протокол <name> — <description>"
        VoicePattern(
            regex=(
                r"^(?:создай|создать)\s+протокол\s+([\w\s\-]+?)"
                r"\s*[-–—:]\s*(.+)$"
            ),
            priority=8,
            args=lambda m: {
                "name": m.group(1).strip(),
                "description": m.group(2).strip(),
            },
        ),
        # UK: "створи протокол <name> — <description>"
        VoicePattern(
            regex=(
                r"^(?:створи|створити)\s+протокол\s+([\w\s\-]+?)"
                r"\s*[-–—:]\s*(.+)$"
            ),
            priority=8,
            args=lambda m: {
                "name": m.group(1).strip(),
                "description": m.group(2).strip(),
            },
        ),
    )

    def __init__(self, engine: ProtocolEngine | None = None) -> None:
        self._engine = engine

    async def execute(self, args: CreateProtocolArgs) -> ToolResult:
        if self._engine is None:
            return ToolResult(success=False, error="Protocol engine not available.")
        try:
            protocol = await self._engine.create_protocol(
                args.name, args.description,
            )
            steps_desc = ", ".join(s.tool_name for s in protocol.steps)
            return ToolResult(
                success=True,
                output=(
                    f"Protocol '{protocol.name}' created with "
                    f"{len(protocol.steps)} step(s): {steps_desc}. "
                    f"Say '{protocol.name}' to run it."
                ),
            )
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            log.exception("Failed to create protocol")
            return ToolResult(success=False, error=f"Failed to create protocol: {e}")


class RunProtocolTool:
    name: str = "run_protocol"
    description: str = (
        "Runs a user-defined protocol (macro) by name. "
        "Executes all steps in sequence. "
        "(RU: запусти протокол / UK: запусти протокол)"
    )
    args_schema: type[BaseModel] = RunProtocolArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN: "run protocol <name>"
        VoicePattern(
            regex=r"^run\s+protocol\s+(.+)$",
            priority=8,
            args=lambda m: {"name": m.group(1).strip()},
        ),
        # RU: "запусти протокол <name>"
        VoicePattern(
            regex=r"^(?:запусти|запустить|выполни|выполнить)\s+протокол\s+(.+)$",
            priority=8,
            args=lambda m: {"name": m.group(1).strip()},
        ),
        # UK: "запусти протокол <name>"
        VoicePattern(
            regex=r"^(?:запусти|запустити|виконай|виконати)\s+протокол\s+(.+)$",
            priority=8,
            args=lambda m: {"name": m.group(1).strip()},
        ),
    )

    def __init__(self, engine: ProtocolEngine | None = None) -> None:
        self._engine = engine

    async def execute(self, args: RunProtocolArgs) -> ToolResult:
        if self._engine is None:
            return ToolResult(success=False, error="Protocol engine not available.")
        result = await self._engine.execute_protocol(args.name)
        if result.success:
            return ToolResult(
                success=True,
                output=(
                    f"Protocol '{result.protocol_name}' executed: "
                    f"{result.steps_completed}/{result.steps_total} steps completed."
                ),
            )
        return ToolResult(success=False, error=result.error or "Protocol execution failed.")


class ListProtocolsTool:
    name: str = "list_protocols"
    description: str = (
        "Lists all user-defined protocols (macros). "
        "(RU: список протоколов / UK: список протоколів)"
    )
    args_schema: type[BaseModel] = ListProtocolsArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^(?:list|show)\s+protocols?$",
            priority=8,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^(?:список|покажи|все)\s+протокол(?:ы|ов)?$",
            priority=8,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:список|покажи|всі)\s+протокол(?:и|ів)?$",
            priority=8,
            args=lambda m: {},
        ),
    )

    def __init__(self, engine: ProtocolEngine | None = None) -> None:
        self._engine = engine

    async def execute(self, args: ListProtocolsArgs) -> ToolResult:
        if self._engine is None:
            return ToolResult(success=False, error="Protocol engine not available.")
        protocols = self._engine.list_protocols()
        if not protocols:
            return ToolResult(success=True, output="No protocols defined yet.")
        lines = []
        for p in protocols:
            status = "enabled" if p.enabled else "disabled"
            steps = ", ".join(s.tool_name for s in p.steps)
            lines.append(f"• {p.name} [{status}]: {steps}")
        return ToolResult(
            success=True,
            output=f"{len(protocols)} protocol(s):\n" + "\n".join(lines),
        )


class DeleteProtocolTool:
    name: str = "delete_protocol"
    description: str = (
        "Deletes a user-defined protocol (macro) by name. "
        "(RU: удали протокол / UK: видали протокол)"
    )
    args_schema: type[BaseModel] = DeleteProtocolArgs
    requires_confirmation: bool = True  # DESTRUCTIVE

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^delete\s+protocol\s+(.+)$",
            priority=8,
            args=lambda m: {"name": m.group(1).strip()},
        ),
        VoicePattern(
            regex=r"^(?:удали|удалить)\s+протокол\s+(.+)$",
            priority=8,
            args=lambda m: {"name": m.group(1).strip()},
        ),
        VoicePattern(
            regex=r"^(?:видали|видалити)\s+протокол\s+(.+)$",
            priority=8,
            args=lambda m: {"name": m.group(1).strip()},
        ),
    )

    def __init__(self, engine: ProtocolEngine | None = None) -> None:
        self._engine = engine

    async def execute(self, args: DeleteProtocolArgs) -> ToolResult:
        if self._engine is None:
            return ToolResult(success=False, error="Protocol engine not available.")
        if self._engine.delete_protocol(args.name):
            return ToolResult(
                success=True,
                output=f"Protocol '{args.name}' deleted.",
            )
        return ToolResult(
            success=False,
            error=f"Protocol '{args.name}' not found.",
        )


class EditProtocolTool:
    name: str = "edit_protocol"
    description: str = (
        "Replaces the steps of an existing protocol with a new description. "
        "(RU: измени протокол / UK: зміни протокол)"
    )
    args_schema: type[BaseModel] = EditProtocolArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=(
                r"^edit\s+protocol\s+([\w\s\-]+?)"
                r"\s*[-–—:]\s*(.+)$"
            ),
            priority=8,
            args=lambda m: {
                "name": m.group(1).strip(),
                "description": m.group(2).strip(),
            },
        ),
        VoicePattern(
            regex=(
                r"^(?:измени|изменить|редактируй)\s+протокол\s+([\w\s\-]+?)"
                r"\s*[-–—:]\s*(.+)$"
            ),
            priority=8,
            args=lambda m: {
                "name": m.group(1).strip(),
                "description": m.group(2).strip(),
            },
        ),
        VoicePattern(
            regex=(
                r"^(?:зміни|змінити|редагуй)\s+протокол\s+([\w\s\-]+?)"
                r"\s*[-–—:]\s*(.+)$"
            ),
            priority=8,
            args=lambda m: {
                "name": m.group(1).strip(),
                "description": m.group(2).strip(),
            },
        ),
    )

    def __init__(self, engine: ProtocolEngine | None = None) -> None:
        self._engine = engine

    async def execute(self, args: EditProtocolArgs) -> ToolResult:
        if self._engine is None:
            return ToolResult(success=False, error="Protocol engine not available.")

        existing = self._engine.get_protocol(args.name)
        if existing is None:
            return ToolResult(
                success=False,
                error=f"Protocol '{args.name}' not found.",
            )

        # Parse new steps
        from jarvis.skills.protocols import _parse_simple_steps, _parse_steps_with_llm
        steps = _parse_simple_steps(args.description)
        if steps is None:
            steps = await _parse_steps_with_llm(
                args.description,
                self._engine._ollama_client,
                self._engine._registry,
            )
        if not steps:
            return ToolResult(
                success=False,
                error="Could not parse new steps from description.",
            )

        self._engine.update_steps(args.name, steps)
        steps_desc = ", ".join(s.tool_name for s in steps)
        return ToolResult(
            success=True,
            output=(
                f"Protocol '{args.name}' updated with "
                f"{len(steps)} step(s): {steps_desc}."
            ),
        )


class EnableProtocolTool:
    name: str = "enable_protocol"
    description: str = (
        "Re-enables a previously disabled protocol. "
        "(RU: включи протокол / UK: увімкни протокол)"
    )
    args_schema: type[BaseModel] = EnableProtocolArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^enable\s+protocol\s+(.+)$",
            priority=8,
            args=lambda m: {"name": m.group(1).strip()},
        ),
        VoicePattern(
            regex=r"^(?:включи|включить|активируй)\s+протокол\s+(.+)$",
            priority=8,
            args=lambda m: {"name": m.group(1).strip()},
        ),
        VoicePattern(
            regex=r"^(?:увімкни|увімкнути|активуй)\s+протокол\s+(.+)$",
            priority=8,
            args=lambda m: {"name": m.group(1).strip()},
        ),
    )

    def __init__(self, engine: ProtocolEngine | None = None) -> None:
        self._engine = engine

    async def execute(self, args: EnableProtocolArgs) -> ToolResult:
        if self._engine is None:
            return ToolResult(success=False, error="Protocol engine not available.")
        if self._engine.set_enabled(args.name, True):
            return ToolResult(
                success=True,
                output=f"Protocol '{args.name}' enabled.",
            )
        return ToolResult(
            success=False,
            error=f"Protocol '{args.name}' not found.",
        )


class DisableProtocolTool:
    name: str = "disable_protocol"
    description: str = (
        "Disables a protocol without deleting it. "
        "(RU: отключи протокол / UK: вимкни протокол)"
    )
    args_schema: type[BaseModel] = DisableProtocolArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^disable\s+protocol\s+(.+)$",
            priority=8,
            args=lambda m: {"name": m.group(1).strip()},
        ),
        VoicePattern(
            regex=r"^(?:отключи|отключить|выключи|деактивируй)\s+протокол\s+(.+)$",
            priority=8,
            args=lambda m: {"name": m.group(1).strip()},
        ),
        VoicePattern(
            regex=r"^(?:вимкни|вимкнути|деактивуй)\s+протокол\s+(.+)$",
            priority=8,
            args=lambda m: {"name": m.group(1).strip()},
        ),
    )

    def __init__(self, engine: ProtocolEngine | None = None) -> None:
        self._engine = engine

    async def execute(self, args: DisableProtocolArgs) -> ToolResult:
        if self._engine is None:
            return ToolResult(success=False, error="Protocol engine not available.")
        if self._engine.set_enabled(args.name, False):
            return ToolResult(
                success=True,
                output=f"Protocol '{args.name}' disabled.",
            )
        return ToolResult(
            success=False,
            error=f"Protocol '{args.name}' not found.",
        )
