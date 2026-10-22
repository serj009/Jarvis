"""Monobank integration — balance, transactions, currency rates.

Off by default. Uses Monobank API token. No external packages needed
(httpx is a core dependency).
"""

from __future__ import annotations

import logging
import time

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)

_MONO_API = "https://api.monobank.ua"


class MonobankIntegration(BaseIntegration):
    name = "monobank"
    display_name = "Monobank"
    category = "finance"
    description = "Balance, transactions, and currency rates from Monobank."
    icon = "\U0001f4b3"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "Monobank API Token", "type": "password"},
    ]
    required_packages = []  # httpx is a core dep

    auth: object | None = None

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    async def connect(self) -> bool:
        self.connected = bool(self._token())
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("mono_balance", "Show Monobank card balances.", {}),
            _fn("mono_transactions", "Recent transactions.",
                {"days": {"type": "integer", "description": "how many days back (max 31)"}},
                ),
            _fn("mono_rates", "Monobank currency exchange rates.", {}),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^my\s+balance$", "mono_balance", 300),
            (r"^\u043c\u043e\u0439\s+\u0431\u0430\u043b\u0430\u043d\u0441$", "mono_balance", 300),
            (r"^\u043c\u0456\u0439\s+\u0431\u0430\u043b\u0430\u043d\u0441$", "mono_balance", 300),
            (r"^last\s+transactions?$", "mono_transactions", 310),
            (r"^\u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435\s+\u0442\u0440\u0430\u043d\u0437\u0430\u043a\u0446\u0438\u0438$", "mono_transactions", 310),
            (r"^\u043e\u0441\u0442\u0430\u043d\u043d\u0456\s+\u0442\u0440\u0430\u043d\u0437\u0430\u043a\u0446\u0456\u0457$", "mono_transactions", 310),
            (r"^dollar\s+rate$", "mono_rates", 310),
            (r"^\u043a\u0443\u0440\u0441\s+\u0434\u043e\u043b\u043b\u0430\u0440\u0430$", "mono_rates", 310),
            (r"^\u043a\u0443\u0440\u0441\s+\u0434\u043e\u043b\u0430\u0440\u0430$", "mono_rates", 310),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        token = self._token()
        if token is None:
            return "Monobank is not connected, sir."
        try:
            import httpx  # noqa: E402
            headers = {"X-Token": token}
            async with httpx.AsyncClient(timeout=10) as client:
                if tool_name == "mono_balance":
                    return await self._balance(client, headers)
                if tool_name == "mono_transactions":
                    return await self._transactions(client, headers, args)
                if tool_name == "mono_rates":
                    return await self._rates(client)
        except Exception as e:
            log.warning("[monobank] %s failed", tool_name, exc_info=True)
            return f"Monobank error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _balance(self, client, headers: dict) -> str:
        r = await client.get(f"{_MONO_API}/personal/client-info", headers=headers)
        r.raise_for_status()
        data = r.json()
        accounts = data.get("accounts", [])
        if not accounts:
            return "No accounts found, sir."
        lines = []
        for acc in accounts:
            bal = acc["balance"] / 100
            cur = _iso_currency(acc.get("currencyCode", 0))
            lines.append(f"  \u2022 {cur}: {bal:,.2f}")
        return "Your balances, sir:\n" + "\n".join(lines)

    async def _transactions(self, client, headers: dict, args: dict) -> str:
        days = min(int(args.get("days", 7)), 31)
        now = int(time.time())
        from_ts = now - days * 86400
        r = await client.get(
            f"{_MONO_API}/personal/statement/0/{from_ts}/{now}", headers=headers,
        )
        r.raise_for_status()
        txns = r.json()
        if not txns:
            return f"No transactions in the last {days} days, sir."
        lines = []
        for t in txns[:15]:
            amount = t["amount"] / 100
            desc = t.get("description", "")
            lines.append(f"  \u2022 {amount:+,.2f} — {desc}")
        return f"Recent transactions ({days}d), sir:\n" + "\n".join(lines)

    async def _rates(self, client) -> str:
        r = await client.get(f"{_MONO_API}/bank/currency")
        r.raise_for_status()
        rates = r.json()
        usd = next((x for x in rates if x.get("currencyCodeA") == 840
                     and x.get("currencyCodeB") == 980), None)
        eur = next((x for x in rates if x.get("currencyCodeA") == 978
                     and x.get("currencyCodeB") == 980), None)
        lines = []
        if usd:
            lines.append(f"  USD/UAH: buy {usd.get('rateBuy', '?')}, sell {usd.get('rateSell', '?')}")
        if eur:
            lines.append(f"  EUR/UAH: buy {eur.get('rateBuy', '?')}, sell {eur.get('rateSell', '?')}")
        return "Monobank rates, sir:\n" + "\n".join(lines) if lines else "No rates available, sir."


def _iso_currency(code: int) -> str:
    return {840: "USD", 978: "EUR", 980: "UAH", 826: "GBP", 985: "PLN"}.get(code, str(code))


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
