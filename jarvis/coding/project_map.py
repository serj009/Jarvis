"""Project structure mapper — builds a lightweight overview of a codebase.

Instead of loading every file into the LLM context (impossible for large
projects), we generate a compact "map" that shows:
- Directory tree
- Per-file: size, language, classes/functions/exports

The map typically fits in 500-1000 tokens even for 50,000+ line projects,
giving the coding model enough context to know WHERE to look.
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

# Directories to always skip when scanning a project.
SKIP_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".env", "target", "bin", "obj", "dist", "build", ".tox", ".mypy_cache",
    ".pytest_cache", ".idea", ".vscode", ".eggs", "*.egg-info",
    ".next", ".nuxt", "coverage", ".cache", ".gradle", ".mvn",
})

# File extensions we consider "source code" (everything else is listed but
# not parsed for symbols).
_LANG_MAP: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".hpp": "C++",
    ".cc": "C++",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".lua": "Lua",
    ".sh": "Shell",
    ".bash": "Shell",
}

# Maximum items before a directory listing is collapsed.
_COLLAPSE_THRESHOLD: int = 8


# ---------------------------------------------------------------------------
# Data holders
# ---------------------------------------------------------------------------

@dataclass
class FileSummary:
    """Lightweight summary of one source file."""

    rel_path: str
    lines: int = 0
    language: str = ""
    symbols: list[str] = field(default_factory=list)  # "class Foo", "def bar", …


@dataclass
class DirSummary:
    """Summary of a directory (used for collapsing)."""

    rel_path: str
    file_count: int = 0
    total_lines: int = 0


# ---------------------------------------------------------------------------
# Symbol extractors
# ---------------------------------------------------------------------------

def _extract_python_symbols(source: str) -> list[str]:
    """Use ``ast`` to extract top-level class and function names."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    symbols: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(f"class {node.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(f"{node.name}()")
    return symbols


# Regex-based extractors for non-Python languages.

_JS_TS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.MULTILINE),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE),
    re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(",
        re.MULTILINE,
    ),
]

_JAVA_CS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^\s*(?:public|private|protected|internal|static|abstract|sealed|partial)?\s*"
        r"(?:class|interface|enum|record|struct)\s+(\w+)",
        re.MULTILINE,
    ),
    re.compile(
        r"^\s*(?:public|private|protected|internal|static|abstract|virtual|override|async)?\s*"
        r"(?:public|private|protected|internal|static|abstract|virtual|override|async)?\s*"
        r"\w[\w<>\[\],\s]*?\s+(\w+)\s*\(",
        re.MULTILINE,
    ),
]

_GO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*type\s+(\w+)\s+struct", re.MULTILINE),
    re.compile(r"^\s*type\s+(\w+)\s+interface", re.MULTILINE),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(", re.MULTILINE),
]

_RUST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*(?:pub\s+)?struct\s+(\w+)", re.MULTILINE),
    re.compile(r"^\s*(?:pub\s+)?enum\s+(\w+)", re.MULTILINE),
    re.compile(r"^\s*(?:pub\s+)?trait\s+(\w+)", re.MULTILINE),
    re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", re.MULTILINE),
]

_C_CPP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*class\s+(\w+)", re.MULTILINE),
    re.compile(r"^\s*struct\s+(\w+)", re.MULTILINE),
    re.compile(
        r"^\s*(?:static\s+|inline\s+|extern\s+|virtual\s+)*"
        r"\w[\w:*&<>\s]*?\s+(\w+)\s*\([^;]*\)\s*\{",
        re.MULTILINE,
    ),
]


def _extract_symbols_regex(
    source: str,
    patterns: list[re.Pattern[str]],
) -> list[str]:
    """Generic regex-based symbol extraction."""
    seen: set[str] = set()
    symbols: list[str] = []
    for pat in patterns:
        for m in pat.finditer(source):
            name = m.group(1)
            if name and name not in seen:
                seen.add(name)
                symbols.append(name)
    return symbols


