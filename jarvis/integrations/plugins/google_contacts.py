"""Google Contacts (People API) integration — lookup + birthdays.

Off by default. Shares the Google OAuth token. Lazy SDK imports.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from jarvis.integrations.base import BaseIntegration
from jarvis.integrations.plugins._google_common import GoogleIntegrationMixin

log = logging.getLogger(__name__)


class GoogleContactsIntegration(GoogleIntegrationMixin, BaseIntegration):
    name = "google_contacts"
    display_name = "Google Contacts"
    category = "communication"
    description = "Find contacts and surface upcoming birthdays."
    icon = "\U0001f465"
    scopes = ["https://www.googleapis.com/auth/contacts.readonly"]

    def get_tools(self) -> list[dict]:
        return [
            _fn(
                "contacts_find", "Find a contact by name.",
                {"name": {"type": "string"}}, required=["name"],
            ),
            _fn("contacts_birthdays", "List upcoming birthdays (next 14 days).", {}),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^find\s+contact\s+(.+)$", "contacts_find", 300),
            (r"^найди\s+контакт\s+(.+)$", "contacts_find", 300),
            (r"^знайди\s+контакт\s+(.+)$", "contacts_find", 300),
            (r"^whose\s+birthday\s+is\s+coming$", "contacts_birthdays", 300),
            (r"^у\s+кого\s+скоро\s+день\s+рождения$", "contacts_birthdays", 300),
            (r"^в\s+кого\s+скоро\s+день\s+народження$", "contacts_birthdays", 300),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        try:
            svc = self._service("people", "v1")
        except Exception as e:
            return str(e)
        try:
            if tool_name == "contacts_find":
                return self._find(svc, args.get("name", ""))
            if tool_name == "contacts_birthdays":
                return self._birthdays_text(svc)
        except Exception as e:
            log.warning("[google_contacts] %s failed", tool_name, exc_info=True)
            return f"Contacts error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    def get_proactive_data(self) -> list[dict] | None:
        """Birthdays within 14 days → ProactiveScenarios.upcoming_date."""
        if not self.connected:
            return None
        try:
            svc = self._service("people", "v1")
            return self._birthdays(svc, within_days=14)
        except Exception:
            log.warning("[google_contacts] proactive fetch failed", exc_info=True)
            return None

    # -- internals -------------------------------------------------------

    def _connections(self, svc) -> list[dict]:
        resp = svc.people().connections().list(
            resourceName="people/me",
            personFields="names,birthdays,emailAddresses",
            pageSize=500,
        ).execute()
        return resp.get("connections", [])

    def _find(self, svc, name: str) -> str:
        q = name.lower().strip()
        for person in self._connections(svc):
            names = person.get("names", [])
            display = names[0].get("displayName", "") if names else ""
            if q in display.lower():
                emails = person.get("emailAddresses", [])
                email = emails[0].get("value", "") if emails else "no email"
                return f"{display} — {email}, sir."
        return f"No contact matching '{name}', sir."

    def _birthdays(self, svc, *, within_days: int) -> list[dict]:
        today = datetime.now(tz=UTC).date()
        out: list[dict] = []
        for person in self._connections(svc):
            bdays = person.get("birthdays", [])
            names = person.get("names", [])
            who = names[0].get("displayName", "someone") if names else "someone"
            for b in bdays:
                d = b.get("date", {})
                mo, day = d.get("month"), d.get("day")
                if not mo or not day:
                    continue
                nxt = _next_occurrence(today, mo, day)
                days_until = (nxt - today).days
                if 0 <= days_until <= within_days:
                    out.append({
                        "name": who,
                        "date": nxt.isoformat(),
                        "days_until": days_until,
                    })
        return out

    def _birthdays_text(self, svc) -> str:
        items = self._birthdays(svc, within_days=14)
        if not items:
            return "No birthdays in the next two weeks, sir."
        items.sort(key=lambda x: x["days_until"])
        lines = [
            f"  • {b['name']} — {b['date']} (in {b['days_until']} days)"
            for b in items
        ]
        return "Upcoming birthdays, sir:\n" + "\n".join(lines)


def _next_occurrence(today: date, month: int, day: int) -> date:
    """Next date this month/day falls on, this year or next."""
    year = today.year
    try:
        candidate = date(year, month, day)
    except ValueError:
        # Feb 29 on a non-leap year → treat as Mar 1.
        candidate = date(year, month, 28)
    if candidate < today:
        try:
            candidate = date(year + 1, month, day)
        except ValueError:
            candidate = date(year + 1, month, 28)
    return candidate


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
