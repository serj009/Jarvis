"""Project templates for CodingAgent scaffolding.

Each sub-module exports a ``TEMPLATE`` dict with the following keys:

- ``name``            — unique slug (e.g. "flask_api")
- ``display_name``    — human-friendly title
- ``description``     — one-line summary
- ``keywords``        — list of search keywords (EN / RU / UK)
- ``files``           — dict mapping relative paths to file contents
- ``post_create_commands`` — shell commands to run after scaffolding
- ``test_command``    — command to run the tests

Optional:
- ``language``        — language key ("python", "javascript", etc.)
                        Defaults to "python" when absent.
"""

from jarvis.coding.templates.automation import TEMPLATE as _AUTOMATION_TEMPLATE
from jarvis.coding.templates.cli_tool import TEMPLATE as _CLI_TOOL_TEMPLATE
from jarvis.coding.templates.csharp_console import TEMPLATE as _CSHARP_CONSOLE_TEMPLATE
from jarvis.coding.templates.data_analysis import TEMPLATE as _DATA_ANALYSIS_TEMPLATE
from jarvis.coding.templates.discord_bot import TEMPLATE as _DISCORD_BOT_TEMPLATE
from jarvis.coding.templates.fastapi_tmpl import TEMPLATE as _FASTAPI_TEMPLATE
from jarvis.coding.templates.flask_api import TEMPLATE as _FLASK_API_TEMPLATE
from jarvis.coding.templates.go_basic import TEMPLATE as _GO_BASIC_TEMPLATE

# Multi-language templates
from jarvis.coding.templates.js_node_basic import TEMPLATE as _JS_NODE_BASIC_TEMPLATE
from jarvis.coding.templates.pygame_game import TEMPLATE as _PYGAME_GAME_TEMPLATE
from jarvis.coding.templates.python_basic import TEMPLATE as _PYTHON_BASIC_TEMPLATE
from jarvis.coding.templates.react_app import TEMPLATE as _REACT_APP_TEMPLATE
from jarvis.coding.templates.rust_basic import TEMPLATE as _RUST_BASIC_TEMPLATE
from jarvis.coding.templates.scraper import TEMPLATE as _SCRAPER_TEMPLATE
from jarvis.coding.templates.telegram_bot import TEMPLATE as _TELEGRAM_BOT_TEMPLATE
from jarvis.coding.templates.ts_node_basic import TEMPLATE as _TS_NODE_BASIC_TEMPLATE

TEMPLATES: list[dict] = [
    _PYTHON_BASIC_TEMPLATE,
    _FLASK_API_TEMPLATE,
    _FASTAPI_TEMPLATE,
    _TELEGRAM_BOT_TEMPLATE,
    _DISCORD_BOT_TEMPLATE,
    _CLI_TOOL_TEMPLATE,
    _SCRAPER_TEMPLATE,
    _DATA_ANALYSIS_TEMPLATE,
    _PYGAME_GAME_TEMPLATE,
    _AUTOMATION_TEMPLATE,
    # Multi-language templates
    _JS_NODE_BASIC_TEMPLATE,
    _TS_NODE_BASIC_TEMPLATE,
    _REACT_APP_TEMPLATE,
    _CSHARP_CONSOLE_TEMPLATE,
    _GO_BASIC_TEMPLATE,
    _RUST_BASIC_TEMPLATE,
]

__all__ = ["TEMPLATES"]
