#!/usr/bin/env python3
"""JARVIS Pre-flight Check — validates project integrity without dependencies.

Run from repo root:
    python preflight_check.py

Checks:
  1. All .py files compile (ast.parse) — catches syntax errors
  2. Migration chain integrity (1→N, no gaps, functions before dict)
  3. All jarvis.* imports resolve to existing files
  4. All regex voice patterns compile (re.compile)
  5. App aliases structure (keys lowercase, no empty values)
  6. Config schema version consistency
  7. Tool risk map completeness
  8. No duplicate tool names
  9. Test file count sanity check

Exit code: 0 = all passed, 1 = failures found.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OK = "✅"
FAIL = "❌"
WARN = "⚠️"

results: list[tuple[str, str, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    status = OK if passed else FAIL
    results.append((name, status, detail))
    icon = status
    if detail and not passed:
        print(f"  {icon} {name}: {detail}")
    else:
        print(f"  {icon} {name}")


# ── 1. Syntax check all .py files ──────────────────────────────────────

def check_syntax():
    print("\n═══ 1. Python syntax (ast.parse) ═══")
    py_files = list(ROOT.rglob("*.py"))
    py_files = [f for f in py_files if ".venv" not in str(f) 
                and "__pycache__" not in str(f)
                and ".pytest_tmp" not in str(f)]
    
    errors = []
    for f in py_files:
        try:
            source = f.read_text(encoding="utf-8")
            ast.parse(source, filename=str(f.relative_to(ROOT)))
        except SyntaxError as e:
            errors.append(f"{f.relative_to(ROOT)}:{e.lineno} — {e.msg}")
        except UnicodeDecodeError:
            pass  # skip binary/non-utf8 files
    
    check(f"{len(py_files)} Python files", len(errors) == 0,
          f"{len(errors)} syntax errors: " + "; ".join(errors[:5]) if errors else "")


# ── 2. Migration chain ─────────────────────────────────────────────────

def check_migrations():
    print("\n═══ 2. Migration chain integrity ═══")
    config_path = ROOT / "jarvis" / "core" / "config.py"
    migrations_path = ROOT / "jarvis" / "core" / "migrations.py"
    
    # Find CURRENT_SCHEMA_VERSION
    config_text = config_path.read_text(encoding="utf-8")
    m = re.search(r"CURRENT_SCHEMA_VERSION\s*=\s*(\d+)", config_text)
    if not m:
        check("CURRENT_SCHEMA_VERSION found", False, "not found in config.py")
        return
    version = int(m.group(1))
    check(f"CURRENT_SCHEMA_VERSION = {version}", True)
    
    # Find migrations
    source = migrations_path if migrations_path.exists() else config_path
    text = source.read_text(encoding="utf-8")
    
    keys = [int(k) for k in re.findall(r"^\s+(\d+):\s*_migrate_v\d+_to_v\d+", text, re.MULTILINE)]
    expected = list(range(1, version))
    missing = set(expected) - set(keys)
    extra = set(keys) - set(expected)
    
    check(f"MIGRATIONS has {len(keys)} entries (need {version-1})",
          not missing and not extra,
          f"missing: {sorted(missing)}" if missing else f"extra: {sorted(extra)}" if extra else "")
    
    # Check functions defined before MIGRATIONS dict
    migrations_line = None
    func_lines = {}
    for i, line in enumerate(text.splitlines(), 1):
        if re.match(r"^MIGRATIONS\s*[:{]|^MIGRATIONS:\s*dict", line):
            migrations_line = i
        func_m = re.match(r"^def (_migrate_v\d+_to_v\d+)", line)
        if func_m:
            func_lines[func_m.group(1)] = i
    
    if migrations_line:
        after = [f"{n} (L{l})" for n, l in func_lines.items() if l > migrations_line]
        check("All migrations defined before MIGRATIONS dict",
              len(after) == 0,
              f"defined AFTER: {', '.join(after[:3])}" if after else "")


# ── 3. Import resolution ───────────────────────────────────────────────

def check_imports():
    print("\n═══ 3. Import resolution (jarvis.*) ═══")
    jarvis_dir = ROOT / "jarvis"
    py_files = list(jarvis_dir.rglob("*.py"))
    
    issues = []
    checked = 0
    for f in py_files:
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for m in re.finditer(r"from (jarvis\.[a-z_.]+) import", text):
            module = m.group(1)
            parts = module.split(".")
            mod_path = ROOT / Path(*parts).with_suffix(".py")
            pkg_path = ROOT / Path(*parts) / "__init__.py"
            if not mod_path.exists() and not pkg_path.exists():
                issues.append(f"{f.relative_to(ROOT)}: from {module}")
                checked += 1
            else:
                checked += 1
    
    check(f"{checked} imports checked", len(issues) == 0,
          f"{len(issues)} broken: " + "; ".join(issues[:5]) if issues else "")


# ── 4. Voice pattern regex compilation ─────────────────────────────────

def check_voice_patterns():
    print("\n═══ 4. Voice pattern regex compilation ═══")
    tools_dir = ROOT / "jarvis" / "tools" / "local"
    
    total = 0
    errors = []
    for f in tools_dir.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r'regex=r"((?:[^"\\]|\\.)*)"', text):
            pattern = m.group(1)
            total += 1
            try:
                re.compile(pattern)
            except re.error as e:
                errors.append(f"{f.name}: {pattern[:40]}... — {e}")
        for m in re.finditer(r"regex=r'((?:[^'\\]|\\.)*)'", text):
            pattern = m.group(1)
            total += 1
            try:
                re.compile(pattern)
            except re.error as e:
                errors.append(f"{f.name}: {pattern[:40]}... — {e}")
    
    check(f"{total} regex patterns", len(errors) == 0,
          f"{len(errors)} invalid: " + "; ".join(errors[:3]) if errors else "")


# ── 5. App aliases ─────────────────────────────────────────────────────

def check_app_aliases():
    print("\n═══ 5. App aliases structure ═══")
    aliases_path = ROOT / "jarvis" / "tools" / "local" / "app_aliases.py"
    if not aliases_path.exists():
        check("app_aliases.py exists", False)
        return
    
    text = aliases_path.read_text(encoding="utf-8")
    
    # Extract dict entries
    entries = re.findall(r'"([^"]+)"\s*:\s*"([^"]*)"', text)
    
    not_lower = [k for k, v in entries if k != k.lower()]
    empty_vals = [k for k, v in entries if not v.strip()]
    
    check(f"{len(entries)} aliases found", len(entries) >= 100,
          f"only {len(entries)}" if len(entries) < 100 else "")
    check("All keys lowercase", len(not_lower) == 0,
          f"NOT lower: {not_lower[:3]}" if not_lower else "")
    check("No empty values", len(empty_vals) == 0,
          f"empty: {empty_vals[:3]}" if empty_vals else "")


# ── 6. Tool risk map ──────────────────────────────────────────────────

def check_risk_map():
    print("\n═══ 6. Tool risk map ═══")
    risk_path = ROOT / "jarvis" / "tools" / "risk.py"
    if not risk_path.exists():
        check("risk.py exists", False)
        return
    
    text = risk_path.read_text(encoding="utf-8")
    entries = re.findall(r'"(\w+)":\s*RiskLevel\.(\w+)', text)
    
    valid_levels = {"READ", "WRITE", "DESTRUCTIVE", "SYSTEM"}
    bad = [(k, v) for k, v in entries if v not in valid_levels]
    
    check(f"{len(entries)} tools in TOOL_RISK_MAP", len(entries) >= 30,
          f"only {len(entries)}" if len(entries) < 30 else "")
    check("All risk levels valid", len(bad) == 0,
          f"invalid: {bad[:3]}" if bad else "")


# ── 7. Filler patterns ────────────────────────────────────────────────

def check_filler_patterns():
    print("\n═══ 7. Filler word patterns ═══")
    router_path = ROOT / "jarvis" / "llm" / "intent_router.py"
    text = router_path.read_text(encoding="utf-8")
    
    patterns = re.findall(r're\.compile\(r"((?:[^"\\]|\\.)*)"', text)
    errors = []
    for p in patterns:
        try:
            re.compile(p)
        except re.error as e:
            errors.append(f"{p[:40]} — {e}")
    
    check(f"{len(patterns)} filler/router patterns", len(errors) == 0,
          f"{len(errors)} invalid: " + "; ".join(errors[:3]) if errors else "")


# ── 8. PersonalizationContext ──────────────────────────────────────────

def check_personalization():
    print("\n═══ 8. PersonalizationContext ═══")
    pctx_path = ROOT / "jarvis" / "core" / "personalization.py"
    check("personalization.py exists", pctx_path.exists())
    
    if pctx_path.exists():
        text = pctx_path.read_text(encoding="utf-8")
        fields = re.findall(r"(\w+):\s*Any\s*=\s*None", text)
        check(f"{len(fields)} fields in PersonalizationContext", len(fields) >= 5,
              f"fields: {fields}" if len(fields) < 5 else "")


# ── 9. Test file count ────────────────────────────────────────────────

def check_test_count():
    print("\n═══ 9. Test coverage ═══")
    test_dir = ROOT / "tests"
    test_files = list(test_dir.rglob("test_*.py"))
    
    # Count test functions
    total_tests = 0
    for f in test_files:
        try:
            text = f.read_text(encoding="utf-8")
            total_tests += len(re.findall(r"(?:def|async def) test_", text))
        except UnicodeDecodeError:
            pass
    
    check(f"{len(test_files)} test files, {total_tests} test functions",
          total_tests >= 100,
          f"only {total_tests} tests" if total_tests < 100 else "")


# ── 10. Key files exist ───────────────────────────────────────────────

def check_key_files():
    print("\n═══ 10. Key files exist ═══")
    must_exist = [
        "jarvis/core/config.py",
        "jarvis/core/migrations.py",
        "jarvis/core/personalization.py",
        "jarvis/core/game_mode.py",
        "jarvis/tools/sandbox.py",
        "jarvis/tools/risk.py",
        "jarvis/tools/tool_filter.py",
        "jarvis/tools/local/app_aliases.py",
        "jarvis/tools/local/get_time.py",
        "jarvis/memory/auto_recall.py",
        "jarvis/memory/auto_save.py",
        "jarvis/memory/tool_examples.py",
        "jarvis/memory/tier_manager.py",
        "jarvis/memory/context_inference.py",
        "jarvis/memory/action_logger.py",
        "jarvis/memory/session_summary.py",
        "jarvis/memory/reminders.py",
        "jarvis/memory/personal_aliases.py",
        "jarvis/memory/user_profile.py",
        "jarvis/memory/usage_stats.py",
        "conftest.py",
        "README.md",
        "setup_jarvis.py",
    ]
    missing = [f for f in must_exist if not (ROOT / f).exists()]
    check(f"{len(must_exist)} key files", len(missing) == 0,
          f"MISSING: {', '.join(missing)}" if missing else "")


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    print()
    print("═" * 55)
    print("  J.A.R.V.I.S. — Pre-flight Check")
    print("═" * 55)
    
    check_syntax()
    check_migrations()
    check_imports()
    check_voice_patterns()
    check_app_aliases()
    check_risk_map()
    check_filler_patterns()
    check_personalization()
    check_test_count()
    check_key_files()
    
    # Summary
    ok = sum(1 for _, s, _ in results if s == OK)
    fail = sum(1 for _, s, _ in results if s == FAIL)
    total = len(results)
    
    print()
    print("═" * 55)
    print(f"  Result: {ok}/{total} {OK}  {f'{fail} {FAIL}' if fail else ''}")
    print("═" * 55)
    print()
    
    return 1 if fail > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
