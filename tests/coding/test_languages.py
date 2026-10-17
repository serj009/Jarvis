"""Tests for jarvis.coding.languages — language detection and toolchain config.

Covers:
- detect_language (by files in directory)
- detect_from_description (by keyword matching EN / RU / UK)
- LanguageConfig completeness for all languages
- Template language fields
- Syntax validation per language
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from jarvis.coding.languages import (
    LANGUAGES,
    LanguageConfig,
    detect_from_description,
    detect_language,
    get_language_config,
    get_language_for_extension,
    _validate_python,
    _validate_js_ts,
)
from jarvis.coding.templates import TEMPLATES


# ── detect_language (by files in directory) ─────────────────────────


class TestDetectLanguageByFiles:
    """detect_language must detect the correct language from project files."""

    @pytest.fixture()
    def tmp(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    def test_detect_python_by_requirements(self, tmp):
        (tmp / "requirements.txt").write_text("flask>=3.0\n")
        assert detect_language(tmp) == "python"

    def test_detect_python_by_pyproject(self, tmp):
        (tmp / "pyproject.toml").write_text("[project]\n")
        assert detect_language(tmp) == "python"

    def test_detect_python_by_setup_py(self, tmp):
        (tmp / "setup.py").write_text("from setuptools import setup\n")
        assert detect_language(tmp) == "python"

    def test_detect_javascript_by_package_json(self, tmp):
        (tmp / "package.json").write_text('{"name": "app"}\n')
        assert detect_language(tmp) == "javascript"

    def test_detect_typescript_by_tsconfig(self, tmp):
        (tmp / "package.json").write_text('{"name": "app"}\n')
        (tmp / "tsconfig.json").write_text('{"compilerOptions": {}}\n')
        assert detect_language(tmp) == "typescript"

    def test_detect_go_by_gomod(self, tmp):
        (tmp / "go.mod").write_text("module example.com/app\n")
        assert detect_language(tmp) == "go"

    def test_detect_rust_by_cargo(self, tmp):
        (tmp / "Cargo.toml").write_text("[package]\n")
        assert detect_language(tmp) == "rust"

    def test_detect_java_by_pom(self, tmp):
        (tmp / "pom.xml").write_text("<project></project>\n")
        assert detect_language(tmp) == "java"

    def test_detect_java_by_gradle(self, tmp):
        (tmp / "build.gradle").write_text("apply plugin: 'java'\n")
        assert detect_language(tmp) == "java"

    def test_detect_csharp_by_csproj(self, tmp):
        (tmp / "App.csproj").write_text("<Project></Project>\n")
        assert detect_language(tmp) == "csharp"

    def test_detect_cpp_by_cmake(self, tmp):
        (tmp / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n")
        assert detect_language(tmp) == "cpp"

    def test_detect_by_extension_fallback(self, tmp):
        """When no marker file exists, count extensions."""
        (tmp / "main.go").write_text("package main\n")
        (tmp / "utils.go").write_text("package main\n")
        assert detect_language(tmp) == "go"

    def test_detect_defaults_to_python(self, tmp):
        """Empty directory defaults to python."""
        assert detect_language(tmp) == "python"

    def test_detect_nonexistent_path(self):
        assert detect_language(Path("/nonexistent/path")) == "python"

    def test_tsconfig_overrides_package_json(self, tmp):
        """tsconfig.json should produce 'typescript', not 'javascript'."""
        (tmp / "package.json").write_text("{}\n")
        (tmp / "tsconfig.json").write_text("{}\n")
        assert detect_language(tmp) == "typescript"


# ── detect_from_description (keywords EN / RU / UK) ─────────────────


class TestDetectFromDescriptionEN:
    """English keyword detection."""

    @pytest.mark.parametrize(
        "description, expected",
        [
            ("create a React app", "typescript"),
            ("build a Node.js API", "javascript"),
            ("write a C# console application", "csharp"),
            ("build a REST API with Go", "go"),
            ("create a Rust CLI tool", "rust"),
            ("build a Spring Boot service", "java"),
            ("write a C++ game engine", "cpp"),
            ("create a Flask API", "python"),
            ("create a Django website", "python"),
            ("build a FastAPI service", "python"),
        ],
    )
    def test_detect_en(self, description, expected):
        assert detect_from_description(description) == expected


class TestDetectFromDescriptionRU:
    """Russian keyword detection."""

    @pytest.mark.parametrize(
        "description, expected",
        [
            ("создай приложение на react", "typescript"),
            ("напиши бот на nodejs", "javascript"),
            ("консольное приложение на шарп", "csharp"),
            ("создай сервис на дотнет", "csharp"),
            ("напиши программу на golang", "go"),
            ("создай проект на раст", "rust"),
            ("проект на карго", "rust"),
            ("веб-приложение на питон", "python"),
        ],
    )
    def test_detect_ru(self, description, expected):
        assert detect_from_description(description) == expected


class TestDetectFromDescriptionUK:
    """Ukrainian keyword detection."""

    @pytest.mark.parametrize(
        "description, expected",
        [
            ("створи фронтенд додаток", "typescript"),
            ("напиши нод сервер", "javascript"),
            ("програма на пітон", "python"),
        ],
    )
    def test_detect_uk(self, description, expected):
        assert detect_from_description(description) == expected


class TestDetectFromDescriptionEdge:
    """Edge cases for description detection."""

    def test_empty_returns_python(self):
        assert detect_from_description("") == "python"

    def test_unknown_returns_python(self):
        assert detect_from_description("quantum blockchain AI") == "python"


# ── LanguageConfig completeness ─────────────────────────────────────


class TestLanguageConfigsComplete:
    """All registered languages must have complete, valid configs."""

    def test_all_languages_have_display_name(self):
        for name, cfg in LANGUAGES.items():
            assert cfg.display_name, f"Language '{name}' missing display_name"

    def test_all_languages_have_extensions(self):
        for name, cfg in LANGUAGES.items():
            assert len(cfg.extensions) > 0, f"Language '{name}' has no extensions"

    def test_all_languages_have_package_file(self):
        for name, cfg in LANGUAGES.items():
            assert cfg.package_file, f"Language '{name}' missing package_file"

    def test_expected_languages_exist(self):
        expected = {"python", "javascript", "typescript", "csharp", "java", "go", "rust", "cpp"}
        assert expected == set(LANGUAGES.keys())

    def test_language_name_matches_key(self):
        for key, cfg in LANGUAGES.items():
            assert cfg.name == key, f"Key '{key}' != config name '{cfg.name}'"

    def test_python_has_syntax_validator(self):
        assert LANGUAGES["python"].validate_syntax is not None

    def test_js_has_syntax_validator(self):
        assert LANGUAGES["javascript"].validate_syntax is not None

    def test_ts_has_syntax_validator(self):
        assert LANGUAGES["typescript"].validate_syntax is not None

    def test_languages_without_validator(self):
        """C#, Java, Go, Rust, C++ currently skip syntax validation."""
        for lang in ("csharp", "java", "go", "rust", "cpp"):
            assert LANGUAGES[lang].validate_syntax is None


