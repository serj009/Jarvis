"""Template: Automation Script."""

TEMPLATE: dict = {
    "name": "automation",
    "display_name": "Automation Script",
    "description": "Windows / cross-platform automation with scheduling",
    "keywords": [
        "automation", "automate", "автоматизация", "автоматизація",
        "schedule", "task", "cron", "планировщик", "задача",
    ],
    "files": {
        "main.py": (
            '"""{project_name} — automation entry point."""\n'
            "\n"
            "import argparse\n"
            "import logging\n"
            "\n"
            "from tasks import (\n"
            "    cleanup_old_files,\n"
            "    organize_downloads,\n"
            "    backup_directory,\n"
            ")\n"
            "from schedule import run_scheduled\n"
            "\n"
            "logging.basicConfig(\n"
            '    format="%(asctime)s - %(levelname)s - %(message)s",\n'
            "    level=logging.INFO,\n"
            ")\n"
            "logger = logging.getLogger(__name__)\n"
            "\n"
            "\n"
            "TASK_MAP = {\n"
            '    "cleanup": cleanup_old_files,\n'
            '    "organize": organize_downloads,\n'
            '    "backup": backup_directory,\n'
            "}\n"
            "\n"
            "\n"
            "def main():\n"
            '    """CLI entry point."""\n'
            "    ap = argparse.ArgumentParser(\n"
            '        description="{project_name} automation"\n'
            "    )\n"
            "    sub = ap.add_subparsers(dest=\"command\")\n"
            "\n"
            "    # Run a single task\n"
            '    run_p = sub.add_parser("run", help="Run a task once")\n'
            "    run_p.add_argument(\n"
            '        "task", choices=list(TASK_MAP.keys()),\n'
            '        help="Task to run",\n'
            "    )\n"
            "    run_p.add_argument(\n"
            '        "path", nargs="?", default=".",\n'
            '        help="Target directory (default: current)",\n'
            "    )\n"
            "\n"
            "    # Schedule tasks\n"
            '    sched_p = sub.add_parser("schedule", help="Run on schedule")\n'
            "    sched_p.add_argument(\n"
            '        "--interval", type=int, default=60,\n'
            '        help="Interval in minutes (default: 60)",\n'
            "    )\n"
            "\n"
            "    args = ap.parse_args()\n"
            "\n"
            '    if args.command == "run":\n'
            "        task_fn = TASK_MAP[args.task]\n"
            "        result = task_fn(args.path)\n"
            '        logger.info(f"Task {{args.task}} complete: {{result}}")\n'
            '    elif args.command == "schedule":\n'
            "        run_scheduled(TASK_MAP, args.interval)\n"
            "    else:\n"
            "        ap.print_help()\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        ),
        "tasks.py": (
            '"""Automation tasks for {project_name}."""\n'
            "\n"
            "import logging\n"
            "import shutil\n"
            "import time\n"
            "from pathlib import Path\n"
            "\n"
            "logger = logging.getLogger(__name__)\n"
            "\n"
            "# File extensions -> folder names\n"
            "ORGANIZE_MAP = {\n"
            '    ".pdf": "Documents",\n'
            '    ".doc": "Documents",\n'
            '    ".docx": "Documents",\n'
            '    ".txt": "Documents",\n'
            '    ".jpg": "Images",\n'
            '    ".jpeg": "Images",\n'
            '    ".png": "Images",\n'
            '    ".gif": "Images",\n'
            '    ".mp4": "Videos",\n'
            '    ".avi": "Videos",\n'
            '    ".mp3": "Music",\n'
            '    ".wav": "Music",\n'
            '    ".zip": "Archives",\n'
            '    ".rar": "Archives",\n'
            '    ".7z": "Archives",\n'
            "}\n"
            "\n"
            "\n"
            "def cleanup_old_files(\n"
            "    directory: str, max_age_days: int = 30\n"
            ") -> dict:\n"
            '    """Delete files older than max_age_days."""\n'
            "    target = Path(directory)\n"
            "    if not target.is_dir():\n"
            '        return {"error": f"Not a directory: {directory}"}\n'
            "\n"
            "    now = time.time()\n"
            "    cutoff = now - (max_age_days * 86400)\n"
            "    deleted = []\n"
            "\n"
            "    for f in target.iterdir():\n"
            "        if f.is_file() and f.stat().st_mtime < cutoff:\n"
            "            logger.info(f\"Deleting old file: {f.name}\")\n"
            "            f.unlink()\n"
            "            deleted.append(f.name)\n"
            "\n"
            '    return {"deleted": len(deleted), "files": deleted}\n'
            "\n"
            "\n"
            "def organize_downloads(directory: str) -> dict:\n"
            '    """Organize files into subfolders by extension."""\n'
            "    target = Path(directory)\n"
            "    if not target.is_dir():\n"
            '        return {"error": f"Not a directory: {directory}"}\n'
            "\n"
            "    moved = []\n"
            "    for f in target.iterdir():\n"
            "        if not f.is_file():\n"
            "            continue\n"
            "        folder_name = ORGANIZE_MAP.get(\n"
            '            f.suffix.lower(), "Other"\n'
            "        )\n"
            "        dest_dir = target / folder_name\n"
            "        dest_dir.mkdir(exist_ok=True)\n"
            "        shutil.move(str(f), str(dest_dir / f.name))\n"
            "        moved.append(f.name)\n"
            '        logger.info(f"Moved {{f.name}} -> {{folder_name}}/")\n'
            "\n"
            '    return {"moved": len(moved), "files": moved}\n'
            "\n"
            "\n"
            "def backup_directory(\n"
            '    directory: str, backup_dir: str = "backups"\n'
            ") -> dict:\n"
            '    """Create a zip backup of a directory."""\n'
            "    target = Path(directory)\n"
            "    if not target.is_dir():\n"
            '        return {"error": f"Not a directory: {directory}"}\n'
            "\n"
            "    backup_path = Path(backup_dir)\n"
            "    backup_path.mkdir(exist_ok=True)\n"
            "\n"
            "    import datetime\n"
            "    timestamp = datetime.datetime.now().strftime(\n"
            '        "%Y%m%d_%H%M%S"\n'
            "    )\n"
            '    archive_name = f"{{target.name}}_{{timestamp}}"\n'
            "    archive_path = shutil.make_archive(\n"
            '        str(backup_path / archive_name), "zip", str(target)\n'
            "    )\n"
            '    logger.info(f"Backup created: {{archive_path}}")\n'
            '    return {"archive": archive_path}\n'
        ),
        "schedule.py": (
            '"""Scheduler for {project_name}."""\n'
            "\n"
            "import logging\n"
            "import time\n"
            "from typing import Callable\n"
            "\n"
            "logger = logging.getLogger(__name__)\n"
            "\n"
            "\n"
            "def run_scheduled(\n"
            "    tasks: dict[str, Callable],\n"
            "    interval_minutes: int = 60,\n"
            ") -> None:\n"
            '    """Run all tasks on a recurring schedule."""\n'
            "    logger.info(\n"
            '        f"Starting scheduler: {{len(tasks)}} tasks "\n'
            '        f"every {{interval_minutes}} min"\n'
            "    )\n"
            "\n"
            "    try:\n"
            "        while True:\n"
            "            for name, fn in tasks.items():\n"
            "                try:\n"
            '                    logger.info(f"Running task: {{name}}")\n'
            '                    fn(".")\n'
            "                except Exception as exc:\n"
            "                    logger.error(\n"
            '                        f"Task {{name}} failed: {{exc}}"\n'
            "                    )\n"
            "\n"
            '            logger.info(f"Sleeping {{interval_minutes}} min")\n'
            "            time.sleep(interval_minutes * 60)\n"
            "    except KeyboardInterrupt:\n"
            '        logger.info("Scheduler stopped")\n'
        ),
        "tests/__init__.py": "",
        "tests/test_tasks.py": (
            '"""Tests for {project_name} tasks."""\n'
            "\n"
            "import os\n"
            "import tempfile\n"
            "from pathlib import Path\n"
            "\n"
            "from tasks import (\n"
            "    organize_downloads,\n"
            "    backup_directory,\n"
            "    cleanup_old_files,\n"
            "    ORGANIZE_MAP,\n"
            ")\n"
            "\n"
            "\n"
            "def test_organize_downloads():\n"
            "    with tempfile.TemporaryDirectory() as tmp:\n"
            "        # Create test files\n"
            '        (Path(tmp) / "doc.pdf").write_text("pdf")\n'
            '        (Path(tmp) / "pic.jpg").write_text("jpg")\n'
            '        (Path(tmp) / "unknown.xyz").write_text("xyz")\n'
            "\n"
            "        result = organize_downloads(tmp)\n"
            '        assert result["moved"] == 3\n'
            "        assert (Path(tmp) / \"Documents\" / \"doc.pdf\").exists()\n"
            "        assert (Path(tmp) / \"Images\" / \"pic.jpg\").exists()\n"
            "        assert (Path(tmp) / \"Other\" / \"unknown.xyz\").exists()\n"
            "\n"
            "\n"
            "def test_backup_directory():\n"
            "    with tempfile.TemporaryDirectory() as tmp:\n"
            "        src = Path(tmp) / \"source\"\n"
            "        src.mkdir()\n"
            '        (src / "file.txt").write_text("hello")\n'
            "        backup_dir = str(Path(tmp) / \"backups\")\n"
            "\n"
            "        result = backup_directory(str(src), backup_dir)\n"
            '        assert "archive" in result\n'
            '        assert result["archive"].endswith(".zip")\n'
            "\n"
            "\n"
            "def test_cleanup_invalid_dir():\n"
            '    result = cleanup_old_files("/nonexistent/path")\n'
            '    assert "error" in result\n'
            "\n"
            "\n"
            "def test_organize_map_has_common_extensions():\n"
            '    assert ".pdf" in ORGANIZE_MAP\n'
            '    assert ".jpg" in ORGANIZE_MAP\n'
            '    assert ".mp4" in ORGANIZE_MAP\n'
        ),
        "requirements.txt": (
            "pytest>=8.0\n"
        ),
        "README.md": (
            "# {project_name}\n"
            "\n"
            "Automation scripts for file management and scheduling.\n"
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
            "# Run a task once\n"
            "python main.py run cleanup ~/Downloads\n"
            "python main.py run organize ~/Downloads\n"
            "python main.py run backup ~/Documents\n"
            "\n"
            "# Run on schedule (every 60 minutes)\n"
            "python main.py schedule --interval 60\n"
            "```\n"
            "\n"
            "## Available Tasks\n"
            "\n"
            "| Task | Description |\n"
            "|------|-------------|\n"
            "| cleanup | Delete files older than 30 days |\n"
            "| organize | Sort files into folders by extension |\n"
            "| backup | Create zip backup of a directory |\n"
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
            "backups/\n"
        ),
        "pyproject.toml": (
            "[build-system]\n"
            'requires = ["setuptools>=68.0"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            'name = "{project_name}"\n'
            'version = "0.1.0"\n'
            'description = "Automation scripts"\n'
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
