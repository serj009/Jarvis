"""Data leak scanner for outgoing requests (Security Layer 2).

Scans ALL outgoing data (URLs, request bodies) for the presence of
the user's personal information.  Works INDEPENDENTLY from the LLM —
it is a separate check that the LLM cannot bypass or influence.

Checks performed:
    1. URL / body contains the user's personal data (name, email, city…)
    2. URL / body contains base64-encoded personal data
    3. URL / body contains hex-encoded personal data
    4. URL matches known exfiltration domain patterns
    5. URL has suspicious structural characteristics (very long query
       strings, excessive parameters)

Decision matrix (see ``tool_guard.py`` for enforcement):
    USER_PATTERN / USER_VOICE  + no leak → ALLOW
    USER_PATTERN / USER_VOICE  + leak    → WARN  (user may intend it)
    LLM_TOOL_CALL              + no leak → ALLOW
    LLM_TOOL_CALL              + leak    → BLOCK + alert user
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from urllib.parse import parse_qs, unquote_plus, urlparse

from jarvis.security.origin_tracker import RequestContext

if TYPE_CHECKING:
    from jarvis.memory.store import MemoryStore
    from jarvis.memory.user_profile import UserProfile

log = logging.getLogger(__name__)

# UserProfile fields scanned for sensitive values.
_PROFILE_KEYS = ("name", "city", "country", "timezone", "occupation")

# Strings shorter than this are too common to be useful indicators.
_MIN_SENSITIVE_LENGTH = 3


# ---------------------------------------------------------------------------
# Scan result
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    """Outcome of scanning a URL or text body for leaks."""

    action: Literal["allow", "block", "warn"]
    reasons: list[str] = field(default_factory=list)
    matched_data: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Known exfiltration / webhook services
# ---------------------------------------------------------------------------

_EXFIL_DOMAIN_FRAGMENTS: tuple[str, ...] = (
    "webhook.site",
    "requestbin",
    "pipedream",
    "hookbin",
    "ngrok.io",
    "ngrok-free.app",
    "burpcollaborator",
    "interact.sh",
    "oast.fun",
    "canarytokens",
    "requestcatcher",
    "beeceptor",
    "mockbin",
    "postb.in",
)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class DataLeakScanner:
    """Scans outgoing requests for potential data exfiltration.

    Constructed with the UserProfile and (optionally) MemoryStore so it
    knows which strings are sensitive.  The set of sensitive strings can
    be refreshed at any time via ``refresh()`` / ``add_sensitive_string()``.
    """

    def __init__(
        self,
        user_profile: UserProfile | None = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self._sensitive_strings: set[str] = set()
        self._update_sensitive_data(user_profile, memory_store)

    # -- sensitive-data management ----------------------------------------

    def _update_sensitive_data(
        self,
        profile: UserProfile | None,
        memory: MemoryStore | None,
    ) -> None:
        """Rebuild the full set of sensitive strings from all sources."""
        strings: set[str] = set()

        # ---- UserProfile fields ----
        if profile is not None:
            for key in _PROFILE_KEYS:
                val = profile.get(key)
                if isinstance(val, str) and len(val) >= _MIN_SENSITIVE_LENGTH:
                    strings.add(val)
                    strings.add(val.lower())
            # Interests list
            interests = profile.get("interests")
            if isinstance(interests, list):
                for item in interests:
                    if isinstance(item, str) and len(item) >= _MIN_SENSITIVE_LENGTH:
                        strings.add(item)
                        strings.add(item.lower())
            # Custom fields (may contain email, phone, etc.)
            raw_data = getattr(profile, "_data", None) or {}
            custom = raw_data.get("custom", {})
            if isinstance(custom, dict):
                for _k, v in custom.items():
                    if isinstance(v, str) and len(v) >= _MIN_SENSITIVE_LENGTH:
                        strings.add(v)
                        strings.add(v.lower())

        # ---- MemoryStore: recent facts (best-effort, sync) ----
        if memory is not None:
            self._collect_memory_strings(memory, strings)

        self._sensitive_strings = strings

    @staticmethod
    def _collect_memory_strings(
        memory: MemoryStore,
        out: set[str],
    ) -> None:
        """Extract personal-data fragments from recent memory facts.

        Uses a direct SQLite read (bypassing async) because the scanner
        is constructed synchronously.  Errors are swallowed — missing
        memory data degrades detection, it must never crash the scanner.
        """
        try:
            import sqlite3

            from jarvis.platform.encrypted_db import decrypt_value

            db_path = str(memory.db_path)
            conn = sqlite3.connect(db_path, timeout=2)
            try:
                rows = conn.execute(
                    "SELECT text FROM facts "
                    "ORDER BY created_at DESC LIMIT 100",
                ).fetchall()
            finally:
                conn.close()

            for (enc_text,) in rows:
                text = decrypt_value(enc_text) or enc_text
                # Only grab multi-word "entity" fragments (> 5 chars) that
                # are likely names / places / identifiers, not every word.
                for fragment in _extract_entity_fragments(text):
                    if len(fragment) >= _MIN_SENSITIVE_LENGTH:
                        out.add(fragment)
                        out.add(fragment.lower())
        except Exception:
            log.debug(
                "[leak-scanner] could not read memory store; "
                "continuing with profile data only",
                exc_info=True,
            )

    def refresh(
        self,
        user_profile: UserProfile | None = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        """Re-collect sensitive strings (call after profile changes)."""
        self._update_sensitive_data(user_profile, memory_store)

    def add_sensitive_string(self, s: str) -> None:
        """Manually register a sensitive string."""
        if len(s) >= _MIN_SENSITIVE_LENGTH:
            self._sensitive_strings.add(s)
            self._sensitive_strings.add(s.lower())

    @property
    def sensitive_count(self) -> int:
        return len(self._sensitive_strings)

    # -- scan entry points ------------------------------------------------

    def scan_url(self, url: str, context: RequestContext) -> ScanResult:
        """Scan a URL for data leaks.  Returns ALLOW / BLOCK / WARN."""
        reasons: list[str] = []
        matched: list[str] = []

        # Decode percent-encoding so %53erhii → Serhii
        decoded_url = unquote_plus(url)

        # 1. Personal data in URL
        personal = self._check_personal_data(decoded_url)
        if personal:
            reasons.append(
                f"Personal data in URL: {', '.join(personal)}"
            )
            matched.extend(personal)

        # 2. Encoded personal data (base64 / hex segments)
        encoded = self._check_encoded_data(url)
        if encoded:
            reasons.append(
                f"Encoded personal data: {', '.join(encoded)}"
            )
            matched.extend(encoded)

        # 3. Suspicious URL patterns
        suspicious = self._check_suspicious_patterns(url)
        if suspicious:
            reasons.extend(suspicious)

        return self._finalize(reasons, matched, context)

    def scan_text(self, text: str, context: RequestContext) -> ScanResult:
        """Scan arbitrary text (POST body, etc.) for leaks."""
        reasons: list[str] = []
        matched: list[str] = []

        personal = self._check_personal_data(text)
        if personal:
            reasons.append(
                f"Personal data in request body: {', '.join(personal)}"
            )
            matched.extend(personal)

        encoded = self._check_encoded_data(text)
        if encoded:
            reasons.append(
                f"Encoded personal data: {', '.join(encoded)}"
            )
            matched.extend(encoded)

        return self._finalize(reasons, matched, context)

    # -- detection helpers ------------------------------------------------

    def _check_personal_data(self, text: str) -> list[str]:
        """Find known sensitive strings inside *text*."""
        found: list[str] = []
        text_lower = text.lower()
        for sensitive in self._sensitive_strings:
            # Skip the lowered copy when checking — we compare case-insensitively
            # by lowering the text, and we only need each conceptual value once.
            if sensitive != sensitive.lower():
                continue  # skip the original-case duplicate
            if sensitive in text_lower:
                if sensitive not in found:
                    found.append(sensitive)
        return found

    def _check_encoded_data(self, text: str) -> list[str]:
        """Find base64 / hex segments that decode to sensitive data."""
        found: list[str] = []

        # --- base64 ---
        # Look for long-ish base64 strings that could carry a payload.
        # URL-safe base64 uses - and _ instead of + and /.
        for m in _B64_RE.finditer(text):
            segment = m.group()
            # Pad if necessary
            padded = segment + "=" * (-len(segment) % 4)
            try:
                decoded = base64.b64decode(
                    padded, altchars=b"-_", validate=False,
                ).decode("utf-8", errors="ignore")
            except Exception:
                continue
            for sensitive in self._sensitive_strings:
                if sensitive.lower() in decoded.lower():
                    found.append(f"base64({sensitive})")
                    break

        # --- hex ---
        for m in _HEX_RE.finditer(text):
            segment = m.group()
            try:
                decoded = bytes.fromhex(segment).decode(
                    "utf-8", errors="ignore",
                )
            except (ValueError, UnicodeDecodeError):
                continue
            for sensitive in self._sensitive_strings:
                if sensitive.lower() in decoded.lower():
                    found.append(f"hex({sensitive})")
                    break

        return found

    def _check_suspicious_patterns(self, url: str) -> list[str]:
        """Heuristic checks for exfiltration-style URLs."""
        reasons: list[str] = []
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()

        # Known exfiltration domains
        for fragment in _EXFIL_DOMAIN_FRAGMENTS:
            if fragment in host:
                reasons.append(f"Suspicious exfiltration domain: {host}")
                break

        # Very long query string (potential data dump)
        if parsed.query and len(parsed.query) > 2000:
            reasons.append(
                f"Unusually long query string ({len(parsed.query)} chars)"
            )

        # Many query parameters
        params = parse_qs(parsed.query)
        if len(params) > 15:
            reasons.append(
                f"Unusually many query parameters ({len(params)})"
            )

        # Long subdomain labels (DNS exfiltration)
        if host:
            labels = host.split(".")
            for label in labels:
                if len(label) > 63:
                    reasons.append(
                        "Subdomain label exceeds 63 chars "
                        "(possible DNS exfiltration)"
                    )
                    break

        return reasons

    # -- finalization -----------------------------------------------------

    @staticmethod
    def _finalize(
        reasons: list[str],
        matched: list[str],
        context: RequestContext,
    ) -> ScanResult:
        if not reasons:
            return ScanResult(action="allow")
        if context.is_user_initiated:
            return ScanResult(
                action="warn", reasons=reasons, matched_data=matched,
            )
        return ScanResult(
            action="block", reasons=reasons, matched_data=matched,
        )


# ---------------------------------------------------------------------------
# Regex patterns (module-level compilation)
# ---------------------------------------------------------------------------

# Base64: at least 8 chars of base64 alphabet (catches encoded names ≥6 bytes).
_B64_RE = re.compile(r"[A-Za-z0-9+/\-_]{8,}={0,2}")

# Hex: at least 6 hex-char pairs (12 chars = 6 bytes, shortest useful name).
_HEX_RE = re.compile(r"(?:[0-9a-fA-F]{2}){6,}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Regex for extracting "entity-like" fragments from memory text.
# Matches sequences of word chars (including unicode letters), optionally
# separated by single spaces — e.g. "Zielona Góra", "Serhii".
_ENTITY_RE = re.compile(
    r"\b[A-ZА-ЯІЇЄҐ][a-zа-яіїєґA-ZА-ЯІЇЄҐ]+(?:\s+[A-ZА-ЯІЇЄҐ][a-zа-яіїєґA-ZА-ЯІЇЄҐ]+)*\b"
)


def _extract_entity_fragments(text: str) -> list[str]:
    """Pull capitalized entity-like phrases from free text.

    Only returns fragments that start with an uppercase letter (likely
    proper nouns) and are at least ``_MIN_SENSITIVE_LENGTH`` long.
    """
    return [m.group() for m in _ENTITY_RE.finditer(text)]
