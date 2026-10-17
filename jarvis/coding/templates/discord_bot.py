"""Template: Discord Bot."""

TEMPLATE: dict = {
    "name": "discord_bot",
    "display_name": "Discord Bot",
    "description": "Discord bot with discord.py and cogs",
    "keywords": [
        "discord", "бот", "bot", "діскорд", "дискорд",
        "discord.py",
    ],
    "files": {
        "bot.py": (
            '"""{project_name} — Discord bot entry point."""\n'
            "\n"
            "import asyncio\n"
            "import logging\n"
            "\n"
            "import discord\n"
            "from discord.ext import commands\n"
            "\n"
            "from config import BOT_TOKEN, PREFIX\n"
            "\n"
            "logging.basicConfig(\n"
            '    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",\n'
            "    level=logging.INFO,\n"
            ")\n"
            "logger = logging.getLogger(__name__)\n"
            "\n"
            "intents = discord.Intents.default()\n"
            "intents.message_content = True\n"
            "\n"
            "bot = commands.Bot(command_prefix=PREFIX, intents=intents)\n"
            "\n"
            "\n"
            "@bot.event\n"
            "async def on_ready():\n"
            '    """Called when bot is ready."""\n'
            '    logger.info(f"Logged in as {{bot.user}} (ID: {{bot.user.id}})")\n'
            "\n"
            "\n"
            "async def load_cogs():\n"
            '    """Load all cogs."""\n'
            '    await bot.load_extension("cogs.general")\n'
            '    logger.info("Cogs loaded")\n'
            "\n"
            "\n"
            "async def main():\n"
            '    """Start the bot."""\n'
            "    async with bot:\n"
            "        await load_cogs()\n"
            "        await bot.start(BOT_TOKEN)\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    asyncio.run(main())\n"
        ),
        "config.py": (
            '"""Configuration for {project_name}."""\n'
            "\n"
            "import os\n"
            "\n"
            "BOT_TOKEN = os.environ.get(\n"
            '    "DISCORD_BOT_TOKEN",\n'
            '    "YOUR_BOT_TOKEN_HERE",\n'
            ")\n"
            'PREFIX = os.environ.get("BOT_PREFIX", "!")\n'
        ),
        "cogs/__init__.py": "",
        "cogs/general.py": (
            '"""General commands cog for {project_name}."""\n'
            "\n"
            "import discord\n"
            "from discord.ext import commands\n"
            "\n"
            "\n"
            "class General(commands.Cog):\n"
            '    """General bot commands."""\n'
            "\n"
            "    def __init__(self, bot: commands.Bot):\n"
            "        self.bot = bot\n"
            "\n"
            "    @commands.command()\n"
            "    async def ping(self, ctx: commands.Context):\n"
            '        """Check bot latency."""\n'
            "        latency = round(self.bot.latency * 1000)\n"
            '        await ctx.send(f"Pong! {{latency}}ms")\n'
            "\n"
            "    @commands.command()\n"
            "    async def hello(self, ctx: commands.Context):\n"
            '        """Say hello."""\n'
            '        await ctx.send(f"Hello, {{ctx.author.display_name}}!")\n'
            "\n"
            "    @commands.command()\n"
            "    async def info(self, ctx: commands.Context):\n"
            '        """Show bot info."""\n'
            "        embed = discord.Embed(\n"
            '            title="{project_name}",\n'
            '            description="A Discord bot",\n'
            "            color=discord.Color.blue(),\n"
            "        )\n"
            '        embed.add_field(name="Prefix", value=self.bot.command_prefix)\n'
            "        embed.add_field(\n"
            '            name="Guilds", value=str(len(self.bot.guilds))\n'
            "        )\n"
            "        await ctx.send(embed=embed)\n"
            "\n"
            "\n"
            "async def setup(bot: commands.Bot):\n"
            '    """Add cog to bot."""\n'
            "    await bot.add_cog(General(bot))\n"
        ),
        "tests/__init__.py": "",
        "tests/test_bot.py": (
            '"""Tests for {project_name} bot."""\n'
            "\n"
            "import pytest\n"
            "from unittest.mock import MagicMock, AsyncMock\n"
            "\n"
            "from cogs.general import General\n"
            "\n"
            "\n"
            "@pytest.fixture()\n"
            "def bot():\n"
            '    """Mock bot."""\n'
            "    b = MagicMock()\n"
            "    b.latency = 0.05\n"
            "    b.guilds = [MagicMock(), MagicMock()]\n"
            "    return b\n"
            "\n"
            "\n"
            "@pytest.fixture()\n"
            "def cog(bot):\n"
            '    """General cog instance."""\n'
            "    return General(bot)\n"
            "\n"
            "\n"
            "@pytest.fixture()\n"
            "def ctx():\n"
            '    """Mock context."""\n'
            "    c = MagicMock()\n"
            "    c.send = AsyncMock()\n"
            '    c.author.display_name = "TestUser"\n'
            "    return c\n"
            "\n"
            "\n"
            "@pytest.mark.asyncio\n"
            "async def test_ping(cog, ctx):\n"
            "    await cog.ping(ctx)\n"
            "    ctx.send.assert_called_once()\n"
            '    assert "Pong" in ctx.send.call_args[0][0]\n'
            "\n"
            "\n"
            "@pytest.mark.asyncio\n"
            "async def test_hello(cog, ctx):\n"
            "    await cog.hello(ctx)\n"
            "    ctx.send.assert_called_once()\n"
            '    assert "TestUser" in ctx.send.call_args[0][0]\n'
        ),
        "requirements.txt": (
            "discord.py>=2.4\n"
            "pytest>=8.0\n"
            "pytest-asyncio>=0.24\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "A Discord bot built with discord.py.\n"
            "\n"
            "## Setup\n"
            "\n"
            "1. Create an app at [Discord Developer Portal]"
            "(https://discord.com/developers/applications)\n"
            "2. Create a bot and copy the token\n"
            "3. Invite bot to your server with the OAuth2 URL generator\n"
            "\n"
            "```bash\n"
            "python -m venv .venv\n"
            "source .venv/bin/activate  # Linux/macOS\n"
            ".venv\\\\Scripts\\\\activate     # Windows\n"
            "pip install -r requirements.txt\n"
            "```\n"
            "\n"
            "4. Set your token:\n"
            "\n"
            "```bash\n"
            'export DISCORD_BOT_TOKEN="your-token-here"\n'
            "```\n"
            "\n"
            "## Run\n"
            "\n"
            "```bash\n"
            "python bot.py\n"
            "```\n"
            "\n"
            "## Commands\n"
            "\n"
            "| Command | Description |\n"
            "|---------|-------------|\n"
            "| !ping | Check latency |\n"
            "| !hello | Say hello |\n"
            "| !info | Bot info |\n"
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
            'description = "Discord bot"\n'
            'requires-python = ">=3.10"\n'
            "dependencies = [\n"
            '    "discord.py>=2.4",\n'
            "]\n"
            "\n"
            "[project.optional-dependencies]\n"
            'dev = ["pytest>=8.0", "pytest-asyncio>=0.24"]\n'
        ),
    },
    "post_create_commands": ["pip install -r requirements.txt"],
    "test_command": "python -m pytest -q",
}
