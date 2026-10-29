"""Translation integration — DeepL text translation.

Off by default. Uses DeepL API Free (500K chars/month). No extra packages.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)

_DEEPL_API = "https://api-free.deepl.com/v2"


class TranslationIntegration(BaseIntegration):
    name = "translation"
    display_name = "Translator (DeepL)"
    category = "information"
    description = "Translate text via DeepL API (auto-detect source language)."
    icon = "\U0001f310"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "DeepL API Key (Free)", "type": "password"},
    ]
    required_packages = []

    auth: object | None = None

    def _api_key(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    async def connect(self) -> bool:
        self.connected = bool(self._api_key())
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._api_key())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("translate_text", "Translate text (auto-detect source).",
                {"text": {"type": "string"}, "target_lang": {"type": "string",
                 "description": "e.g. UK, EN, DE, RU"}},
                required=["text", "target_lang"]),
            _fn("translate_clipboard", "Translate clipboard content.",
                {"target_lang": {"type": "string"}}, required=["target_lang"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^translate\s+(.+)\s+to\s+(\w+)$", "translate_text", 320),
            (
                r"^\u043f\u0435\u0440\u0435\u0432\u0435\u0434\u0438\s+(.+)\s+\u043d\u0430\s+(\w+)$",
                "translate_text",
                320,
            ),
            (
                r"^\u043f\u0435\u0440\u0435\u043a\u043b\u0430\u0434\u0438\s+(.+)\s+\u043d\u0430\s+(\w+)$",
                "translate_text",
                320,
            ),
            (r"^translate\s+clipboard$", "translate_clipboard", 310),
            (
                r"^\u043f\u0435\u0440\u0435\u0432\u0435\u0434\u0438\s+\u0431\u0443\u0444\u0435\u0440\s+\u043e\u0431\u043c\u0435\u043d\u0430$",
                "translate_clipboard",
                310,
            ),
            (
                r"^\u043f\u0435\u0440\u0435\u043a\u043b\u0430\u0434\u0438\s+\u0431\u0443\u0444\u0435\u0440\s+\u043e\u0431\u043c\u0456\u043d\u0443$",
                "translate_clipboard",
                310,
            ),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        key = self._api_key()
        if key is None:
            return "Translator is not connected, sir."
        try:
            if tool_name == "translate_text":
                return await self._translate(key, args)
            if tool_name == "translate_clipboard":
                return await self._translate_clipboard(key, args)
        except Exception as e:
            log.warning("[translation] %s failed", tool_name, exc_info=True)
            return f"Translation error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _translate(self, key: str, args: dict) -> str:
        import httpx  # noqa: E402
        text = args.get("text", "")
        target = args.get("target_lang", "EN").upper()
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{_DEEPL_API}/translate",
                headers={"Authorization": f"DeepL-Auth-Key {key}"},
                data={"text": text, "target_lang": target},
            )
            r.raise_for_status()
            data = r.json()
        translations = data.get("translations", [])
        if not translations:
            return "Translation failed, sir."
        result = translations[0]["text"]
        detected = translations[0].get("detected_source_language", "?")
        return f"[{detected}\u2192{target}] {result}"

    async def _translate_clipboard(self, key: str, args: dict) -> str:
        try:
            import subprocess
            result = subprocess.run(
                ["powershell", "-Command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=5,
            )
            text = result.stdout.strip()
        except Exception:
            return "Could not read clipboard, sir."
        if not text:
            return "Clipboard is empty, sir."
        return await self._translate(key, {"text": text, **args})


def _fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required or [],
            },
        },
    }
