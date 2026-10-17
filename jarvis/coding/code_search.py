"""Fast code search — find functions, classes, and patterns without loading everything.

Instead of loading the entire codebase into context, the agent can:
1. Build a symbol index once (class names, function names, variables).
2. Look up any symbol by name → get (file, line, context).
3. Find usages of a symbol across the codebase (grep-like).
4. Read just one function/class from a file without the rest.

This keeps per-step context small no matter how large the project is.
"""

from __future__ import annotations

import ast
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".env", "target", "bin", "obj", "dist", "build", ".tox", ".mypy_cache",
    ".pytest_cache", ".idea", ".vscode", ".eggs",
    ".next", ".nuxt", "coverage", ".cache", ".gradle", ".mvn",
})

_SOURCE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".java", ".cs", ".go", ".rs",
    ".c", ".h", ".cpp", ".hpp", ".cc",
    ".rb", ".php", ".swift", ".kt", ".scala", ".lua",
    ".sh", ".bash",
})


# ---------------------------------------------------------------------------
# SymbolEntry
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SymbolEntry:
    """One indexed symbol (function, class, variable)."""

    name: str
    file: str      # relative path (forward-slash)
    line: int      # 1-based line number
    kind: str      # "class", "function", "variable", "method", …
    context: str   # the source line stripped


# ---------------------------------------------------------------------------
# Python AST indexer
# ---------------------------------------------------------------------------

