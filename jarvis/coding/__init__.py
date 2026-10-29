"""Coding Agent subsystem — AI-powered code generation, editing, and project management.

Public surface:
  - CodingAgent:   the main orchestrator that plans and executes coding tasks.
  - ResponseParser: extracts file blocks from LLM responses.
  - CODING_SYSTEM_PROMPT: the system prompt injected into the coding model.
  - ProjectMap:    compact codebase overview for large projects.
  - CodeSearch:    fast symbol index and code search.
  - AgentNotepad:  persistent notes across coding steps.

The agent uses ``jarvis.llm.model_provider.ModelProvider`` for LLM access and
``jarvis.core.config.CodingConfig`` for behaviour settings.  It is exposed to
the user via the ``coding_agent`` voice tool
(``jarvis.tools.local.coding_tools.CodingAgentTool``).
"""

from jarvis.coding.agent import CODING_SYSTEM_PROMPT, CodingAgent, ResponseParser
from jarvis.coding.agent_notepad import AgentNotepad
from jarvis.coding.code_search import CodeSearch
from jarvis.coding.project_map import ProjectMap
from jarvis.coding.task_storage import CodingTask, TaskStep, TaskStorage
from jarvis.coding.voice_confirm import VoiceConfirm

__all__ = [
    "AgentNotepad",
    "CODING_SYSTEM_PROMPT",
    "CodeSearch",
    "CodingAgent",
    "CodingTask",
    "ProjectMap",
    "ResponseParser",
    "TaskStep",
    "TaskStorage",
    "VoiceConfirm",
]
