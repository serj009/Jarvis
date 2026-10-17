"""Language detection and per-language toolchain configuration.

Extends CodingAgent beyond Python to support JavaScript/TypeScript,
C#, Java, Go, Rust, and C/C++.  Each language has a ``LanguageConfig``
that describes how to build, test, lint, and validate code.

Usage::

    from jarvis.coding.languages import detect_language, get_language_config

    lang = detect_language(Path("./my_project"))
    cfg  = get_language_config(lang)
    print(cfg.test_command)   # "npm test", "go test ./...", etc.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Syntax validators
# ---------------------------------------------------------------------------

def _validate_python(content: str, filepath: str) -> bool:
    """Validate Python syntax via ``ast.parse``.

    Raises :class:`SyntaxError` on invalid syntax so the caller can
    capture the detailed error message.
    """
    ast.parse(content, filename=filepath)
    return True


def _validate_js_ts(content: str, filepath: str) -> bool:
    """Basic JS / TS syntax check — balanced braces, brackets, parens.

    This is intentionally lightweight (no full parse).  It catches the
    most common LLM mistakes (unclosed blocks) without requiring a
    Node.js runtime.
    """
    pairs = {"{": "}", "[": "]", "(": ")"}
    stack: list[str] = []
    in_string: Optional[str] = None
    escape = False

    for ch in content:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if in_string:
            if ch == in_string:
                in_string = None
            continue
        if ch in ("\"", "'", "`"):
            in_string = ch
            continue
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            if not stack or stack[-1] != ch:
                return False
            stack.pop()

    return len(stack) == 0


# ---------------------------------------------------------------------------
# LanguageConfig dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LanguageConfig:
    """Per-language toolchain configuration.

    Attributes
    ----------
    name : str
        Machine key (``"python"``, ``"javascript"``, …).
    display_name : str
        Human-readable label (``"Python"``, ``"JavaScript"``, …).
    extensions : tuple[str, ...]
        File extensions that belong to this language.
    test_command : str | None
        Shell command to run the test suite.
    run_command : str | None
        Shell command to run a single file (``{file}`` placeholder).
    build_command : str | None
        Shell command to compile / build.
    lint_command : str | None
        Shell command to lint the project.
    package_file : str
        Primary dependency / build file name.
    install_command : str | None
        Shell command to install dependencies.
    validate_syntax : callable | None
        ``(content, filepath) → bool``.  May raise ``SyntaxError``.
    system_prompt_rules : str
        Extra coding-convention rules injected into the system prompt.
    """

    name: str
    display_name: str
    extensions: tuple
    test_command: Optional[str]
    run_command: Optional[str]
    build_command: Optional[str]
    lint_command: Optional[str]
    package_file: str
    install_command: Optional[str]
    validate_syntax: Optional[Callable] = None
    system_prompt_rules: str = ""


# ---------------------------------------------------------------------------
# Language registry
# ---------------------------------------------------------------------------

LANGUAGES: dict[str, LanguageConfig] = {
    "python": LanguageConfig(
        name="python",
        display_name="Python",
        extensions=(".py",),
        test_command="python -m pytest -q",
        run_command="python {file}",
        build_command=None,
        lint_command="ruff check",
        package_file="requirements.txt",
        install_command="pip install -r requirements.txt",
        validate_syntax=_validate_python,
        system_prompt_rules=(
            "Follow PEP 8 style. Include type hints and docstrings.\n"
            "Include: __init__.py, requirements.txt, README.md, .gitignore."
        ),
    ),
    "javascript": LanguageConfig(
        name="javascript",
        display_name="JavaScript",
        extensions=(".js", ".jsx", ".mjs", ".cjs"),
        test_command="npm test",
        run_command="node {file}",
        build_command=None,
        lint_command="npx eslint .",
        package_file="package.json",
        install_command="npm install",
        validate_syntax=_validate_js_ts,
        system_prompt_rules=(
            "Follow modern ES6+ conventions. Use JSDoc comments.\n"
            "Include: package.json, README.md, .gitignore."
        ),
    ),
    "typescript": LanguageConfig(
        name="typescript",
        display_name="TypeScript",
        extensions=(".ts", ".tsx"),
        test_command="npm test",
        run_command="npx ts-node {file}",
        build_command="npx tsc",
        lint_command="npx eslint .",
        package_file="package.json",
        install_command="npm install",
        validate_syntax=_validate_js_ts,
        system_prompt_rules=(
            "Use strict TypeScript with proper types (avoid `any`).\n"
            "Include: package.json, tsconfig.json, README.md, .gitignore."
        ),
    ),
    "csharp": LanguageConfig(
        name="csharp",
        display_name="C#",
        extensions=(".cs",),
        test_command="dotnet test",
        run_command="dotnet run",
        build_command="dotnet build",
        lint_command="dotnet format --verify-no-changes",
        package_file=".csproj",
        install_command="dotnet restore",
        validate_syntax=None,
        system_prompt_rules=(
            "Follow C# coding conventions. Use nullable reference types.\n"
            "Include: .csproj, Program.cs, README.md, .gitignore."
        ),
    ),
    "java": LanguageConfig(
        name="java",
        display_name="Java",
        extensions=(".java",),
        test_command="mvn test",
        run_command="java {file}",
        build_command="mvn compile",
        lint_command=None,
        package_file="pom.xml",
        install_command="mvn dependency:resolve",
        validate_syntax=None,
        system_prompt_rules=(
            "Follow Java coding conventions. Use proper packages.\n"
            "Include: pom.xml (or build.gradle), README.md, .gitignore."
        ),
    ),
    "go": LanguageConfig(
        name="go",
        display_name="Go",
        extensions=(".go",),
        test_command="go test ./...",
        run_command="go run {file}",
        build_command="go build ./...",
        lint_command="go vet ./...",
        package_file="go.mod",
        install_command="go mod tidy",
        validate_syntax=None,
        system_prompt_rules=(
            "Follow Go conventions (gofmt, effective Go). Handle errors explicitly.\n"
            "Include: go.mod, README.md, .gitignore."
        ),
    ),
    "rust": LanguageConfig(
        name="rust",
        display_name="Rust",
        extensions=(".rs",),
        test_command="cargo test",
        run_command="cargo run",
        build_command="cargo build",
        lint_command="cargo clippy",
        package_file="Cargo.toml",
        install_command=None,
        validate_syntax=None,
        system_prompt_rules=(
            "Follow Rust idioms. Prefer Result/Option over panics.\n"
            "Include: Cargo.toml, src/main.rs, README.md, .gitignore."
        ),
    ),
    "cpp": LanguageConfig(
        name="cpp",
        display_name="C/C++",
        extensions=(".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx"),
        test_command=None,
        run_command=None,
        build_command="cmake --build build",
        lint_command=None,
        package_file="CMakeLists.txt",
        install_command=None,
        validate_syntax=None,
        system_prompt_rules=(
            "Follow modern C++17/20 conventions. Use RAII and smart pointers.\n"
            "Include: CMakeLists.txt, README.md, .gitignore."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Extension → language  (built once at import time)
# ---------------------------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {}
for _lang_name, _lang_cfg in LANGUAGES.items():
    for _ext in _lang_cfg.extensions:
        _EXT_TO_LANG[_ext] = _lang_name


# ---------------------------------------------------------------------------
# Marker-file detection order
# ---------------------------------------------------------------------------

_PRIORITY_MARKERS: list[tuple[str, str]] = [
    ("tsconfig.json", "typescript"),
    ("Cargo.toml",    "rust"),
    ("go.mod",        "go"),
    ("pom.xml",       "java"),
    ("build.gradle",  "java"),
    ("build.gradle.kts", "java"),
    ("CMakeLists.txt", "cpp"),
]

_PYTHON_MARKERS: tuple[str, ...] = (
    "requirements.txt", "pyproject.toml", "setup.py", "Pipfile",
)


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------

def detect_language(project_path: Path) -> str:
    """Detect project language from files in *project_path*.

    Checks for package / config files first (most reliable), then falls
    back to counting source-file extensions.  Returns ``"python"`` as
    the default when nothing else matches.
    """
    project_path = Path(project_path)
    if not project_path.exists():
        return "python"

    # Phase 1 — marker files (highest-confidence signals).
    for marker, lang in _PRIORITY_MARKERS:
        if (project_path / marker).exists():
            return lang

    # .csproj glob
    if list(project_path.glob("*.csproj")):
        return "csharp"

    # package.json without tsconfig → JavaScript
    if (project_path / "package.json").exists():
        return "javascript"

    # Python markers
    for marker in _PYTHON_MARKERS:
        if (project_path / marker).exists():
            return "python"

    # Phase 2 — count extensions (skip hidden dirs).
    ext_counts: dict[str, int] = {}
    try:
        for f in project_path.rglob("*"):
            if f.is_file() and not any(
                p.startswith(".") for p in f.relative_to(project_path).parts
            ):
                ext = f.suffix.lower()
                if ext in _EXT_TO_LANG:
                    lang = _EXT_TO_LANG[ext]
                    ext_counts[lang] = ext_counts.get(lang, 0) + 1
    except (PermissionError, OSError):
        pass

    if ext_counts:
        return max(ext_counts, key=lambda k: ext_counts[k])

    return "python"


# ---------------------------------------------------------------------------
# Description-based detection (EN / RU / UK keywords)
# ---------------------------------------------------------------------------

_DESCRIPTION_PATTERNS: list[tuple[str, list[str]]] = [
    ("typescript", [
        "typescript", " ts ", "react", "реакт", "angular",
        "next.js", "nextjs", "тайпскрипт", "фронтенд",
    ]),
    ("javascript", [
        "javascript", " js ", "node", "nodejs", "нод",
        "express", "джаваскрипт", "нода",
    ]),
    ("csharp", [
        "c#", "csharp", "шарп", "dotnet", "дотнет", ".net",
        "unity", "asp.net", "blazor", "wpf", "winforms",
    ]),
    ("java", [
        "java ", "джава", "spring", "maven", "gradle", "android",
    ]),
    ("go", [
        " go ", "golang", " го ", "горутин",
    ]),
    ("rust", [
        "rust", "раст", "cargo", "карго",
    ]),
    ("cpp", [
        "c++", "cpp", "cmake", "qt", "плюсы", "сиплюсплюс",
    ]),
    ("python", [
        "python", "питон", "пітон", "django", "flask", "fastapi",
        "pandas", "numpy", "pytorch", "tensorflow",
    ]),
]


def detect_from_description(description: str) -> str:
    """Detect language from a natural-language task description.

    Performs case-insensitive keyword matching with multi-language
    support (EN / RU / UK).  Returns ``"python"`` when nothing matches.
    """
    if not description:
        return "python"

    desc_lower = f" {description.lower()} "

    best_lang = "python"
    best_score = 0

    for lang, keywords in _DESCRIPTION_PATTERNS:
        score = sum(1 for kw in keywords if kw in desc_lower)
        if score > best_score:
            best_score = score
            best_lang = lang

    return best_lang


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_language_config(name: str) -> LanguageConfig:
    """Return the ``LanguageConfig`` for *name*, defaulting to Python."""
    return LANGUAGES.get(name, LANGUAGES["python"])


def get_language_for_extension(ext: str) -> str:
    """Return the language name for a file extension, or ``""`` if unknown."""
    return _EXT_TO_LANG.get(ext.lower(), "")
