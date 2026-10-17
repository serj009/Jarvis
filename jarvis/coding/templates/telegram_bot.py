"""Template: Telegram Bot."""

TEMPLATE: dict = {
    "name": "telegram_bot",
    "display_name": "Telegram Bot",
    "description": "Telegram bot with python-telegram-bot library",
    "keywords": [
        "telegram", "бот", "bot", "телеграм", "тг",
        "tg", "чат-бот", "chatbot",
    ],
    "files": {
        "bot.py": (
            '"""{project_name} — Telegram bot entry point."""\n'
            "\n"
            "import logging\n"
            "\n"
            "from telegram.ext import (\n"
            "    ApplicationBuilder,\n"
            "    CommandHandler,\n"
            "    MessageHandler,\n"
            "    filters,\n"
            ")\n"
            "\n"
            "from config import BOT_TOKEN\n"
            "from handlers import start, help_command, echo\n"
            "\n"
            "logging.basicConfig(\n"
            '    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",\n'
            "    level=logging.INFO,\n"
            ")\n"
            "logger = logging.getLogger(__name__)\n"
            "\n"
            "\n"
            "def main() -> None:\n"
            '    """Start the bot."""\n'
            "    app = ApplicationBuilder().token(BOT_TOKEN).build()\n"
            "\n"
            '    app.add_handler(CommandHandler("start", start))\n'
            '    app.add_handler(CommandHandler("help", help_command))\n'
            "    app.add_handler(\n"
            "        MessageHandler(\n"
            "            filters.TEXT & ~filters.COMMAND, echo\n"
            "        )\n"
            "    )\n"
            "\n"
            '    logger.info("Bot started")\n'
            "    app.run_polling()\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        ),
        "handlers.py": (
            '"""Message handlers for {project_name}."""\n'
            "\n"
            "from telegram import Update\n"
            "from telegram.ext import ContextTypes\n"
            "\n"
            "\n"
            "async def start(\n"
            "    update: Update, context: ContextTypes.DEFAULT_TYPE\n"
            ") -> None:\n"
            '    """Send a greeting when /start is issued."""\n'
            "    user = update.effective_user\n"
            "    await update.message.reply_html(\n"
            '        f"Hi {{user.mention_html()}}!\\n"\n'
            '        f"I am {project_name} bot. Send /help for commands."\n'
            "    )\n"
            "\n"
            "\n"
            "async def help_command(\n"
            "    update: Update, context: ContextTypes.DEFAULT_TYPE\n"
            ") -> None:\n"
            '    """Send help text."""\n'
            "    text = (\n"
            '        "Available commands:\\n"\n'
            '        "/start — Start the bot\\n"\n'
            '        "/help — Show this help\\n"\n'
            '        "\\nSend any text and I will echo it back."\n'
            "    )\n"
            "    await update.message.reply_text(text)\n"
            "\n"
            "\n"
            "async def echo(\n"
            "    update: Update, context: ContextTypes.DEFAULT_TYPE\n"
            ") -> None:\n"
            '    """Echo user message."""\n'
            "    await update.message.reply_text(update.message.text)\n"
        ),
        "config.py": (
            '"""Configuration for {project_name}."""\n'
            "\n"
            "import os\n"
            "\n"
            "BOT_TOKEN = os.environ.get(\n"
            '    "TELEGRAM_BOT_TOKEN",\n'
            '    "YOUR_BOT_TOKEN_HERE",\n'
            ")\n"
        ),
        "tests/__init__.py": "",
        "tests/test_handlers.py": (
            '"""Tests for {project_name} handlers."""\n'
            "\n"
            "import pytest\n"
            "from unittest.mock import AsyncMock, MagicMock, patch\n"
            "\n"
            "from handlers import start, help_command, echo\n"
            "\n"
            "\n"
            "@pytest.fixture()\n"
            "def update():\n"
            '    """Mock Telegram Update."""\n'
            "    upd = MagicMock()\n"
            "    upd.effective_user.mention_html.return_value = (\n"
            '        "<b>TestUser</b>"\n'
            "    )\n"
            "    upd.message.reply_html = AsyncMock()\n"
            "    upd.message.reply_text = AsyncMock()\n"
            '    upd.message.text = "hello"\n'
            "    return upd\n"
            "\n"
            "\n"
            "@pytest.fixture()\n"
            "def context():\n"
            '    """Mock context."""\n'
            "    return MagicMock()\n"
            "\n"
            "\n"
            "@pytest.mark.asyncio\n"
            "async def test_start(update, context):\n"
            "    await start(update, context)\n"
            "    update.message.reply_html.assert_called_once()\n"
            "\n"
            "\n"
            "@pytest.mark.asyncio\n"
            "async def test_help(update, context):\n"
            "    await help_command(update, context)\n"
            "    update.message.reply_text.assert_called_once()\n"
            "\n"
            "\n"
            "@pytest.mark.asyncio\n"
            "async def test_echo(update, context):\n"
            "    await echo(update, context)\n"
            "    update.message.reply_text.assert_called_once_with(\n"
            '        "hello"\n'
            "    )\n"
        ),
        "requirements.txt": (
            "python-telegram-bot>=21.0\n"
            "pytest>=8.0\n"
            "pytest-asyncio>=0.24\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "A Telegram bot built with python-telegram-bot.\n"
            "\n"
            "## Setup\n"
            "\n"
            "1. Create a bot via [@BotFather](https://t.me/BotFather)\n"
            "2. Copy the token\n"
            "\n"
            "```bash\n"
            "python -m venv .venv\n"
            "source .venv/bin/activate  # Linux/macOS\n"
            ".venv\\\\Scripts\\\\activate     # Windows\n"
            "pip install -r requirements.txt\n"
            "```\n"
            "\n"
            "3. Set your token:\n"
            "\n"
            "```bash\n"
            'export TELEGRAM_BOT_TOKEN="your-token-here"\n'
            "```\n"
            "\n"
            "Or edit `config.py` directly.\n"
            "\n"
            "## Run\n"
            "\n"
            "```bash\n"
            "python bot.py\n"
            "```\n"
            "\n"
            "## Test\n"
            "\n"
            "```bash\n"
            "python -m pytest -q\n"
            "```\n"
        ),
        ".gitignore": (
            "__pycache__/\n"
            "*.py[cod]\n"
            "*$py.class\n"
            ".env\n"
            ".venv\n"
            "env/\n"
            "venv/\n"
            ".idea/\n"
            ".vscode/\n"
            "*.swp\n"
            ".DS_Store\n"
            "Thumbs.db\n"
            ".pytest_cache/\n"
            ".coverage\n"
        ),
        "pyproject.toml": (
            "[build-system]\n"
            'requires = ["setuptools>=68.0"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            'name = "{project_name}"\n'
            'version = "0.1.0"\n'
            'description = "Telegram bot"\n'
            'requires-python = ">=3.10"\n'
            "dependencies = [\n"
            '    "python-telegram-bot>=21.0",\n'
            "]\n"
            "\n"
            "[project.optional-dependencies]\n"
            'dev = ["pytest>=8.0", "pytest-asyncio>=0.24"]\n'
        ),
    },
    "post_create_commands": ["pip install -r requirements.txt"],
    "test_command": "python -m pytest -q",
}