# ── Syntax validators ───────────────────────────────────────────────


class TestValidatePython:
    """Python syntax validator uses ast.parse."""

    def test_valid_python(self):
        assert _validate_python("x = 1\n", "test.py") is True

    def test_invalid_python_raises(self):
        with pytest.raises(SyntaxError):
            _validate_python("def broken(\n", "test.py")

    def test_multiline_valid(self):
        code = "def add(a, b):\n    return a + b\n"
        assert _validate_python(code, "test.py") is True


class TestValidateJsTs:
    """JS/TS syntax validator checks balanced braces."""

    def test_valid_js(self):
        code = 'function greet(name) {\n  return `Hello, ${name}!`;\n}\n'
        assert _validate_js_ts(code, "test.js") is True

    def test_unbalanced_braces(self):
        code = "function broken() {\n  return 1;\n"
        assert _validate_js_ts(code, "test.js") is False

    def test_unbalanced_brackets(self):
        code = "const arr = [1, 2, 3;\n"
        assert _validate_js_ts(code, "test.js") is False

    def test_string_with_braces_ok(self):
        code = 'const s = "{ not a brace }";\n'
        assert _validate_js_ts(code, "test.js") is True

    def test_template_literal_ok(self):
        code = "const s = `Hello ${name}`;\n"
        assert _validate_js_ts(code, "test.js") is True

    def test_empty_file_ok(self):
        assert _validate_js_ts("", "test.js") is True


# ── get_language_config / get_language_for_extension ─────────────────


class TestGetLanguageConfig:
    """get_language_config returns correct config or Python default."""

    def test_known_language(self):
        cfg = get_language_config("go")
        assert cfg.name == "go"
        assert cfg.test_command == "go test ./..."

    def test_unknown_defaults_to_python(self):
        cfg = get_language_config("brainfuck")
        assert cfg.name == "python"

    def test_python_test_command(self):
        cfg = get_language_config("python")
        assert "pytest" in cfg.test_command

    def test_rust_build_command(self):
        cfg = get_language_config("rust")
        assert cfg.build_command == "cargo build"


class TestGetLanguageForExtension:
    """get_language_for_extension maps extensions correctly."""

    @pytest.mark.parametrize(
        "ext, expected",
        [
            (".py", "python"),
            (".js", "javascript"),
            (".jsx", "javascript"),
            (".ts", "typescript"),
            (".tsx", "typescript"),
            (".cs", "csharp"),
            (".java", "java"),
            (".go", "go"),
            (".rs", "rust"),
            (".cpp", "cpp"),
            (".h", "cpp"),
        ],
    )
    def test_known_extensions(self, ext, expected):
        assert get_language_for_extension(ext) == expected

    def test_unknown_extension_returns_empty(self):
        assert get_language_for_extension(".json") == ""
        assert get_language_for_extension(".md") == ""
        assert get_language_for_extension(".txt") == ""


# ── Template language fields ────────────────────────────────────────


class TestTemplatesForEachLanguage:
    """New templates must declare their language."""

    def test_multilang_templates_have_language_field(self):
        """Non-Python templates must have a 'language' key."""
        multilang = [t for t in TEMPLATES if t.get("language") and t["language"] != "python"]
        assert len(multilang) >= 6, (
            f"Expected at least 6 multi-language templates, got {len(multilang)}"
        )

    def test_all_template_languages_are_valid(self):
        """Every declared language must be in the LANGUAGES registry."""
        for tpl in TEMPLATES:
            lang = tpl.get("language", "python")
            assert lang in LANGUAGES, (
                f"Template '{tpl['name']}' has unknown language '{lang}'"
            )

    @pytest.mark.parametrize(
        "template_name, expected_lang",
        [
            ("js_node_basic", "javascript"),
            ("ts_node_basic", "typescript"),
            ("react_app", "typescript"),
            ("csharp_console", "csharp"),
            ("go_basic", "go"),
            ("rust_basic", "rust"),
        ],
    )
    def test_specific_template_language(self, template_name, expected_lang):
        tpl = next((t for t in TEMPLATES if t["name"] == template_name), None)
        assert tpl is not None, f"Template '{template_name}' not found"
        assert tpl["language"] == expected_lang
