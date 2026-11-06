"""Shared fakes for the integration plugin tests."""

from __future__ import annotations


class FakeAuth:
    """In-memory jarvis.integrations.auth.AuthSource: one api key, one token
    bundle and extra fields, whatever integration asks."""

    def __init__(
        self,
        api_key: str | None = None,
        tokens: dict | None = None,
        fields: dict[str, str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._tokens = tokens
        self._fields = fields or {}

    def get_api_key(self, integration_name: str) -> str | None:
        return self._api_key

    def get_tokens(self, integration_name: str) -> dict | None:
        return self._tokens

    def get_field(self, integration_name: str, field: str) -> str | None:
        return self._fields.get(field)
