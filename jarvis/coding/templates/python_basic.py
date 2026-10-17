"""Template: Python Basic Project."""

TEMPLATE: dict = {
    "name": "python_basic",
    "display_name": "Python Basic Project",
    "description": "Simple Python project with tests and proper structure",
    "keywords": [
        "python", "script", "скрипт", "програма", "program",
        "basic", "simple", "простой", "проект",
    ],
    "files": {
        "main.py": (
            '"""{project_name} — main entry point."""\n'
            "\n"
            "from utils import greet\n"
            "\n"
            "\n"
            "def main() -> None:\n"
            '    """Run the application."""\n'
            '    name = input("Enter your name: ")\n'
            "    print(greet(name))\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        ),
        "utils.py": (
            '"""Utility functions for {project_name}."""\n'
            "\n"
            "\n"
            "def greet(name: str) -> str:\n"
            '    """Return a greeting message."""\n'
            "    if not name or not name.strip():\n"
            '        return "Hello, World!"\n'
            '    return f"Hello, {{name.strip()}}!"\n'
            "\n"
            "\n"
            "def add(a: int | float, b: int | float) -> int | float:\n"
            '    """Add two numbers."""\n'
            "    return a + b\n"
        ),
        "tests/__init__.py": "",
        "tests/test_main.py": (
            '"""Tests for {project_name}."""\n'
            "\n"
            "from utils import greet, add\n"
            "\n"
            "\n"
            "def test_greet_with_name():\n"
            '    assert greet("Alice") == "Hello, Alice!"\n'
            "\n"
            "\n"
            "def test_greet_empty():\n"
            '    assert greet("") == "Hello, World!"\n'
            "\n"
            "\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
            "    assert add(-1, 1) == 0\n"
            "    assert add(0.1, 0.2) == pytest.approx(0.3)\n"
            "\n"
            "\n"
            "import pytest\n"
        ),
        "requirements.txt": (
            "pytest>=8.0\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "A simple Python project.\n"
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
            "## Run\n"
            "\n"
            "```bash\n"
            "python main.py\n"
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
            "*.so\n"
            "build/\n"
            "dist/\n"
            "*.egg-info/\n"
            "*.egg\n"
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
            "*.db\n"
            "*.sqlite3\n"
            "*.log\n"
        ),
        "pyproject.toml": (
            "[build-system]\n"
            'requires = ["setuptools>=68.0"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            'name = "{project_name}"\n'
            'version = "0.1.0"\n'
            'description = "A simple Python project"\n'
            'requires-python = ">=3.10"\n'
            "dependencies = []\n"
            "\n"
            "[project.optional-dependencies]\n"
            'dev = ["pytest>=8.0"]\n'
        ),
    },
    "post_create_commands": ["pip install -r requirements.txt"],
    "test_command": "python -m pytest -q",
}
