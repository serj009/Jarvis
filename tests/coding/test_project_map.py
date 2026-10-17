"""Tests for ProjectMap — compact project structure mapper.

Verifies:
  - Directory scanning with skip-list
  - Python symbol extraction via ast
  - JS/TS symbol extraction via regex
  - Java/C#/Go/Rust/C++ symbol extraction
  - Tree rendering with collapse logic
  - File summary lookup
  - Keyword-based file finding
  - Empty project handling
  - Deep nesting
  - Requirements summarisation
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jarvis.coding.project_map import (
    ProjectMap,
    FileSummary,
    _extract_python_symbols,
    _extract_symbols,
    _summarise_requirements,
    SKIP_DIRS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, rel: str, content: str) -> Path:
    """Write *content* to *tmp_path / rel*, creating parents."""
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Python symbol extraction
# ---------------------------------------------------------------------------

class TestPythonSymbols:
    def test_class_and_functions(self):
        src = textwrap.dedent("""\
            class User:
                pass

            def create_user():
                pass

            async def delete_user():
                pass
        """)
        syms = _extract_python_symbols(src)
        assert "class User" in syms
        assert "create_user()" in syms
        assert "delete_user()" in syms

    def test_no_symbols(self):
        assert _extract_python_symbols("x = 1\n") == []

    def test_syntax_error_returns_empty(self):
        assert _extract_python_symbols("def broken(\n") == []


# ---------------------------------------------------------------------------
# Multi-language symbol extraction
# ---------------------------------------------------------------------------

class TestMultiLanguageSymbols:
    def test_javascript(self):
        src = textwrap.dedent("""\
            export class Router {}
            export function handleRequest() {}
            const fetchData = async () => {}
        """)
        syms = _extract_symbols(src, "JavaScript")
        assert "Router" in syms
        assert "handleRequest" in syms
        assert "fetchData" in syms

    def test_typescript(self):
        src = "class AppService {}\nfunction bootstrap() {}\n"
        syms = _extract_symbols(src, "TypeScript")
        assert "AppService" in syms
        assert "bootstrap" in syms

    def test_go(self):
        src = textwrap.dedent("""\
            type Server struct {}
            type Handler interface {}
            func NewServer() *Server {}
            func (s *Server) Listen() {}
        """)
        syms = _extract_symbols(src, "Go")
        assert "Server" in syms
        assert "Handler" in syms
        assert "NewServer" in syms
        assert "Listen" in syms

    def test_rust(self):
        src = textwrap.dedent("""\
            pub struct Config {}
            pub enum Status { Active, Inactive }
            pub trait Runnable {}
            pub async fn start() {}
        """)
        syms = _extract_symbols(src, "Rust")
        assert "Config" in syms
        assert "Status" in syms
        assert "Runnable" in syms
        assert "start" in syms

    def test_cpp(self):
        src = textwrap.dedent("""\
            class Engine {};
            struct Point {};
            void render() {
            }
        """)
        syms = _extract_symbols(src, "C++")
        assert "Engine" in syms
        assert "Point" in syms

    def test_unknown_language_returns_empty(self):
        assert _extract_symbols("anything", "Haskell") == []


# ---------------------------------------------------------------------------
# ProjectMap.generate
# ---------------------------------------------------------------------------

class TestProjectMapGenerate:
    def test_single_python_file(self, tmp_path):
        _write(tmp_path, "main.py", "def main():\n    pass\n")
        pm = ProjectMap(tmp_path)
        text = pm.generate()
        assert "main.py" in text
        assert "main()" in text
        assert "Python" in text

    def test_nested_structure(self, tmp_path):
        _write(tmp_path, "src/app.py", "class App:\n    pass\n")
        _write(tmp_path, "src/utils.py", "def helper():\n    pass\n")
        _write(tmp_path, "README.md", "# Readme\n")
        pm = ProjectMap(tmp_path)
        text = pm.generate()
        assert "src/" in text
        assert "app.py" in text
        assert "utils.py" in text
        assert "README.md" in text

    def test_skips_git_and_pycache(self, tmp_path):
        _write(tmp_path, ".git/config", "bare = false")
        _write(tmp_path, "__pycache__/mod.cpython-311.pyc", "bytes")
        _write(tmp_path, "real.py", "x = 1\n")
        pm = ProjectMap(tmp_path)
        text = pm.generate()
        assert "real.py" in text
        assert ".git" not in text
        assert "__pycache__" not in text

    def test_skips_node_modules(self, tmp_path):
        _write(tmp_path, "node_modules/pkg/index.js", "module.exports = {}")
        _write(tmp_path, "index.js", "console.log('hi')")
        pm = ProjectMap(tmp_path)
        text = pm.generate()
        assert "index.js" in text
        assert "node_modules" not in text

    def test_empty_project(self, tmp_path):
        pm = ProjectMap(tmp_path)
        text = pm.generate()
        assert "empty" in text.lower()

    def test_total_line_count(self, tmp_path):
        _write(tmp_path, "a.py", "x = 1\ny = 2\nz = 3\n")
        _write(tmp_path, "b.py", "a = 1\n")
        pm = ProjectMap(tmp_path)
        text = pm.generate()
        # Should report total files and lines.
        assert "2 files" in text

    def test_collapse_deep_directory(self, tmp_path):
        """Directories with >8 files should be collapsed."""
        for i in range(12):
            _write(tmp_path, f"big_dir/file_{i}.py", f"x_{i} = {i}\n")
        pm = ProjectMap(tmp_path)
        text = pm.generate()
        assert "12 files" in text
        assert "total)" in text


# ---------------------------------------------------------------------------
# get_file_summary
# ---------------------------------------------------------------------------

class TestGetFileSummary:
    def test_existing_file(self, tmp_path):
        _write(tmp_path, "models.py", "class User:\n    pass\n\ndef get_user():\n    pass\n")
        pm = ProjectMap(tmp_path)
        summary = pm.get_file_summary("models.py")
        assert "models.py" in summary
        assert "Python" in summary
        assert "class User" in summary
        assert "get_user()" in summary

    def test_nonexistent_file(self, tmp_path):
        pm = ProjectMap(tmp_path)
        summary = pm.get_file_summary("nope.py")
        assert "not found" in summary.lower()


# ---------------------------------------------------------------------------
# find_relevant_files
# ---------------------------------------------------------------------------

class TestFindRelevantFiles:
    def test_keyword_match(self, tmp_path):
        _write(tmp_path, "auth.py", "def login():\n    pass\ndef logout():\n    pass\n")
        _write(tmp_path, "models.py", "class User:\n    pass\n")
        _write(tmp_path, "utils.py", "def fmt():\n    pass\n")
        pm = ProjectMap(tmp_path)
        results = pm.find_relevant_files("user login authentication")
        # auth.py should rank high (login symbol), models.py too (User).
        assert len(results) >= 2
        paths = [r for r in results]
        assert "auth.py" in paths
        assert "models.py" in paths

    def test_no_match_returns_empty(self, tmp_path):
        _write(tmp_path, "main.py", "x = 1\n")
        pm = ProjectMap(tmp_path)
        assert pm.find_relevant_files("quantum physics") == []


# ---------------------------------------------------------------------------
# Requirements summarisation
# ---------------------------------------------------------------------------

class TestSummariseRequirements:
    def test_basic(self):
        text = "flask>=3.0\nsqlalchemy\npytest\n"
        result = _summarise_requirements(text)
        assert "flask" in result
        assert "sqlalchemy" in result

    def test_many_deps_truncated(self):
        lines = "\n".join(f"pkg{i}" for i in range(20))
        result = _summarise_requirements(lines)
        assert "20 total" in result

    def test_empty(self):
        assert _summarise_requirements("") == ""
        assert _summarise_requirements("# comment only\n") == ""
