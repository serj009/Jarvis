"""Project templates for CodingAgent scaffolding.

Each sub-module exports a ``TEMPLATE`` dict with the following keys:

- ``name``            — unique slug (e.g. "flask_api")
- ``display_name``    — human-friendly title
- ``description``     — one-line summary
- ``keywords``        — list of search keywords (EN / RU / UK)
- ``files``           — dict mapping relative paths to file contents
- ``post_create_commands`` — shell commands to run after scaffolding
- ``test_command``    — command to run the tests
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
]

__all__ = ["TEMPLATES"]
