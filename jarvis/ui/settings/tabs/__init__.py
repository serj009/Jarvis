"""Per-section tabs that compose into SettingsWindow's sidebar + stacked widget.

Every tab follows the same contract:
  - Constructor: (*, config: JarvisConfig, on_change: Callable[[], None],
                  ...optional tab-specific extras)
  - Reads field values from `config` at construction.
  - On any user edit: mutates the matching config field in-place,
    persists via save_config(config), then invokes on_change() so the
    composition root can republish the ConfigChanged events the audio
    loop subscribes to.
"""

from jarvis.ui.settings.tabs.about import AboutTab
from jarvis.ui.settings.tabs.advanced import AdvancedTab
from jarvis.ui.settings.tabs.agent import AgentTab
from jarvis.ui.settings.tabs.coding import CodingTab
from jarvis.ui.settings.tabs.commands import CommandsTab
from jarvis.ui.settings.tabs.gaming import GamingTab
from jarvis.ui.settings.tabs.general import GeneralTab
from jarvis.ui.settings.tabs.hotkeys import HotkeysTab
from jarvis.ui.settings.tabs.image_gen import ImageGenTab
from jarvis.ui.settings.tabs.memory import MemoryTab
from jarvis.ui.settings.tabs.models import ModelsTab
from jarvis.ui.settings.tabs.security_tab import SecurityTab
from jarvis.ui.settings.tabs.integrations import IntegrationsTab
from jarvis.ui.settings.tabs.proactive import ProactiveTab
from jarvis.ui.settings.tabs.self_eval import SelfEvalTab
from jarvis.ui.settings.tabs.tools import ToolsTab
from jarvis.ui.settings.tabs.voice import VoiceTab

__all__ = [
    "AboutTab",
    "AdvancedTab",
    "AgentTab",
    "CodingTab",
    "CommandsTab",
    "GamingTab",
    "GeneralTab",
    "HotkeysTab",
    "ImageGenTab",
    "MemoryTab",
    "ModelsTab",
    "SecurityTab",
    "IntegrationsTab",
    "ProactiveTab",
    "SelfEvalTab",
    "ToolsTab",
    "VoiceTab",
]
