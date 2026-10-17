"""Tests for CodeSearch — fast symbol indexing and code search.

Verifies:
  - Index build for Python (ast-based)
  - Index build for JS/TS (regex-based)
  - Index build for Go, Rust, C/C++
  - find_symbol exact match
  - find_usages grep-like search
  - find_by_description keyword search
  - read_function Python extraction (ast)
  - read_function brace-based extraction (JS/Go/Rust)
  - Skips non-source files
  - Handles missing files gracefully
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jarvis.coding.code_search import (
    CodeSearch,
    SymbolEntry,
    _extract_python_node,
    _extract_brace_block,
    _index_python,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, rel: str, content: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Python index tests
# ---------------------------------------------------------------------------

class TestPythonIndex:
    def test_classes_and_functions(self, tmp_path):
        src = textwrap.dedent("""\
            class User:
                def save(self):
                    pass

            def create_user():
                pass

            async def delete_user():
                pass
        """)
        entries = _index_python(src, "models.py")
        names = [e.name for e in entries]
        assert "User" in names
        assert "save" in names
        assert "create_user" in names
        assert "delete_user" in names

    def test_syntax_error(self):
        entries = _index_python("def broken(\n", "bad.py")
        assert entries == []

    def test_line_numbers(self):
        src = "class A:\n    pass\n\ndef b():\n    pass\n"
        entries = _index_python(src, "test.py")
        a = next(e for e in entries if e.name == "A")
        b = next(e for e in entries if e.name == "b")
        assert a.line == 1
        assert b.line == 4


# ---------------------------------------------------------------------------
# Build index + find_symbol
# ---------------------------------------------------------------------------

class TestBuildIndexAndFindSymbol:
    def test_python_symbols_found(self, tmp_path):
        _write(tmp_path, "app.py", textwrap.dedent("""\
            class App:
                pass

            def run():
                pass
        """))
        cs = CodeSearch(tmp_path)
        cs.build_index()
        results = cs.find_symbol("App")
        assert len(results) >= 1
        assert results[0][0] == "app.py"
        assert results[0][1] == 1  # line 1

    def test_js_symbols_found(self, tmp_path):
        _write(tmp_path, "router.js", textwrap.dedent("""\
            export class Router {}
            export function handleRequest() {}
        """))
        cs = CodeSearch(tmp_path)
        cs.build_index()
        results = cs.find_symbol("Router")
        assert len(results) >= 1
        assert results[0][0] == "router.js"

    def test_go_symbols_found(self, tmp_path):
        _write(tmp_path, "server.go", textwrap.dedent("""\
            type Server struct {
                port int
            }

            func NewServer() *Server {
                return &Server{}
            }
        """))
        cs = CodeSearch(tmp_path)
        cs.build_index()
        results = cs.find_symbol("Server")
        assert len(results) >= 1
        results2 = cs.find_symbol("NewServer")
        assert len(results2) >= 1

    def test_rust_symbols_found(self, tmp_path):
        _write(tmp_path, "lib.rs", textwrap.dedent("""\
            pub struct Config {
                pub name: String,
            }

            pub fn init() {
            }
        """))
        cs = CodeSearch(tmp_path)
        cs.build_index()
        assert len(cs.find_symbol("Config")) >= 1
        assert len(cs.find_symbol("init")) >= 1

    def test_symbol_not_found(self, tmp_path):
        _write(tmp_path, "empty.py", "x = 1\n")
        cs = CodeSearch(tmp_path)
        cs.build_index()
        assert cs.find_symbol("NonExistent") == []

    def test_skips_pycache(self, tmp_path):
        _write(tmp_path, "__pycache__/mod.py", "class Hidden:\n    pass\n")
        _write(tmp_path, "real.py", "class Visible:\n    pass\n")
        cs = CodeSearch(tmp_path)
        cs.build_index()
        assert cs.find_symbol("Hidden") == []
        assert len(cs.find_symbol("Visible")) >= 1

    def test_auto_builds_index_on_first_query(self, tmp_path):
        _write(tmp_path, "a.py", "class Auto:\n    pass\n")
        cs = CodeSearch(tmp_path)
        # Don't call build_index — find_symbol should do it.
        assert len(cs.find_symbol("Auto")) >= 1


# ---------------------------------------------------------------------------
# find_usages
# ---------------------------------------------------------------------------

class TestFindUsages:
    def test_finds_usages_across_files(self, tmp_path):
        _write(tmp_path, "models.py", "class User:\n    pass\n")
        _write(tmp_path, "views.py", "from models import User\nu = User()\n")
        _write(tmp_path, "tests.py", "from models import User\nassert User\n")
        cs = CodeSearch(tmp_path)
        usages = cs.find_usages("User")
        files = {u[0] for u in usages}
        assert "models.py" in files
        assert "views.py" in files
        assert "tests.py" in files

    def test_no_usages(self, tmp_path):
        _write(tmp_path, "main.py", "x = 1\n")
        cs = CodeSearch(tmp_path)
        assert cs.find_usages("Nonexistent") == []


# ---------------------------------------------------------------------------
# find_by_description
# ---------------------------------------------------------------------------

class TestFindByDescription:
    def test_keyword_match(self, tmp_path):
        _write(tmp_path, "auth.py", "def login():\n    pass\ndef verify():\n    pass\n")
        _write(tmp_path, "utils.py", "def format_date():\n    pass\n")
        cs = CodeSearch(tmp_path)
        cs.build_index()
        results = cs.find_by_description("login authentication")
        assert "auth.py" in results

    def test_no_match(self, tmp_path):
        _write(tmp_path, "main.py", "x = 1\n")
        cs = CodeSearch(tmp_path)
        cs.build_index()
        assert cs.find_by_description("quantum physics") == []


# ---------------------------------------------------------------------------
# read_function
# ---------------------------------------------------------------------------

class TestReadFunction:
    def test_python_function(self, tmp_path):
        src = textwrap.dedent("""\
            def helper():
                return 42

            def main():
                x = helper()
                print(x)

            class Unused:
                pass
        """)
        _write(tmp_path, "app.py", src)
        cs = CodeSearch(tmp_path)
        result = cs.read_function("app.py", "main")
        assert "def main()" in result
        assert "helper()" in result
        # Should NOT contain the Unused class.
        assert "Unused" not in result

    def test_python_class(self, tmp_path):
        src = textwrap.dedent("""\
            class User:
                name: str
                email: str

                def save(self):
                    pass

            def unrelated():
                pass
        """)
        _write(tmp_path, "models.py", src)
        cs = CodeSearch(tmp_path)
        result = cs.read_function("models.py", "User")
        assert "class User" in result
        assert "save" in result
        assert "unrelated" not in result

    def test_js_function(self, tmp_path):
        src = textwrap.dedent("""\
            function fetchData() {
                return fetch('/api');
            }

            function unused() {
                return null;
            }
        """)
        _write(tmp_path, "api.js", src)
        cs = CodeSearch(tmp_path)
        result = cs.read_function("api.js", "fetchData")
        assert "fetchData" in result
        assert "fetch('/api')" in result

    def test_file_not_found(self, tmp_path):
        cs = CodeSearch(tmp_path)
        result = cs.read_function("nope.py", "anything")
        assert "not found" in result.lower()

    def test_symbol_not_found_in_file(self, tmp_path):
        _write(tmp_path, "empty.py", "x = 1\n")
        cs = CodeSearch(tmp_path)
        result = cs.read_function("empty.py", "NonExistent")
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# Low-level extractors
# ---------------------------------------------------------------------------

class TestExtractors:
    def test_extract_python_node(self):
        src = "def a():\n    return 1\n\ndef b():\n    return 2\n"
        result = _extract_python_node(src, "a")
        assert result is not None
        assert "return 1" in result
        assert "return 2" not in result

    def test_extract_python_node_not_found(self):
        assert _extract_python_node("x = 1\n", "missing") is None

    def test_extract_python_node_syntax_error(self):
        assert _extract_python_node("def broken(\n", "broken") is None

    def test_extract_brace_block(self):
        src = "function hello() {\n  console.log('hi');\n}\n\nfunction bye() {\n  return;\n}\n"
        result = _extract_brace_block(src, "hello")
        assert result is not None
        assert "console.log" in result
        assert "bye" not in result

    def test_extract_brace_block_not_found(self):
        assert _extract_brace_block("x = 1", "nope") is None
