"""Gmail integration — inbox, read, send, search.

Off by default. Shares the Google OAuth token (see _google_common). Heavy
Google SDK imports are lazy (inside execute paths) so discovery works with
the packages absent.
"""

from __future__ import annotations

import base64
import logging
from email.mime.text import MIMEText

from jarvis.integrations.base import BaseIntegration
from jarvis.integrations.plugins._google_common import GoogleIntegrationMixin

log = logging.getLogger(__name__)


class GmailIntegration(GoogleIntegrationMixin, BaseIntegration):
    name = "gmail"
    display_name = "Gmail"
    category = "communication"
    description = "Read, search, and send email; feeds proactive inbox alerts."
    icon = "\U0001f4e7"
    scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("gmail_inbox", "List recent inbox messages (unread first).", {}),
            _fn(
                "gmail_read",
                "Read a single message by search query (subject/sender).",
                {"query": {"type": "string"}},
                required=["query"],
            ),
            _fn(
                "gmail_send",
                "Send an email.",
                {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                required=["to", "subject", "body"],
            ),
            _fn(
                "gmail_search",
                "Search messages with a Gmail query string.",
                {"query": {"type": "string"}},
                required=["query"],
            ),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^check\s+my\s+(?:e)?mail$", "gmail_inbox", 300),
            (
                r"^\u043f\u0440\u043e\u0432\u0435\u0440\u044c\s+\u043f\u043e\u0447\u0442\u0443$",
                "gmail_inbox",
                300,
            ),
            (
                r"^\u043f\u0435\u0440\u0435\u0432\u0456\u0440\u0442\u0435?\s+\u043f\u043e\u0448\u0442\u0443$",
                "gmail_inbox",
                300,
            ),
            (r"^read\s+(?:e)?mail\s+(?:from\s+)?(.+)$", "gmail_read", 320),
            (
                r"^\u043f\u0440\u043e\u0447\u0442\u0438\s+\u043f\u0438\u0441\u044c\u043c\u043e\s+(?:\u043e\u0442\s+)?(.+)$",
                "gmail_read",
                320,
            ),
            (
                r"^\u043f\u0440\u043e\u0447\u0438\u0442\u0430\u0439\s+\u043b\u0438\u0441\u0442\s+(?:\u0432\u0456\u0434\s+)?(.+)$",
                "gmail_read",
                320,
            ),
            (r"^search\s+(?:e)?mail\s+(?:for\s+)?(.+)$", "gmail_search", 310),
            (
                r"^\u043d\u0430\u0439\u0434\u0438\s+\u043f\u0438\u0441\u044c\u043c\u043e\s+(.+)$",
                "gmail_search",
                310,
            ),
            (
                r"^\u0437\u043d\u0430\u0439\u0434\u0438\s+\u043b\u0438\u0441\u0442\s+(.+)$",
                "gmail_search",
                310,
            ),
            (r"^send\s+(?:e)?mail\s+(.+)$", "gmail_send", 320),
            (
                r"^\u043e\u0442\u043f\u0440\u0430\u0432\u044c\s+\u043f\u0438\u0441\u044c\u043c\u043e\s+(.+)$",
                "gmail_send",
                320,
            ),
            (
                r"^\u0432\u0456\u0434\u043f\u0440\u0430\u0432\u0020?(?:\u043b\u0438\u0441\u0442)\s+(.+)$",
                "gmail_send",
                320,
            ),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        try:
            svc = self._service("gmail", "v1")
        except Exception as e:
            return str(e)
        try:
            if tool_name == "gmail_inbox":
                return self._inbox(svc)
            if tool_name == "gmail_read":
                return self._read(svc, args.get("query", ""))
            if tool_name == "gmail_send":
                return self._send(svc, args)
            if tool_name == "gmail_search":
                return self._search(svc, args.get("query", ""))
        except Exception as e:
            log.warning("[gmail] %s failed", tool_name, exc_info=True)
            return f"Mail error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- proactive -------------------------------------------------------

    def get_proactive_data(self) -> list[dict] | None:
        """Unread-count signal, shaped for proactive inbox alerts."""
        if not self.connected:
            return None
        try:
            svc = self._service("gmail", "v1")
            resp = svc.users().messages().list(
                userId="me", q="is:unread in:inbox", maxResults=50,
            ).execute()
            msgs = resp.get("messages", [])
            if not msgs:
                return None
            return [{"type": "unread_email", "count": len(msgs)}]
        except Exception:
            log.warning("[gmail] proactive fetch failed", exc_info=True)
            return None

    # -- internals -------------------------------------------------------

    def _inbox(self, svc) -> str:
        resp = svc.users().messages().list(
            userId="me", q="in:inbox", maxResults=10, labelIds=["INBOX"],
        ).execute()
        msgs = resp.get("messages", [])
        if not msgs:
            return "Your inbox is empty, sir."
        lines = []
        for m in msgs[:10]:
            full = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject"],
            ).execute()
            hdrs = _headers(full)
            unread = "UNREAD" in full.get("labelIds", [])
            mark = "\u2022" if unread else " "
            lines.append(
                f"  {mark} {hdrs.get('From', '?')[:30]} \u2014"
                f" {hdrs.get('Subject', '(no subject)')}"
            )
        return "Your inbox, sir:\n" + "\n".join(lines)

    def _read(self, svc, query: str) -> str:
        resp = svc.users().messages().list(
            userId="me", q=query or "in:inbox", maxResults=1,
        ).execute()
        msgs = resp.get("messages", [])
        if not msgs:
            return f"No message matching '{query}', sir."
        full = svc.users().messages().get(
            userId="me", id=msgs[0]["id"], format="full",
        ).execute()
        hdrs = _headers(full)
        body = _extract_body(full.get("payload", {}))
        return (
            f"From: {hdrs.get('From', '?')}\n"
            f"Subject: {hdrs.get('Subject', '(no subject)')}\n\n"
            f"{body[:1500]}"
        )

    def _send(self, svc, args: dict) -> str:
        to = args.get("to", "")
        subject = args.get("subject", "")
        body = args.get("body", "")
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        svc.users().messages().send(
            userId="me", body={"raw": raw},
        ).execute()
        return f"Sent to {to}, sir."

    def _search(self, svc, query: str) -> str:
        resp = svc.users().messages().list(
            userId="me", q=query, maxResults=10,
        ).execute()
        msgs = resp.get("messages", [])
        if not msgs:
            return f"No messages matching '{query}', sir."
        lines = []
        for m in msgs[:10]:
            full = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject"],
            ).execute()
            hdrs = _headers(full)
            lines.append(
                f"  \u2022 {hdrs.get('From', '?')[:30]} \u2014"
                f" {hdrs.get('Subject', '(no subject)')}"
            )
        return f"Found {len(msgs)} message(s), sir:\n" + "\n".join(lines)


