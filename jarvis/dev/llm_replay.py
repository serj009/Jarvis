"""Replay the last chat request Jarvis sent to Ollama (dev tool).

    $env:JARVIS_DUMP_LLM_REQUEST = "1"; python -m jarvis   # ask ONE question, quit
    python -m jarvis.dev.llm_replay
    python -m jarvis.dev.llm_replay --delete                # remove the dump

First prints what the request is made of (messages per role, tool schemas,
sizes). Then sends it again, non-streaming, in three variants:

  as-is       exactly what Jarvis sent (tools included)
  no-tools    same messages without the tool schemas
  lang-hint   as-is + one system line "reply in <language of the question>"

and prints for each: the answer, any tool call, prompt size and timings.
Options are sent unchanged (same num_ctx), so Ollama does not reload.

NOTE: the dump holds the whole prompt in plaintext, recalled personal
memories included. Delete it when done (--delete).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys

from jarvis.core.phrases import detect_language
from jarvis.llm.ollama_client import DEFAULT_ENDPOINT, DUMP_FILENAME

LANG_HINTS: dict[str, str] = {
    "ru": "Пользователь говорит по-русски. Отвечай только на русском языке.",
    "uk": "Користувач говорить українською. Відповідай лише українською мовою.",
    "en": "The user is speaking English. Reply only in English.",
}


def _dump_path():
    from jarvis.paths import data_path

    return data_path("debug", DUMP_FILENAME)


def _last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""


def with_lang_hint(body: dict) -> dict:
    """Copy of *body* with a reply-language system line before the last user turn."""
    out = copy.deepcopy(body)
    messages = out.get("messages", [])
    lang = detect_language(_last_user_text(messages)) or "en"
    idx = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=None)
    hint = {"role": "system", "content": LANG_HINTS[lang]}
    if idx is None:
        messages.append(hint)
    else:
        messages.insert(idx, hint)
    return out


def describe(body: dict) -> list[str]:
    lines = [f"model: {body.get('model')}  options: {body.get('options')}"]
    for i, msg in enumerate(body.get("messages", [])):
        content = str(msg.get("content") or "")
        extra = f" tool_calls={len(msg['tool_calls'])}" if msg.get("tool_calls") else ""
        lines.append(f"  [{i}] {msg.get('role'):<9} {len(content):>6} chars{extra}")
    tools = body.get("tools") or []
    size = len(json.dumps(tools, ensure_ascii=False))
    names = ", ".join(t.get("function", {}).get("name", "?") for t in tools)
    lines.append(f"  tools: {len(tools)} schemas, {size} chars: {names}")
    return lines


def _send(endpoint: str, body: dict) -> dict:
    import httpx

    req = dict(body, stream=False)
    resp = httpx.post(f"{endpoint.rstrip('/')}/api/chat", json=req, timeout=300)
    resp.raise_for_status()
    return resp.json()


def _report(name: str, data: dict) -> list[str]:
    msg = data.get("message") or {}
    sec = 1e9
    lines = [
        f"[{name}] prompt {data.get('prompt_eval_count', '?')} tok in "
        f"{data.get('prompt_eval_duration', 0) / sec:.1f}s, answer "
        f"{data.get('eval_count', '?')} tok in {data.get('eval_duration', 0) / sec:.1f}s",
    ]
    for call in msg.get("tool_calls") or []:
        fn = call.get("function", {})
        call_args = json.dumps(fn.get("arguments"), ensure_ascii=False)
        lines.append(f"    tool call: {fn.get('name')}({call_args})")
    text = " ".join(str(msg.get("content") or "").split())
    lines.append(f"    answer: {text[:400] or '(empty)'}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay Jarvis's last LLM request")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--delete", action="store_true", help="delete the dump file and exit")
    args = parser.parse_args(argv)

    path = _dump_path()
    if args.delete:
        path.unlink(missing_ok=True)
        print(f"deleted: {path}")
        return 0
    if not path.exists():
        print(f"no dump at {path}")
        print('run Jarvis with $env:JARVIS_DUMP_LLM_REQUEST = "1" and ask one question first')
        return 1

    body = json.loads(path.read_text(encoding="utf-8"))
    print(f"dump: {path}")
    print("\n".join(describe(body)))
    print()
    no_tools = {k: v for k, v in body.items() if k != "tools"}
    for name, variant in (("as-is", body), ("no-tools", no_tools),
                          ("lang-hint", with_lang_hint(body))):
        print("\n".join(_report(name, _send(args.endpoint, variant))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