def _index_python(source: str, rel_path: str) -> list[SymbolEntry]:
    """Extract symbols from Python source using ``ast``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    source_lines = source.splitlines()
    entries: list[SymbolEntry] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            ctx = source_lines[node.lineno - 1].strip() if node.lineno <= len(source_lines) else ""
            entries.append(SymbolEntry(
                name=node.name, file=rel_path,
                line=node.lineno, kind="class", context=ctx,
            ))
            # Also index methods.
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    ctx2 = source_lines[child.lineno - 1].strip() if child.lineno <= len(source_lines) else ""
                    entries.append(SymbolEntry(
                        name=child.name, file=rel_path,
                        line=child.lineno, kind="method", context=ctx2,
                    ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Only top-level functions (methods handled above).
            if not any(
                isinstance(p, ast.ClassDef)
                for p in ast.walk(tree)
                if hasattr(p, "body") and node in getattr(p, "body", [])
            ):
                ctx = source_lines[node.lineno - 1].strip() if node.lineno <= len(source_lines) else ""
                entries.append(SymbolEntry(
                    name=node.name, file=rel_path,
                    line=node.lineno, kind="function", context=ctx,
                ))
    return entries


# ---------------------------------------------------------------------------
# Regex-based indexer (JS/TS, Java, C#, Go, Rust, C/C++)
# ---------------------------------------------------------------------------

_REGEX_PATTERNS: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "js_ts": [
        (re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.MULTILINE), "class"),
        (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE), "function"),
        (re.compile(
            r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(",
            re.MULTILINE,
        ), "function"),
    ],
    "java_cs": [
        (re.compile(
            r"^\s*(?:public|private|protected|internal|static|abstract|sealed|partial)?\s*"
            r"(?:class|interface|enum|record|struct)\s+(\w+)",
            re.MULTILINE,
        ), "class"),
        (re.compile(
            r"^\s*(?:public|private|protected|internal|static|abstract|virtual|override|async)?\s*"
            r"(?:public|private|protected|internal|static|abstract|virtual|override|async)?\s*"
            r"\w[\w<>\[\],\s]*?\s+(\w+)\s*\(",
            re.MULTILINE,
        ), "function"),
    ],
    "go": [
        (re.compile(r"^\s*type\s+(\w+)\s+struct", re.MULTILINE), "class"),
        (re.compile(r"^\s*type\s+(\w+)\s+interface", re.MULTILINE), "class"),
        (re.compile(r"^\s*func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(", re.MULTILINE), "function"),
    ],
    "rust": [
        (re.compile(r"^\s*(?:pub\s+)?struct\s+(\w+)", re.MULTILINE), "class"),
        (re.compile(r"^\s*(?:pub\s+)?enum\s+(\w+)", re.MULTILINE), "class"),
        (re.compile(r"^\s*(?:pub\s+)?trait\s+(\w+)", re.MULTILINE), "class"),
        (re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", re.MULTILINE), "function"),
    ],
    "c_cpp": [
        (re.compile(r"^\s*class\s+(\w+)", re.MULTILINE), "class"),
        (re.compile(r"^\s*struct\s+(\w+)", re.MULTILINE), "class"),
        (re.compile(
            r"^\s*(?:static\s+|inline\s+|extern\s+|virtual\s+)*"
            r"\w[\w:*&<>\s]*?\s+(\w+)\s*\([^;]*\)\s*\{",
            re.MULTILINE,
        ), "function"),
    ],
}

_EXT_TO_LANG_KEY: dict[str, str] = {
    ".py": "python",
    ".js": "js_ts", ".jsx": "js_ts", ".ts": "js_ts", ".tsx": "js_ts",
    ".java": "java_cs", ".cs": "java_cs", ".kt": "java_cs", ".scala": "java_cs",
    ".go": "go",
    ".rs": "rust",
    ".c": "c_cpp", ".h": "c_cpp", ".cpp": "c_cpp", ".hpp": "c_cpp", ".cc": "c_cpp",
}


def _index_regex(
    source: str,
    rel_path: str,
    patterns: list[tuple[re.Pattern[str], str]],
) -> list[SymbolEntry]:
    """Index symbols via regex for non-Python languages."""
    source_lines = source.splitlines()
    entries: list[SymbolEntry] = []
    seen: set[tuple[str, int]] = set()
    for pat, kind in patterns:
        for m in pat.finditer(source):
            name = m.group(1)
            lineno = source[:m.start()].count("\n") + 1
            key = (name, lineno)
            if key in seen:
                continue
            seen.add(key)
            ctx = source_lines[lineno - 1].strip() if lineno <= len(source_lines) else ""
            entries.append(SymbolEntry(
                name=name, file=rel_path, line=lineno, kind=kind, context=ctx,
            ))
    return entries


# ---------------------------------------------------------------------------
# Python function/class extractor
# ---------------------------------------------------------------------------

def _extract_python_node(source: str, name: str) -> str | None:
    """Extract the source of a function or class *name* from Python *source*.

    Returns the full source text of the node, or ``None`` if not found.
    Uses ``ast`` for precise extraction.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    source_lines = source.splitlines(keepends=True)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name != name:
                continue
            start = node.lineno - 1  # 0-based
            end = node.end_lineno if hasattr(node, "end_lineno") and node.end_lineno else None
            if end is not None:
                return "".join(source_lines[start:end])
            # Fallback: go until next top-level node or end.
            return "".join(source_lines[start:])
    return None


def _extract_brace_block(source: str, name: str) -> str | None:
    """Extract a brace-delimited block for *name* from *source*.

    Handles functions/classes in JS, TS, Java, C#, Go, Rust, C/C++.
    """
    # Find the line where the name is declared.
    pat = re.compile(rf"\b{re.escape(name)}\b")
    match = pat.search(source)
    if not match:
        return None

    # Walk backward to find the start of the declaration (look for common
    # leading keywords or a blank line).
    lines = source.splitlines(keepends=True)
    decl_line = source[:match.start()].count("\n")

    # Find the opening brace on or after decl_line.
    brace_pos = source.find("{", match.start())
    if brace_pos == -1:
        # No opening brace — return the single line.
        return lines[decl_line] if decl_line < len(lines) else None

    # Count braces.
    depth = 0
    idx = brace_pos
    while idx < len(source):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end_line = source[:idx + 1].count("\n")
                return "".join(lines[decl_line : end_line + 1])
        idx += 1

    # Unclosed brace — return from decl_line to end.
    return "".join(lines[decl_line:])


# ---------------------------------------------------------------------------
# CodeSearch
# ---------------------------------------------------------------------------

