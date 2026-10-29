"""Tests for jarvis.coding.scaffolder — project scaffolding system."""

import tempfile
from pathlib import Path

import pytest

from jarvis.coding.scaffolder import (
    REQUIRED_TEMPLATE_KEYS,
    find_template,
    get_template,
    list_templates,
    scaffold_project,
)
from jarvis.coding.templates import TEMPLATES

# ── find_template ───────────────────────────────────────────────────

class TestFindTemplateByKeyword:
    """find_template must match templates by keywords in EN / RU / UK."""

    # English keywords
    @pytest.mark.parametrize(
        "description, expected_name",
        [
            ("create a flask API", "flask_api"),
            ("build a REST server with flask", "flask_api"),
            ("fastapi async service", "fastapi"),
            ("make a telegram bot", "telegram_bot"),
            ("discord bot with cogs", "discord_bot"),
            ("command-line CLI tool", "cli_tool"),
            ("web scraper with beautifulsoup", "scraper"),
            ("data analysis with pandas", "data_analysis"),
            ("pygame game 2d", "pygame_game"),
            ("automate file tasks", "automation"),
            ("simple python script", "python_basic"),
        ],
    )
    def test_find_template_en(self, description, expected_name):
        tpl = find_template(description)
        assert tpl is not None, f"No template found for: {description!r}"
        assert tpl["name"] == expected_name

    # Russian keywords
    @pytest.mark.parametrize(
        "description, expected_name",
        [
            ("создай бота для телеграм", "telegram_bot"),
            ("создай сервер на flask", "flask_api"),
            ("напиши парсер сайтов", "scraper"),
            ("анализ данных pandas", "data_analysis"),
            ("простой скрипт python", "python_basic"),
            ("автоматизация задач", "automation"),
            ("консоль утилита", "cli_tool"),
            ("создай игру pygame", "pygame_game"),
        ],
    )
    def test_find_template_ru(self, description, expected_name):
        tpl = find_template(description)
        assert tpl is not None, f"No template found for: {description!r}"
        assert tpl["name"] == expected_name

    # Ukrainian keywords
    @pytest.mark.parametrize(
        "description, expected_name",
        [
            ("створи програму python", "python_basic"),
            ("напиши апі на flask", "flask_api"),
            ("бот для діскорд", "discord_bot"),
            ("аналіз дані", "data_analysis"),
            ("утиліта командная строка", "cli_tool"),
        ],
    )
    def test_find_template_uk(self, description, expected_name):
        tpl = find_template(description)
        assert tpl is not None, f"No template found for: {description!r}"
        assert tpl["name"] == expected_name


# ── unknown description → None ──────────────────────────────────────

class TestUnknownDescription:
    """find_template returns None for empty or unrecognized input."""

    def test_empty_string(self):
        assert find_template("") is None

    def test_unknown_description_returns_none(self):
        result = find_template(
            "quantum computing neural interface blockchain"
        )
        # Either None or a very low-confidence match; should not crash.
        # The function may return a template if a keyword accidentally
        # matches, but for this specific gibberish it should be None.
        assert result is None


# ── scaffold_project ────────────────────────────────────────────────

