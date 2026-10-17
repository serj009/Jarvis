"""Coding Agent subsystem — AI-powered code generation, editing, and project management.

Public surface:
  - CodingAgent:   the main orchestrator that plans and executes coding tasks.
  - ResponseParser: extracts file blocks from LLM responses.
  - CODING_SYSTEM_PROMPT: the system prompt injected into the coding model.

The agent uses ``jarvis.llm.model_provider.ModelProvider`` for LLM access and
``jarvis.core.config.CodingConfig`` for behaviour settings.  It is exposed to
the user via the ``coding_agent`` voice tool
(``jarvis.tools.local.coding_tools.CodingAgentTool``).
"""

from jarvis.coding.agent import CodingAgent, ResponseParser, CODING_SYSTEM_PROMPT

__all__ = [
    "CodingAgent",
    "CODING_SYSTEM_PROMPT",
    "ResponseParser",
]