class CodeSearch:
    """Fast code search — find functions, classes, and patterns without
    loading the full project into LLM context.

    Usage::

        cs = CodeSearch(Path("/project"))
        cs.build_index()
        hits = cs.find_symbol("UserModel")
        usages = cs.find_usages("UserModel")
        code = cs.read_function("models.py", "UserModel")
    """

    def __init__(self, root: Path) -> None:
        self.root: Path = Path(root).resolve()
        self._index: list[SymbolEntry] = []
        self._indexed: bool = False

    # -- index build -------------------------------------------------------

    def _should_skip_dir(self, name: str) -> bool:
        if name in SKIP_DIRS:
            return True
        for pat in SKIP_DIRS:
            if "*" in pat and name.endswith(pat.lstrip("*")):
                return True
        return False

    def build_index(self) -> None:
        """Scan all source files and index symbols."""
        self._index.clear()

        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not self._should_skip_dir(d)]

            for fname in filenames:
                full = Path(dirpath) / fname
                ext = full.suffix.lower()
                if ext not in _SOURCE_EXTENSIONS:
                    continue

                rel = str(full.relative_to(self.root)).replace("\\", "/")
                try:
                    content = full.read_text(encoding="utf-8", errors="replace")
                except (OSError, UnicodeDecodeError):
                    continue

                lang_key = _EXT_TO_LANG_KEY.get(ext)
                if lang_key == "python":
                    self._index.extend(_index_python(content, rel))
                elif lang_key and lang_key in _REGEX_PATTERNS:
                    self._index.extend(
                        _index_regex(content, rel, _REGEX_PATTERNS[lang_key])
                    )

        self._indexed = True

    # -- queries -----------------------------------------------------------

    def find_symbol(self, name: str) -> list[tuple[str, int, str]]:
        """Find a function/class/variable by name.

        Returns ``[(file, line, context), ...]``.
        """
        if not self._indexed:
            self.build_index()

        results: list[tuple[str, int, str]] = []
        for entry in self._index:
            if entry.name == name:
                results.append((entry.file, entry.line, entry.context))
        return results

    def find_usages(self, name: str) -> list[tuple[str, int, str]]:
        """Find where a symbol is used (grep-like across all source files).

        Returns ``[(file, line, context_line), ...]``.
        """
        results: list[tuple[str, int, str]] = []
        pattern = re.compile(rf"\b{re.escape(name)}\b")

        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not self._should_skip_dir(d)]

            for fname in filenames:
                full = Path(dirpath) / fname
                ext = full.suffix.lower()
                if ext not in _SOURCE_EXTENSIONS:
                    continue

                rel = str(full.relative_to(self.root)).replace("\\", "/")
                try:
                    content = full.read_text(encoding="utf-8", errors="replace")
                except (OSError, UnicodeDecodeError):
                    continue

                for lineno, line in enumerate(content.splitlines(), 1):
                    if pattern.search(line):
                        results.append((rel, lineno, line.strip()))
        return results

    def find_by_description(self, description: str) -> list[str]:
        """Find files relevant to a natural-language description.

        Simple keyword matching against file paths and symbol names.
        Returns a sorted list of relative file paths.
        """
        if not self._indexed:
            self.build_index()

        keywords = set(description.lower().split())
        scores: dict[str, float] = {}

        for entry in self._index:
            haystack = (entry.file + " " + entry.name + " " + entry.kind).lower()
            score = sum(1.0 for kw in keywords if kw in haystack)
            if score > 0:
                scores[entry.file] = scores.get(entry.file, 0) + score

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [path for path, _ in ranked]

    def read_function(self, file: str, function_name: str) -> str:
        """Extract just one function/class from a file.

        *file* is relative to the project root.  Returns the extracted
        source or an error message if not found.
        """
        normalised = file.replace("\\", "/")
        full = self.root / normalised
        if not full.exists():
            return f"File not found: {file}"

        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError) as exc:
            return f"Cannot read {file}: {exc}"

        ext = full.suffix.lower()
        lang_key = _EXT_TO_LANG_KEY.get(ext)

        if lang_key == "python":
            result = _extract_python_node(content, function_name)
        else:
            result = _extract_brace_block(content, function_name)

        if result is None:
            return f"Symbol '{function_name}' not found in {file}"

        return result
