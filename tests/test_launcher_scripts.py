"""Invariants of the Windows launcher scripts (start-jarvis.ps1, register-task.ps1).

The scripts are PowerShell and only run at logon, so the parts that broke
live are pinned here as text checks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _script(name: str) -> str:
    return (_ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["start-jarvis.ps1", "register-task.ps1"])
def test_project_dir_defaults_to_the_scripts_own_folder(name: str):
    # The autostart task kept running an old copy at C:\Users\serj\Jarvis
    # whose code refused the newer config ("schema_version 41 is newer than
    # supported 22") on every logon.
    m = re.search(r"\[string\]\s*\$ProjectDir\s*=\s*([^,\r\n]+)", _script(name))
    assert m is not None
    assert m.group(1).strip() == "$PSScriptRoot"


def test_does_not_race_the_ollama_tray_app():
    # At logon the Ollama app starts ~40 s after the task; a second
    # "ollama serve" left it retrying every second (1431 times on 15.11).
    script = _script("start-jarvis.ps1")
    assert "Startup\\Ollama.lnk" in script
    assert "Test-OllamaAppStarts" in script