class TestScaffoldCreatesFiles:
    """scaffold_project must create all files on disk."""

    @pytest.fixture()
    def tmp(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    def test_scaffold_creates_files(self, tmp):
        tpl = get_template("python_basic")
        assert tpl is not None

        target = tmp / "my_project"
        created = scaffold_project(tpl, "my_project", target)

        assert len(created) > 0
        assert len(created) == len(tpl["files"])

        # Every file in the template must exist on disk
        for rel_path in tpl["files"]:
            abs_path = target / rel_path
            assert abs_path.exists(), f"Missing: {rel_path}"

    def test_scaffold_creates_nested_dirs(self, tmp):
        tpl = get_template("flask_api")
        assert tpl is not None

        target = tmp / "api_project"
        scaffold_project(tpl, "api_project", target)

        assert (target / "app" / "__init__.py").exists()
        assert (target / "app" / "routes.py").exists()
        assert (target / "tests" / "conftest.py").exists()

    def test_scaffold_all_templates(self, tmp):
        """Every template scaffolds without error."""
        for tpl in TEMPLATES:
            target = tmp / tpl["name"]
            created = scaffold_project(tpl, "test_proj", target)
            assert len(created) == len(tpl["files"]), (
                f"Template {tpl['name']}: expected {len(tpl['files'])} "
                f"files, got {len(created)}"
            )


# ── {project_name} replacement ──────────────────────────────────────

class TestScaffoldReplacesProjectName:
    """scaffold_project must replace {project_name} in all files."""

    @pytest.fixture()
    def tmp(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    def test_scaffold_replaces_project_name(self, tmp):
        tpl = get_template("python_basic")
        assert tpl is not None

        target = tmp / "cool_app"
        scaffold_project(tpl, "cool_app", target)

        # README should contain the project name
        readme = (target / "README.md").read_text(encoding="utf-8")
        assert "cool_app" in readme

        # pyproject.toml should contain the project name
        toml = (target / "pyproject.toml").read_text(encoding="utf-8")
        assert "cool_app" in toml

        # No un-replaced placeholders should remain
        for rel_path in tpl["files"]:
            content = (target / rel_path).read_text(encoding="utf-8")
            assert "{project_name}" not in content, (
                f"Un-replaced placeholder in {rel_path}"
            )

    def test_placeholder_replaced_in_all_templates(self, tmp):
        """No template leaves {project_name} un-replaced."""
        for tpl in TEMPLATES:
            target = tmp / tpl["name"]
            scaffold_project(tpl, "my_proj", target)

            for rel_path in tpl["files"]:
                content = (target / rel_path).read_text(encoding="utf-8")
                assert "{project_name}" not in content, (
                    f"Template '{tpl['name']}', file '{rel_path}': "
                    f"un-replaced {{project_name}}"
                )


# ── Template structure validation ───────────────────────────────────

class TestAllTemplatesHaveRequiredFields:
    """Every template must have all required keys."""

    def test_all_templates_have_required_fields(self):
        for tpl in TEMPLATES:
            missing = REQUIRED_TEMPLATE_KEYS - set(tpl.keys())
            assert not missing, (
                f"Template '{tpl.get('name', '???')}' missing keys: "
                f"{missing}"
            )

    def test_all_templates_have_non_empty_keywords(self):
        for tpl in TEMPLATES:
            assert len(tpl["keywords"]) > 0, (
                f"Template '{tpl['name']}' has empty keywords"
            )

    def test_all_templates_have_non_empty_files(self):
        for tpl in TEMPLATES:
            assert len(tpl["files"]) > 0, (
                f"Template '{tpl['name']}' has no files"
            )

    def test_template_names_are_unique(self):
        names = [t["name"] for t in TEMPLATES]
        assert len(names) == len(set(names)), (
            f"Duplicate template names: {names}"
        )


class TestAllTemplatesHaveTestFile:
    """Every template must include at least one test file."""

    def test_all_templates_have_test_file(self):
        for tpl in TEMPLATES:
            test_files = [
                f for f in tpl["files"]
                if "test_" in f
                or "_test." in f
                or ".test." in f
                or ".spec." in f
            ]
            assert len(test_files) > 0, (
                f"Template '{tpl['name']}' has no test files"
            )


class TestAllTemplatesHaveGitignore:
    """Every template must include a .gitignore."""

    def test_all_templates_have_gitignore(self):
        for tpl in TEMPLATES:
            assert ".gitignore" in tpl["files"], (
                f"Template '{tpl['name']}' missing .gitignore"
            )


class TestAllTemplatesHaveReadme:
    """Every template must include a README.md."""

    def test_all_templates_have_readme(self):
        for tpl in TEMPLATES:
            assert "README.md" in tpl["files"], (
                f"Template '{tpl['name']}' missing README.md"
            )


class TestAllTemplatesHaveRequirements:
    """Every template must include a dependency / package file."""

    def test_all_templates_have_requirements(self):
        for tpl in TEMPLATES:
            lang = tpl.get("language", "python")
            if lang == "python":
                assert "requirements.txt" in tpl["files"], (
                    f"Template '{tpl['name']}' missing requirements.txt"
                )
            elif lang in ("javascript", "typescript"):
                assert "package.json" in tpl["files"], (
                    f"Template '{tpl['name']}' missing package.json"
                )
            elif lang == "go":
                assert "go.mod" in tpl["files"], (
                    f"Template '{tpl['name']}' missing go.mod"
                )
            elif lang == "rust":
                assert "Cargo.toml" in tpl["files"], (
                    f"Template '{tpl['name']}' missing Cargo.toml"
                )
            elif lang == "csharp":
                has_csproj = any(f.endswith(".csproj") for f in tpl["files"])
                assert has_csproj, (
                    f"Template '{tpl['name']}' missing .csproj file"
                )


class TestAllTemplatesHaveProjectConfig:
    """Every template must include a project configuration file."""

    def test_all_templates_have_project_config(self):
        for tpl in TEMPLATES:
            lang = tpl.get("language", "python")
            if lang == "python":
                has_toml = "pyproject.toml" in tpl["files"]
                has_setup = "setup.py" in tpl["files"]
                assert has_toml or has_setup, (
                    f"Template '{tpl['name']}' missing pyproject.toml and setup.py"
                )
            elif lang in ("javascript", "typescript"):
                assert "package.json" in tpl["files"], (
                    f"Template '{tpl['name']}' missing package.json"
                )
            elif lang == "rust":
                assert "Cargo.toml" in tpl["files"], (
                    f"Template '{tpl['name']}' missing Cargo.toml"
                )
            elif lang == "go":
                assert "go.mod" in tpl["files"], (
                    f"Template '{tpl['name']}' missing go.mod"
                )
            elif lang == "csharp":
                has_csproj = any(f.endswith(".csproj") for f in tpl["files"])
                assert has_csproj, (
                    f"Template '{tpl['name']}' missing .csproj file"
                )


# ── list_templates / get_template ───────────────────────────────────

class TestListAndGetTemplates:
    """Helper functions for template retrieval."""

    def test_list_templates_returns_summaries(self):
        summaries = list_templates()
        assert len(summaries) == len(TEMPLATES)
        for s in summaries:
            assert "name" in s
            assert "display_name" in s
            assert "files" not in s  # summaries exclude files

    def test_get_template_by_name(self):
        tpl = get_template("flask_api")
        assert tpl is not None
        assert tpl["name"] == "flask_api"

    def test_get_template_unknown(self):
        assert get_template("nonexistent_template") is None

    def test_minimum_template_count(self):
        """At least 8 templates must be available."""
        assert len(TEMPLATES) >= 8
