"""Template: Command-Line Tool."""

TEMPLATE: dict = {
    "name": "cli_tool",
    "display_name": "CLI Tool",
    "description": "Command-line tool with click and tests",
    "keywords": [
        "cli", "command", "terminal", "консоль", "утилита",
        "утиліта", "командная строка", "command-line", "tool",
    ],
    "files": {
        "cli.py": (
            '"""{project_name} — command-line interface."""\n'
            "\n"
            "import click\n"
            "\n"
            "from core import process_text, count_words, reverse_string\n"
            "\n"
            "\n"
            "@click.group()\n"
            '@click.version_option(version="0.1.0")\n'
            "def main():\n"
            '    """{project_name} CLI tool."""\n'
            "\n"
            "\n"
            "@main.command()\n"
            '@click.argument("text")\n'
            '@click.option("--upper", is_flag=True, help="Convert to uppercase")\n'
            '@click.option("--lower", is_flag=True, help="Convert to lowercase")\n'
            "def process(text: str, upper: bool, lower: bool):\n"
            '    """Process text with transformations."""\n'
            "    result = process_text(text, upper=upper, lower=lower)\n"
            "    click.echo(result)\n"
            "\n"
            "\n"
            "@main.command()\n"
            '@click.argument("text")\n'
            "def words(text: str):\n"
            '    """Count words in text."""\n'
            '    click.echo(f"Words: {{count_words(text)}}")\n'
            "\n"
            "\n"
            "@main.command()\n"
            '@click.argument("text")\n'
            "def reverse(text: str):\n"
            '    """Reverse text."""\n'
            "    click.echo(reverse_string(text))\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        ),
        "core.py": (
            '"""Core logic for {project_name}."""\n'
            "\n"
            "\n"
            "def process_text(\n"
            "    text: str,\n"
            "    upper: bool = False,\n"
            "    lower: bool = False,\n"
            ") -> str:\n"
            '    """Process text with optional transformations."""\n'
            "    if upper:\n"
            "        return text.upper()\n"
            "    if lower:\n"
            "        return text.lower()\n"
            "    return text\n"
            "\n"
            "\n"
            "def count_words(text: str) -> int:\n"
            '    """Count words in text."""\n'
            "    return len(text.split())\n"
            "\n"
            "\n"
            "def reverse_string(text: str) -> str:\n"
            '    """Reverse a string."""\n'
            "    return text[::-1]\n"
        ),
        "tests/__init__.py": "",
        "tests/test_cli.py": (
            '"""Tests for {project_name} CLI."""\n'
            "\n"
            "from click.testing import CliRunner\n"
            "\n"
            "from cli import main\n"
            "from core import process_text, count_words, reverse_string\n"
            "\n"
            "\n"
            "def test_process_text_upper():\n"
            '    assert process_text("hello", upper=True) == "HELLO"\n'
            "\n"
            "\n"
            "def test_process_text_lower():\n"
            '    assert process_text("HELLO", lower=True) == "hello"\n'
            "\n"
            "\n"
            "def test_process_text_default():\n"
            '    assert process_text("Hello") == "Hello"\n'
            "\n"
            "\n"
            "def test_count_words():\n"
            '    assert count_words("one two three") == 3\n'
            '    assert count_words("") == 0\n'
            "\n"
            "\n"
            "def test_reverse():\n"
            '    assert reverse_string("abc") == "cba"\n'
            "\n"
            "\n"
            "def test_cli_process():\n"
            "    runner = CliRunner()\n"
            '    result = runner.invoke(main, ["process", "hello", "--upper"])\n'
            "    assert result.exit_code == 0\n"
            '    assert "HELLO" in result.output\n'
            "\n"
            "\n"
            "def test_cli_words():\n"
            "    runner = CliRunner()\n"
            '    result = runner.invoke(main, ["words", "one two three"])\n'
            "    assert result.exit_code == 0\n"
            '    assert "3" in result.output\n'
            "\n"
            "\n"
            "def test_cli_reverse():\n"
            "    runner = CliRunner()\n"
            '    result = runner.invoke(main, ["reverse", "abc"])\n'
            "    assert result.exit_code == 0\n"
            '    assert "cba" in result.output\n'
        ),
        "requirements.txt": (
            "click>=8.1\n"
            "pytest>=8.0\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "A command-line tool built with Click.\n"
            "\n"
            "## Setup\n"
            "\n"
            "```bash\n"
            "python -m venv .venv\n"
            "source .venv/bin/activate  # Linux/macOS\n"
            ".venv\\\\Scripts\\\\activate     # Windows\n"
            "pip install -r requirements.txt\n"
            "```\n"
            "\n"
            "## Usage\n"
            "\n"
            "```bash\n"
            '# Process text\n'
            'python cli.py process "Hello World" --upper\n'
            "\n"
            "# Count words\n"
            'python cli.py words "one two three"\n'
            "\n"
            "# Reverse text\n"
            'python cli.py reverse "hello"\n'
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
            'description = "Command-line tool"\n'
            'requires-python = ">=3.10"\n'
            "dependencies = [\n"
            '    "click>=8.1",\n'
            "]\n"
            "\n"
            "[project.optional-dependencies]\n"
            'dev = ["pytest>=8.0"]\n'
            "\n"
            "[project.scripts]\n"
            '{project_name} = "cli:main"\n'
        ),
        "setup.py": (
            '"""Fallback setup.py for {project_name}."""\n'
            "\n"
            "from setuptools import setup\n"
            "\n"
            "setup(\n"
            '    name="{project_name}",\n'
            '    version="0.1.0",\n'
            "    py_modules=[\"cli\", \"core\"],\n"
            "    install_requires=[\"click>=8.1\"],\n"
            "    entry_points={\n"
            '        "console_scripts": ["{project_name}=cli:main"],\n'
            "    },\n"
            ")\n"
        ),
    },
    "post_create_commands": ["pip install -r requirements.txt"],
    "test_command": "python -m pytest -q",
}
