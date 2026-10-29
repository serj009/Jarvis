"""Generic banking integration — balance, transactions, rates.

Off by default.  Configurable REST endpoint + token.  The user provides
``base_url`` and endpoints in Settings.  httpx only.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class BankingIntegration(BaseIntegration):
    name = "banking"
    display_name = "Banking"
    category = "finance"
    description = "Check balance, transactions, and exchange rates (generic REST)."
    icon = "\U0001f4b0"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "Bank API Token", "type": "password"},
        {"name": "base_url", "label": "Bank API Base URL", "type": "text"},
    ]
    required_packages: list[str] = []

    auth: object | None = None

    # -- auth helpers ----------------------------------------------------

    def _token(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    def _base_url(self) -> str:
        if self.auth and hasattr(self.auth, "get_field"):
            u = self.auth.get_field(self.name, "base_url")
            if u:
                return u.rstrip("/")
        return "https://api.bank.example.com"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json"}

    async def connect(self) -> bool:
        self.connected = bool(self._token())
        if not self.connected:
            log.info("[banking] no token yet — add in Settings")
        return self.connected

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return bool(self._token())

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("bank_balance", "Check bank account balance.", {}),
            _fn("bank_transactions", "List recent transactions.",
                {"limit": {"type": "integer", "description": "Max results"}},
                required=[]),
            _fn("bank_rates", "Get current exchange rates.",
                {"currency": {"type": "string", "description": "e.g. USD, EUR"}},
                required=[]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^bank\s+balance$", "bank_balance", 300),
            (r"^баланс\s+банка$", "bank_balance", 300),
            (r"^баланс\s+банку$", "bank_balance", 300),
            (r"^bank\s+transactions$", "bank_transactions", 300),
            (r"^транзакции\s+банка$", "bank_transactions", 300),
            (r"^транзакції\s+банку$", "bank_transactions", 300),
            (r"^exchange\s+rate(?:s)?\s*(.*)$", "bank_rates", 300),
            (r"^курс\s+валют(?:\s+(.+))?$", "bank_rates", 300),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if not self._token():
            return "Banking is not connected, sir."

        base = self._base_url()
        try:
            if tool_name == "bank_balance":
                return await self._balance(base)
            if tool_name == "bank_transactions":
                return await self._transactions(base, args.get("limit", 10))
            if tool_name == "bank_rates":
                return await self._rates(base, args.get("currency", ""))
        except Exception as e:
            log.warning("[banking] %s failed", tool_name, exc_info=True)
            return f"Banking error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _balance(self, base: str) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{base}/accounts/balance",
                            headers=self._headers())
            r.raise_for_status()
        data = r.json()
        bal = data.get("balance", data.get("amount", "?"))
        cur = data.get("currency", "")
        return f"Your balance: {bal} {cur}, sir."

    async def _transactions(self, base: str, limit: int) -> str:
        import httpx

        async with httpx.AsyncClient() as c:
            r = await c.get(f"{base}/accounts/transactions",
                            headers=self._headers(),
                            params={"limit": min(limit, 20)})
            r.raise_for_status()
        txns = r.json() if isinstance(r.json(), list) else r.json().get("transactions", [])
        if not txns:
            return "No recent transactions, sir."
        lines = []
        for t in txns[:15]:
            amt = t.get("amount", "?")
            desc = t.get("description", t.get("merchant", "?"))
            lines.append(f"  \u2022 {desc}: {amt}")
        return f"{len(txns)} transaction(s), sir:\n" + "\n".join(lines)

    async def _rates(self, base: str, currency: str) -> str:
        import httpx

        params = {}
        if currency:
            params["currency"] = currency.upper()
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{base}/rates",
                            headers=self._headers(), params=params)
            r.raise_for_status()
        data = r.json()
        rates = data.get("rates", data)
        if isinstance(rates, dict):
            lines = [f"  \u2022 {k}: {v}" for k, v in list(rates.items())[:10]]
            return "Exchange rates, sir:\n" + "\n".join(lines)
        return f"Rates: {rates}"


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