def _headers(message: dict) -> dict:
    """Flatten a Gmail message's header list into a dict."""
    out = {}
    for h in message.get("payload", {}).get("headers", []):
        out[h.get("name", "")] = h.get("value", "")
    return out


def _extract_body(payload: dict) -> str:
    """Best-effort plain-text body extraction from a Gmail payload.

    Prefers a ``text/plain`` part anywhere in the MIME tree; only if none
    exists does it fall back to whatever body data is present (e.g. a
    single-part HTML message). This two-pass order matters: a multipart
    message lists ``text/html`` before ``text/plain``, and we must not let
    the HTML part's fallback data win over the real plain-text part.
    """
    if not payload:
        return ""
    plain = _find_plain(payload)
    if plain:
        return plain
    return _find_any(payload)


def _find_plain(payload: dict) -> str:
    """Depth-first search for a text/plain part's decoded data."""
    if payload.get("mimeType", "") == "text/plain":
        data = payload.get("body", {}).get("data")
        if data:
            return _b64(data)
    for part in payload.get("parts", []) or []:
        found = _find_plain(part)
        if found:
            return found
    return ""


def _find_any(payload: dict) -> str:
    """Fallback: first decodable body data anywhere in the tree."""
    data = payload.get("body", {}).get("data")
    if data:
        return _b64(data)
    for part in payload.get("parts", []) or []:
        found = _find_any(part)
        if found:
            return found
    return ""


def _b64(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data.encode()).decode("utf-8", "replace")
    except Exception:
        return ""


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
