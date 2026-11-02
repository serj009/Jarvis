"""Memory probe: what would Jarvis recall for a phrase? (dev tool, read-only)

    python -m jarvis.dev.memory_probe "какой у меня браузер" "в каком городе я живу"
    python -m jarvis.dev.memory_probe --ask "какой у меня браузер"

For each phrase it shows the three stages of auto-recall:
  1. candidates from MemoryStore.search (the OR query auto_recall builds);
  2. SmartSearch scores (auto_recall keeps score >= 0.3);
  3. the exact text that would be injected into the model's context.

With --ask it also sends the phrase to the configured Ollama model, the way
the router does (system prompt + injected memories + question), and prints
the answer twice: memories as a SECOND system message (what Conversation does
today) and merged into the ONE system message. That separates "recall did not
find the fact" from "the model got the fact and ignored it".

NOTE: prints facts from YOUR memory.db to the console.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from jarvis.memory.auto_recall import get_relevant_memories
from jarvis.memory.smart_search import SmartSearch
from jarvis.memory.store import MemoryStore, _encryption_active

_DEFAULT_OLLAMA_URL = "http://localhost:11434"


def _short(text: str, width: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _ask_ollama(llm_cfg, messages: list[dict]) -> str:
    import httpx

    url = getattr(llm_cfg, "base_url", None) or getattr(llm_cfg, "host", None)
    url = (url or _DEFAULT_OLLAMA_URL).rstrip("/")
    body = {
        "model": llm_cfg.model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"num_ctx": getattr(llm_cfg, "num_ctx", 16384)},
    }
    resp = httpx.post(f"{url}/api/chat", json=body, timeout=180)
    resp.raise_for_status()
    return " ".join(str(resp.json().get("message", {}).get("content", "")).split())


async def _probe(phrases: list[str], ask: bool) -> None:
    llm_cfg = None
    if ask:
        from jarvis.core.config import load_config

        llm_cfg = load_config().llm

    store = MemoryStore()
    await store.open()
    try:
        rows = await store.all_facts_for_embedding()
        still_encrypted = sum(1 for _, t in rows if t.startswith("dpapi:"))
        empty = sum(1 for _, t in rows if not t.strip())
        print(f"db:                {store.db_path}")
        print(f"encryption active: {_encryption_active()}")
        print(f"facts:             {len(rows)} "
              f"(not decrypted: {still_encrypted}, empty: {empty})")

        for phrase in phrases:
            print("\n" + "=" * 72)
            print(f"phrase: {phrase!r}")
            words = phrase.strip().split()
            query = " OR ".join(words) if len(words) > 1 else phrase
            raw = await store.search(query, limit=20)
            print(f"1) candidates: {len(raw)}")
            for fact in raw[:8]:
                print(f"     - [{fact.language}] {_short(fact.text)}")

            scored = SmartSearch().rank(raw, phrase, language="en", limit=5)
            print("2) scores (kept if >= 0.30):")
            for s in scored:
                print(f"     {s.score:.2f} text={s.text_score:.2f} "
                      f"lang={s.language_score:.1f}  {_short(s.fact.text, 50)}")

            injected = await get_relevant_memories(store, phrase)
            print("3) injected into the model:")
            print("     " + (injected.replace("\n", "\n     ") if injected else "(nothing)"))

            if llm_cfg is not None:
                system = llm_cfg.system_prompt or ""
                user = {"role": "user", "content": phrase}
                two = [{"role": "system", "content": system}]
                if injected:
                    two.append({"role": "system", "content": injected})
                two.append(user)
                merged_text = f"{system}\n\n{injected}" if injected else system
                one = [{"role": "system", "content": merged_text}, user]
                print(f"4) model {llm_cfg.model!r} answers:")
                print(f"     two system messages: {_ask_ollama(llm_cfg, two)}")
                print(f"     one system message:  {_ask_ollama(llm_cfg, one)}")
    finally:
        await store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show what auto-recall finds for a phrase")
    parser.add_argument("phrases", nargs="+", help="phrases as the user would say them")
    parser.add_argument("--ask", action="store_true", help="also ask the configured model")
    args = parser.parse_args(argv)
    asyncio.run(_probe(args.phrases, args.ask))
    return 0


if __name__ == "__main__":
    sys.exit(main())
