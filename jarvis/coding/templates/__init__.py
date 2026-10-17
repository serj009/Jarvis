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

from jarvis.coding.templates.python_basic import TEMPLATE as _python_basic
from jarvis.coding.templates.flask_api import TEMPLATE as _flask_api
from jarvis.coding.templates.fastapi_tmpl import TEMPLATE as _fastapi
from jarvis.coding.templates.telegram_bot import TEMPLATE as _telegram_bot
from jarvis.coding.templates.discord_bot import TEMPLATE as _discord_bot
from jarvis.coding.templates.cli_tool import TEMPLATE as _cli_tool
from jarvis.coding.templates.scraper import TEMPLATE as _scraper
from jarvis.coding.templates.data_analysis import TEMPLATE as _data_analysis
from jarvis.coding.templates.pygame_game import TEMPLATE as _pygame_game
from jarvis.coding.templates.automation import TEMPLATE as _automation

# Multi-language templates
from jarvis.coding.templates.js_node_basic import TEMPLATE as _js_node_basic
from jarvis.coding.templates.ts_node_basic import TEMPLATE as _ts_node_basic
from jarvis.coding.templates.react_app import TEMPLATE as _react_app
from jarvis.coding.templates.csharp_console import TEMPLATE as _csharp_console
from jarvis.coding.templates.go_basic import TEMPLATE as _go_basic
from jarvis.coding.templates.rust_basic import TEMPLATE as _rust_basic

TEMPLATES: list[dict] = [
    _python_basic,
    _flask_api,
    _fastapi,
    _telegram_bot,
    _discord_bot,
    _cli_tool,
    _scraper,
    _data_analysis,
    _pygame_game,
    _automation,
    # Multi-language templates
    _js_node_basic,
    _ts_node_basic,
    _react_app,
    _csharp_console,
    _go_basic,
    _rust_basic,
]

__all__ = ["TEMPLATES"]