def _extract_symbols(source: str, language: str) -> list[str]:
    """Dispatch to the right extractor based on *language*."""
    if language == "Python":
        return _extract_python_symbols(source)
    if language in ("JavaScript", "TypeScript"):
        return _extract_symbols_regex(source, _JS_TS_PATTERNS)
    if language in ("Java", "C#", "Kotlin", "Scala"):
        return _extract_symbols_regex(source, _JAVA_CS_PATTERNS)
    if language == "Go":
        return _extract_symbols_regex(source, _GO_PATTERNS)
    if language == "Rust":
        return _extract_symbols_regex(source, _RUST_PATTERNS)
    if language in ("C", "C++"):
        return _extract_symbols_regex(source, _C_CPP_PATTERNS)
    return []


# ---------------------------------------------------------------------------
# Requirements / manifest extractors
# ---------------------------------------------------------------------------

def _summarise_requirements(content: str) -> str:
    """Return a short "flask, sqlalchemy, ..." summary for requirements.txt."""
    names: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip()
        if name:
            names.append(name)
    if not names:
        return ""
    if len(names) <= 5:
        return ", ".join(names)
    return ", ".join(names[:5]) + f", … ({len(names)} total)"


# ---------------------------------------------------------------------------
# ProjectMap
# ---------------------------------------------------------------------------

