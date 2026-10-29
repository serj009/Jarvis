"""PrivatBank integration — public currency rates.

Off by default. No auth needed (public API). No extra packages (httpx).
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)

_PRIVAT_API = "https://api.privatbank.ua/p24api/pubinfo"


class PrivatBankIntegration(BaseIntegration):
    name = "privatbank"
    display_name = "PrivatBank"
    category = "finance"
    description = "Currency exchange rates from PrivatBank (public API)."
    icon = "\U0001f3e6"

    auth_type = "none"
    auth_fields = []
    required_packages = []

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return True

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("privat_rates", "PrivatBank currency rates (cash / non-cash / NBU).",
                {"exchange": {"type": "string",
                              "description": "5 (cash) or 11 (non-cash), default 5"}},
                ),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^privatbank\s+rates?$", "privat_rates", 310),
            (
                r"^\u043a\u0443\u0440\u0441\s+\u043f\u0440\u0438\u0432\u0430\u0442\u0431\u0430\u043d\u043a\u0430$",
                "privat_rates",
                310,
            ),
            (
                r"^\u043a\u0443\u0440\u0441\s+\u043f\u0440\u0438\u0432\u0430\u0442\u0431\u0430\u043d\u043a\u0443$",
                "privat_rates",
                310,
            ),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if tool_name != "privat_rates":
            return f"Unknown tool: {tool_name}"
        try:
            import httpx  # noqa: E402
            exchange = args.get("exchange", "5")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(_PRIVAT_API, params={"exchange": exchange, "json": ""})
                r.raise_for_status()
                rates = r.json()
            if not rates:
                return "No rates available, sir."
            lines = []
            for rt in rates:
                ccy = rt.get("ccy", "?")
                buy = rt.get("buy", "?")
                sale = rt.get("sale", "?")
                lines.append(f"  \u2022 {ccy}/UAH: buy {buy}, sell {sale}")
            label = "cash" if exchange == "5" else "non-cash"
            return f"PrivatBank rates ({label}), sir:\n" + "\n".join(lines)
        except Exception as e:
            log.warning("[privatbank] rates failed", exc_info=True)
            return f"PrivatBank error, sir: {e}"


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
