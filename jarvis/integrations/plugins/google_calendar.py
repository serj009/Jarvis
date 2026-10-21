"""Google Calendar integration — events, scheduling, proactive briefings.

Off by default. Shares the Google OAuth token (see _google_common). Heavy
Google SDK imports are lazy (inside execute paths) so discovery works with
the packages absent.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from jarvis.integrations.base import BaseIntegration
from jarvis.integrations.plugins._google_common import GoogleIntegrationMixin

log = logging.getLogger(__name__)


class GoogleCalendarIntegration(GoogleIntegrationMixin, BaseIntegration):
    name = "google_calendar"
    display_name = "Google Calendar"
    category = "productivity"
    description = "Your events and meetings; feeds morning briefings."
    icon = "\U0001f4c5"
    scopes = ["https://www.googleapis.com/auth/calendar"]

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("calendar_today", "List today's calendar events.", {}),
            _fn("calendar_week", "List this week's calendar events.", {}),
            _fn(
                "calendar_create",
                "Create a calendar event.",
                {
                    "title": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "time": {"type": "string", "description": "HH:MM 24h"},
                    "duration": {"type": "integer", "description": "minutes"},
                },
                required=["title", "date", "time"],
            ),
            _fn(
                "calendar_delete",
                "Delete a calendar event by title or time.",
                {"query": {"type": "string"}},
                required=["query"],
            ),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^what'?s\s+on\s+my\s+calendar\s+today$", "calendar_today", 300),
            (r"^что\s+у\s+меня\s+сегодня$", "calendar_today", 300),
            (r"^що\s+в\s+мене\s+сьогодні$", "calendar_today", 300),
            (r"^what'?s\s+(?:on\s+)?this\s+week$", "calendar_week", 300),
            (r"^что\s+на\s+этой\s+неделе$", "calendar_week", 300),
            (r"^що\s+на\s+цьому\s+тижні$", "calendar_week", 300),
            (r"^create\s+meeting\s+(.+)$", "calendar_create", 320),
            (r"^создай\s+встречу\s+(.+)$", "calendar_create", 320),
            (r"^створи\s+зустріч\s+(.+)$", "calendar_create", 320),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        try:
            svc = self._service("calendar", "v3")
        except Exception as e:
            return str(e)
        try:
            if tool_name == "calendar_today":
                return self._list_events(svc, days=1)
            if tool_name == "calendar_week":
                return self._list_events(svc, days=7)
            if tool_name == "calendar_create":
                return self._create_event(svc, args)
            if tool_name == "calendar_delete":
                return self._delete_event(svc, args.get("query", ""))
        except Exception as e:
            log.warning("[google_calendar] %s failed", tool_name, exc_info=True)
            return f"Calendar error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- proactive -------------------------------------------------------

    def get_proactive_data(self) -> list[dict] | None:
        """Next 7 days of events, shaped for ProactiveScenarios.upcoming_date."""
        if not self.connected:
            return None
        try:
            svc = self._service("calendar", "v3")
            now = datetime.now(tz=UTC)
            later = now + timedelta(days=7)
            resp = svc.events().list(
                calendarId="primary",
                timeMin=now.isoformat(), timeMax=later.isoformat(),
                singleEvents=True, orderBy="startTime",
            ).execute()
            out: list[dict] = []
            for ev in resp.get("items", []):
                start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date")
                if not start:
                    continue
                try:
                    when = datetime.fromisoformat(start.replace("Z", "+00:00"))
                except ValueError:
                    continue
                days = (when.date() - now.date()).days
                out.append({
                    "name": ev.get("summary", "event"),
                    "date": start[:10],
                    "days_until": days,
                })
            return out
        except Exception:
            log.warning("[google_calendar] proactive fetch failed", exc_info=True)
            return None

    # -- internals -------------------------------------------------------

    def _list_events(self, svc, *, days: int) -> str:
        now = datetime.now(tz=UTC)
        later = now + timedelta(days=days)
        resp = svc.events().list(
            calendarId="primary",
            timeMin=now.isoformat(), timeMax=later.isoformat(),
            singleEvents=True, orderBy="startTime",
        ).execute()
        items = resp.get("items", [])
        if not items:
            span = "today" if days == 1 else "this week"
            return f"Nothing on your calendar {span}, sir."
        lines = []
        for ev in items[:20]:
            start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
            lines.append(f"  • {start[:16].replace('T', ' ')} — {ev.get('summary', '(no title)')}")
        return "Here's what you have, sir:\n" + "\n".join(lines)

    def _create_event(self, svc, args: dict) -> str:
        title = args.get("title", "Untitled")
        date = args.get("date", "")
        time = args.get("time", "09:00")
        duration = int(args.get("duration") or 60)
        start_dt = datetime.fromisoformat(f"{date}T{time}:00")
        end_dt = start_dt + timedelta(minutes=duration)
        body = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
        }
        svc.events().insert(calendarId="primary", body=body).execute()
        return f"Created '{title}' on {date} at {time}, sir."

    def _delete_event(self, svc, query: str) -> str:
        now = datetime.now(tz=UTC)
        later = now + timedelta(days=30)
        resp = svc.events().list(
            calendarId="primary", timeMin=now.isoformat(),
            timeMax=later.isoformat(), singleEvents=True, orderBy="startTime",
        ).execute()
        q = query.lower()
        for ev in resp.get("items", []):
            if q in ev.get("summary", "").lower():
                svc.events().delete(calendarId="primary", eventId=ev["id"]).execute()
                return f"Deleted '{ev.get('summary')}', sir."
        return f"No event matching '{query}', sir."


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