class ProjectMap:
    """Generate a compact text map of a project directory.

    Usage::

        pm = ProjectMap(Path("/path/to/project"))
        text = pm.generate()
        summary = pm.get_file_summary("src/main.py")
        relevant = pm.find_relevant_files("authentication logic")
    """

    def __init__(self, root: Path) -> None:
        self.root: Path = Path(root).resolve()
        self._files: list[FileSummary] = []
        self._scanned: bool = False

    # -- scanning ----------------------------------------------------------

    def _should_skip_dir(self, name: str) -> bool:
        """Return *True* if directory *name* should be excluded."""
        if name in SKIP_DIRS:
            return True
        # Also skip dirs matching wildcard patterns like *.egg-info.
        for pat in SKIP_DIRS:
            if "*" in pat and name.endswith(pat.lstrip("*")):
                return True
        return False

    def _scan(self) -> None:
        """Walk the project tree and populate ``self._files``."""
        if self._scanned:
            return
        self._files.clear()

        for dirpath, dirnames, filenames in os.walk(self.root):
            # Prune skipped directories in-place.
            dirnames[:] = [
                d for d in dirnames if not self._should_skip_dir(d)
            ]

            for fname in filenames:
                full = Path(dirpath) / fname
                rel = str(full.relative_to(self.root)).replace("\\", "/")
                ext = full.suffix.lower()
                language = _LANG_MAP.get(ext, "")

                try:
                    content = full.read_text(encoding="utf-8", errors="replace")
                except (OSError, UnicodeDecodeError):
                    content = ""

                lines = (
                    content.count("\n") + (1 if content and not content.endswith("\n") else 0)
                    if content
                    else 0
                )
                symbols = _extract_symbols(content, language) if language else []

                self._files.append(
                    FileSummary(
                        rel_path=rel,
                        lines=lines,
                        language=language,
                        symbols=symbols,
                    )
                )

        self._scanned = True

    # -- public API --------------------------------------------------------

    def generate(self) -> str:
        """Generate a compact text map of the project.

        The output follows the format::

            myproject/
            ├── app/
            │   ├── __init__.py (15 lines) — create_app()
            │   ├── routes.py (120 lines) — login(), register(), …
            ...
            Language: Python | Total: 12 files, 1,250 lines
        """
        self._scan()

        if not self._files:
            return "./ (empty)\n"

        # Build a nested dict tree: path parts → FileSummary / sub-dicts
        tree: dict[str, Any] = {}
        for fs in self._files:
            parts = fs.rel_path.split("/")
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = fs

        lines: list[str] = ["./"]
        self._render_tree(tree, lines, prefix="")

        # Footer with totals.
        total_files = len(self._files)
        total_lines = sum(f.lines for f in self._files)
        languages = {f.language for f in self._files if f.language}
        lang_str = ", ".join(sorted(languages)) if languages else "mixed"
        lines.append("")
        lines.append(
            f"Language: {lang_str} | Total: {total_files} files, "
            f"{total_lines:,} lines"
        )
        return "\n".join(lines)

    def _render_tree(
        self,
        node: dict[str, Any],
        lines: list[str],
        prefix: str,
    ) -> None:
        """Recursively render the tree into *lines*."""
        entries = sorted(
            node.items(),
            key=lambda kv: (not isinstance(kv[1], dict), kv[0].lower()),
        )
        for i, (name, value) in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")

            if isinstance(value, FileSummary):
                desc = self._format_file_entry(value)
                lines.append(f"{prefix}{connector}{desc}")
            elif isinstance(value, dict):
                # Directory — check if it should be collapsed.
                flat_files = self._collect_files(value)
                if self._should_collapse(value, flat_files):
                    total = len(flat_files)
                    total_lines = sum(f.lines for f in flat_files)
                    lines.append(
                        f"{prefix}{connector}{name}/ "
                        f"({total} files, {total_lines:,} lines total)"
                    )
                else:
                    lines.append(f"{prefix}{connector}{name}/")
                    self._render_tree(value, lines, child_prefix)

    @staticmethod
    def _format_file_entry(fs: FileSummary) -> str:
        """Format a single file for the tree output."""
        name = fs.rel_path.rsplit("/", 1)[-1]
        parts = [name]
        if fs.lines:
            parts.append(f"({fs.lines} lines)")
        if fs.symbols:
            sym_str = ", ".join(fs.symbols[:6])
            if len(fs.symbols) > 6:
                sym_str += ", …"
            parts.append(f"— {sym_str}")
        return " ".join(parts)

    @staticmethod
    def _collect_files(subtree: dict[str, Any]) -> list[FileSummary]:
        """Recursively collect all FileSummary objects from a subtree."""
        result: list[FileSummary] = []
        for value in subtree.values():
            if isinstance(value, FileSummary):
                result.append(value)
            elif isinstance(value, dict):
                result.extend(ProjectMap._collect_files(value))
        return result

    @staticmethod
    def _should_collapse(subtree: dict[str, Any], flat_files: list[FileSummary]) -> bool:
        """Decide whether a sub-directory should be shown collapsed."""
        if len(flat_files) > _COLLAPSE_THRESHOLD:
            return True
        return False

    def get_file_summary(self, path: str) -> str:
        """Get a detailed summary of one file (imports, classes, functions).

        *path* is relative to the project root (forward-slash separated).
        """
        self._scan()
        normalised = path.replace("\\", "/")
        for fs in self._files:
            if fs.rel_path == normalised:
                parts = [f"File: {fs.rel_path}"]
                if fs.language:
                    parts.append(f"Language: {fs.language}")
                parts.append(f"Lines: {fs.lines}")
                if fs.symbols:
                    parts.append("Symbols: " + ", ".join(fs.symbols))
                return "\n".join(parts)
        return f"File not found in project: {path}"

    def find_relevant_files(self, query: str) -> list[str]:
        """Find files whose path or symbols match keywords in *query*.

        Returns a list of relative file paths, best matches first.
        """
        self._scan()
        keywords = set(query.lower().split())
        scored: list[tuple[float, str]] = []
        for fs in self._files:
            score = 0.0
            haystack = (
                fs.rel_path.lower()
                + " "
                + " ".join(s.lower() for s in fs.symbols)
            )
            for kw in keywords:
                if kw in haystack:
                    score += 1.0
            if score > 0:
                scored.append((score, fs.rel_path))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [path for _, path in scored]
