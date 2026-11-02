"""Opt-in dump of the LLM request (JARVIS_DUMP_LLM_REQUEST) and the replay helpers."""

from __future__ import annotations

import json

from jarvis.dev.llm_replay import LANG_HINTS, describe, with_lang_hint
from jarvis.llm.ollama_client import DUMP_ENV_VAR, DUMP_FILENAME, dump_request_if_enabled

_BODY = {
    "model": "qwen3:8b",
    "options": {"num_ctx": 16384},
    "messages": [
        {"role": "system", "content": "prompt"},
        {"role": "system", "content": "[Relevant memories:]\n- x"},
        {"role": "user", "content": "Какой у меня браузер?"},
    ],
    "tools": [{"type": "function", "function": {"name": "search_memory"}}],
}


def test_no_dump_without_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv(DUMP_ENV_VAR, raising=False)
    dump_request_if_enabled(_BODY)
    assert not (tmp_path / "debug" / DUMP_FILENAME).exists()


def test_dump_written_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(DUMP_ENV_VAR, "1")
    dump_request_if_enabled(_BODY)
    saved = json.loads((tmp_path / "debug" / DUMP_FILENAME).read_text(encoding="utf-8"))
    assert saved == _BODY


def test_lang_hint_goes_right_before_the_user_turn():
    out = with_lang_hint(_BODY)
    assert out["messages"][-2] == {"role": "system", "content": LANG_HINTS["ru"]}
    assert out["messages"][-1]["role"] == "user"
    assert len(_BODY["messages"]) == 3  # original untouched


def test_describe_lists_roles_and_tools():
    text = "\n".join(describe(_BODY))
    assert "tools: 1 schemas" in text and "search_memory" in text
    assert text.count("system") == 2
