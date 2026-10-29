"""Currency & crypto integration — prices, conversion, portfolio.

Off by default. Uses CoinGecko free tier API. No extra packages (httpx).
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)

_COINGECKO = "https://api.coingecko.com/api/v3"


class CurrencyCryptoIntegration(BaseIntegration):
    name = "currency"
    display_name = "Currency & Crypto"
    category = "finance"
    description = "Crypto prices, currency conversion, portfolio tracking."
    icon = "\U0001f4b0"

    auth_type = "api_key"
    auth_fields = [
        {"name": "api_key", "label": "CoinGecko API Key (free tier)", "type": "password"},
    ]
    required_packages = []

    auth: object | None = None

    def _api_key(self) -> str | None:
        if self.auth is None:
            return None
        return self.auth.get_api_key(self.name)

    async def connect(self) -> bool:
        # CoinGecko free tier works without a key too, but key gives higher limits
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.connected = False

    async def health_check(self) -> bool:
        return True

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("crypto_price", "Get cryptocurrency price.",
                {"coin": {"type": "string", "description": "e.g. bitcoin, ethereum"},
                 "currency": {"type": "string", "description": "e.g. usd, uah"}},
                required=["coin"]),
            _fn("currency_convert", "Convert between currencies.",
                {"amount": {"type": "number"}, "from_cur": {"type": "string"},
                 "to_cur": {"type": "string"}},
                required=["amount", "from_cur", "to_cur"]),
            _fn("crypto_portfolio", "Track crypto portfolio value.",
                {"holdings": {"type": "string",
                              "description": "JSON: {\"bitcoin\": 0.5, \"ethereum\": 2}"}},
                required=["holdings"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^bitcoin\s+price$", "crypto_price", 310),
            (
                r"^\u0446\u0435\u043d\u0430\s+\u0431\u0438\u0442\u043a\u043e\u0438\u043d\u0430$",
                "crypto_price",
                310,
            ),
            (
                r"^\u0446\u0456\u043d\u0430\s+\u0431\u0456\u0442\u043a\u043e\u0457\u043d\u0430$",
                "crypto_price",
                310,
            ),
            (r"^convert\s+(\d+)\s+(\w+)\s+to\s+(\w+)$", "currency_convert", 320),
            (
                r"^\u043a\u043e\u043d\u0432\u0435\u0440\u0442\u0438\u0440\u0443\u0439\s+(.+)$",
                "currency_convert",
                320,
            ),
            (
                r"^\u043a\u043e\u043d\u0432\u0435\u0440\u0442\u0443\u0439\s+(.+)$",
                "currency_convert",
                320,
            ),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        try:
            import httpx  # noqa: E402
            headers = {}
            key = self._api_key()
            if key:
                headers["x-cg-demo-api-key"] = key
            async with httpx.AsyncClient(timeout=10) as client:
                if tool_name == "crypto_price":
                    return await self._crypto_price(client, headers, args)
                if tool_name == "currency_convert":
                    return await self._convert(client, headers, args)
                if tool_name == "crypto_portfolio":
                    return await self._portfolio(client, headers, args)
        except Exception as e:
            log.warning("[currency] %s failed", tool_name, exc_info=True)
            return f"Currency error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    async def _crypto_price(self, client, headers: dict, args: dict) -> str:
        coin = args.get("coin", "bitcoin").lower()
        currency = args.get("currency", "usd").lower()
        r = await client.get(
            f"{_COINGECKO}/simple/price",
            params={"ids": coin, "vs_currencies": currency},
            headers=headers,
        )
        r.raise_for_status()
        data = r.json()
        price = data.get(coin, {}).get(currency)
        if price is None:
            return f"Could not find price for {coin}, sir."
        return f"{coin.title()} is {price:,.2f} {currency.upper()}, sir."

    async def _convert(self, client, headers: dict, args: dict) -> str:
        amount = float(args.get("amount", 1))
        from_cur = args.get("from_cur", "usd").lower()
        to_cur = args.get("to_cur", "uah").lower()
        r = await client.get(
            f"{_COINGECKO}/simple/price",
            params={"ids": "tether", "vs_currencies": f"{from_cur},{to_cur}"},
            headers=headers,
        )
        r.raise_for_status()
        data = r.json().get("tether", {})
        rate_from = data.get(from_cur, 1)
        rate_to = data.get(to_cur, 1)
        if rate_from == 0:
            return "Conversion rate unavailable, sir."
        result = amount * (rate_to / rate_from)
        return f"{amount:,.2f} {from_cur.upper()} = {result:,.2f} {to_cur.upper()}, sir."

    async def _portfolio(self, client, headers: dict, args: dict) -> str:
        import json as _json
        holdings = _json.loads(args.get("holdings", "{}"))
        if not holdings:
            return "No holdings provided, sir."
        coins = ",".join(holdings.keys())
        r = await client.get(
            f"{_COINGECKO}/simple/price",
            params={"ids": coins, "vs_currencies": "usd"},
            headers=headers,
        )
        r.raise_for_status()
        data = r.json()
        total = 0.0
        lines = []
        for coin, qty in holdings.items():
            price = data.get(coin, {}).get("usd", 0)
            value = qty * price
            total += value
            lines.append(f"  \u2022 {coin}: {qty} × ${price:,.2f} = ${value:,.2f}")
        lines.append(f"\n  Total: ${total:,.2f}")
        return "Your portfolio, sir:\n" + "\n".join(lines)


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
