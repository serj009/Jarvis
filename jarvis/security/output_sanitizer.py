"""Layer 3 — Output Sanitizer: inspects LLM output BEFORE tool execution.

Catches prompt injection, suspicious tool-call patterns, and data
exfiltration chains.  Sits between the LLM response parser (intent_router)
and the tool execution path (ToolRegistry.execute) — every response is
checked before any side-effect happens.

Three inspection surfaces
-------------------------
1. **Raw LLM text** — ``sanitize_llm_response()``.  Scans for hidden
   system/admin instructions, role-switching attempts, and other prompt
   injection markers that a compromised or manipulated model might emit.

2. **Parsed tool calls** — ``sanitize_tool_call()``.  Checks individual
   tool invocations for suspicious argument patterns (base64/hex blobs
   in URLs, personal data leaking as parameters).

3. **External content** — ``sanitize_external_content()``.  Strips
   prompt-injection payloads from web pages, RSS feeds, and other
   untrusted text *before* it enters the LLM context window.

4. **Tool chains** — ``check_tool_chain()``.  Detects multi-step
   exfiltration patterns (read_memory → open_url, see_screen → web_search)
   by analysing recent tool-call history.

Thread safety
-------------
All methods are stateless and re-entrant.  ``check_tool_chain`` only
reads the ``recent_calls`` list the caller provides — it does not mutate
shared state.

Non-Windows note
----------------
Nothing here is platform-specific.  Pure Python, zero extra dependencies.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SanitizeResult:
    """Outcome of a sanitizer check.

    ``safe`` is True when no issues were found.  When False,
    ``reason`` explains what triggered and ``matched_patterns``
    lists the specific regex/rule names that fired, so the audit
    log can record granular data.
    """

    safe: bool
    reason: str = ""
    matched_patterns: tuple[str, ...] = ()
    severity: Literal["none", "warning", "critical"] = "none"


# Convenience constructors — keeps call sites readable.
_SAFE = SanitizeResult(safe=True)


def _blocked(
    reason: str,
    patterns: tuple[str, ...] = (),
    severity: Literal["warning", "critical"] = "critical",
) -> SanitizeResult:
    return SanitizeResult(
        safe=False,
        reason=reason,
        matched_patterns=patterns,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# Pattern banks
# ---------------------------------------------------------------------------

# Prompt-injection patterns matched against raw LLM output and external
# content.  Each tuple is (human_label, compiled_regex).
INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("system_tag",
     re.compile(r"\[SYSTEM\]", re.IGNORECASE)),
    ("admin_tag",
     re.compile(r"\[ADMIN\]", re.IGNORECASE)),
    ("assistant_tag",
     re.compile(r"\[ASSISTANT\]", re.IGNORECASE)),
    ("ignore_previous",
     re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE)),
    ("forget_previous",
     re.compile(r"forget\s+(all\s+)?previous\s+(instructions|rules|context)",
                re.IGNORECASE)),
    ("role_switch_you_are",
     re.compile(r"you\s+are\s+now\s+", re.IGNORECASE)),
    ("role_switch_new_role",
     re.compile(r"new\s+role\s*:", re.IGNORECASE)),
    ("system_prompt_override",
     re.compile(r"system\s+prompt\s*:", re.IGNORECASE)),
    ("override_security",
     re.compile(r"override\s+(?:security|safety|rules)", re.IGNORECASE)),
    ("do_not_follow",
     re.compile(r"do\s+not\s+follow\s+(?:your|the)\s+(?:rules|instructions)",
                re.IGNORECASE)),
    ("pretend_to_be",
     re.compile(r"pretend\s+(?:you\s+are|to\s+be)\s+", re.IGNORECASE)),
    ("act_as_if",
     re.compile(r"act\s+as\s+(?:if|though)\s+", re.IGNORECASE)),
    ("disregard",
     re.compile(r"disregard\s+(?:all|previous|your)\s+", re.IGNORECASE)),
]

# Suspicious tool-call chain definitions.
# (set_of_data_reading_tools, set_of_data_sending_tools, human_reason)
SUSPICIOUS_CHAINS: list[tuple[frozenset[str], frozenset[str], str]] = [
    (
        frozenset({"search_memory", "get_user_profile"}),
        frozenset({"open_url", "web_search", "download_file", "web_page_fetch"}),
        "Memory/profile data may be exfiltrated via network request",
    ),
    (
        frozenset({"see_screen", "screenshot", "full_page_screenshot"}),
        frozenset({"open_url", "web_search"}),
        "Screenshot data may be exfiltrated",
    ),
    (
        frozenset({"save_page_knowledge", "content_reader"}),
        frozenset({"open_url"}),
        "Extracted knowledge may be redirected to external URL",
    ),
]

# Personal-data field names that should never appear as tool arguments
# going to network-facing tools.
_PII_KEYS: frozenset[str] = frozenset({
    "password", "secret", "token", "api_key", "apikey",
    "ssn", "credit_card", "card_number",
})

# Network-facing tool names (arguments checked for PII).
_NETWORK_TOOLS: frozenset[str] = frozenset({
    "open_url", "web_search", "download_file", "web_page_fetch",
})

# Suspicious URL patterns: long base64 or hex blobs in query params.
_SUSPICIOUS_URL_BLOB = re.compile(
    r"[?&][^=]+=(?:[A-Za-z0-9+/]{40,}={0,2}|[0-9a-fA-F]{40,})"
)

# Zero-width / invisible Unicode characters used to hide instructions.
_ZERO_WIDTH_CHARS = re.compile(
    r"[\u200b\u200c\u200d\u200e\u200f"
    r"\u2060\u2061\u2062\u2063\u2064"
    r"\ufeff\u00ad\u034f\u17b4\u17b5"
    r"\u2028\u2029\u202a-\u202e"
    r"\u2066-\u2069\u206a-\u206f]+",
)

# HTML comment containing instruction-like text.
_HTML_COMMENT_INJECTION = re.compile(
    r"<!--\s*(?:system|admin|ignore|override|you are now|new role)\b[^>]*-->",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# OutputSanitizer
# ---------------------------------------------------------------------------

class OutputSanitizer:
    """Analyses LLM output before tool calls are executed.

    Stateless — construct once (or per-session), call methods as needed.
    Every public method returns a ``SanitizeResult`` (or cleaned string)
    and never raises on valid input.

    Catches
    -------
    1. Hidden system instructions in LLM output
       ("[SYSTEM]", "[ADMIN]", "ignore previous instructions").
    2. Suspicious tool-call patterns:
       - URL arguments containing base64/hex blobs.
       - Tool arguments leaking personal-data field names.
       - Multi-step exfiltration chains (read → send).
    3. Prompt-injection markers in external content (web pages, RSS,
       translated text) before they enter LLM context.
    """

    # ---- 1. Raw LLM response text -------------------------------------

    def sanitize_llm_response(self, response: str) -> SanitizeResult:
        """Check raw LLM response text for prompt-injection markers.

        Called on the full accumulated text of a streaming LLM response
        *before* any tool calls extracted from it are executed.
        """
        if not response:
            return _SAFE

        matched: list[str] = []
        for label, pattern in INJECTION_PATTERNS:
            if pattern.search(response):
                matched.append(label)

        if matched:
            preview = response[:120].replace("\n", " ")
            log.warning(
                "[output-sanitizer] injection pattern(s) %s in LLM response: %s…",
                matched, preview,
            )
            return _blocked(
                reason=f"LLM response contains injection pattern(s): {', '.join(matched)}",
                patterns=tuple(matched),
                severity="critical",
            )

        return _SAFE

    # ---- 2. Individual tool calls -------------------------------------

    def sanitize_tool_call(
        self, tool_name: str, args: dict,
    ) -> SanitizeResult:
        """Check a parsed tool call for suspicious argument patterns.

        Inspects URL arguments for encoded blobs (potential exfiltration
        payloads) and looks for personal-data keys in network-facing
        tool arguments.
        """
        issues: list[str] = []

        # -- Blob-in-URL check --
        if tool_name in _NETWORK_TOOLS:
            for key, value in args.items():
                if not isinstance(value, str):
                    continue
                if _SUSPICIOUS_URL_BLOB.search(value):
                    issues.append(f"suspicious_url_blob_in_{key}")

        # -- PII-key leak check --
        if tool_name in _NETWORK_TOOLS:
            for key in args:
                if key.lower() in _PII_KEYS:
                    issues.append(f"pii_key_{key}")

        if issues:
            log.warning(
                "[output-sanitizer] suspicious tool call %s(%s): %s",
                tool_name, list(args.keys()), issues,
            )
            return _blocked(
                reason=f"Suspicious tool call {tool_name}: {', '.join(issues)}",
                patterns=tuple(issues),
                severity="warning",
            )

        return _SAFE

    # ---- 3. External content stripping --------------------------------

    def sanitize_external_content(self, content: str, source: str) -> str:
        """Strip prompt-injection attempts from external content.

        Called on text fetched from untrusted sources (web pages, RSS
        feeds, user-pasted URLs) *before* it is added to the LLM
        context window.  Returns cleaned text.

        Removes:
        - ``[SYSTEM]``, ``[ADMIN]``, ``[ASSISTANT]`` prefixed blocks
        - "ignore previous instructions" style patterns
        - "you are now" role-switching attempts
        - Hidden Unicode / zero-width characters
        - HTML comments containing instructions
        """
        if not content:
            return content

        cleaned = content

        # Strip zero-width / invisible Unicode characters.
        cleaned = _ZERO_WIDTH_CHARS.sub("", cleaned)

        # Strip HTML comment injections.
        cleaned = _HTML_COMMENT_INJECTION.sub("", cleaned)

        # Strip injection-pattern lines.
        # We remove entire lines containing injection patterns rather
        # than just the match, because the surrounding text is likely
        # part of the payload.
        lines: list[str] = []
        stripped_count = 0
        for line in cleaned.split("\n"):
            is_injection = False
            for _label, pattern in INJECTION_PATTERNS:
                if pattern.search(line):
                    is_injection = True
                    stripped_count += 1
                    break
            if not is_injection:
                lines.append(line)

        cleaned = "\n".join(lines)

        if stripped_count > 0:
            log.info(
                "[output-sanitizer] stripped %d injection line(s) from "
                "external content (source=%s)",
                stripped_count, source,
            )

        return cleaned

    # ---- 4. Tool-chain analysis ---------------------------------------

    def check_tool_chain(
        self, recent_calls: list[dict],
    ) -> SanitizeResult:
        """Detect suspicious sequences in recent tool-call history.

        ``recent_calls`` is a list of dicts with at least a ``"tool_name"``
        key, ordered chronologically (oldest first).

        Suspicious patterns:
        - recall/search_memory → open_url  (memory exfiltration)
        - see_screen → open_url            (screenshot exfiltration)
        - user_profile_read → any_network_tool (profile exfiltration)
        """
        if len(recent_calls) < 2:
            return _SAFE

        # Build a set of all tool names seen so far (the "read" side).
        # Then walk forward: each time we see a "send" tool, check if
        # any prior "read" tool matches a suspicious chain.
        seen_tools: set[str] = set()
        triggered: list[str] = []

        for call in recent_calls:
            tool_name = call.get("tool_name", "")

            for read_set, send_set, reason in SUSPICIOUS_CHAINS:
                if tool_name in send_set and seen_tools & read_set:
                    triggered.append(reason)

            seen_tools.add(tool_name)

        if triggered:
            # De-duplicate while preserving order.
            unique: list[str] = list(dict.fromkeys(triggered))
            log.warning(
                "[output-sanitizer] suspicious tool chain detected: %s",
                unique,
            )
            return _blocked(
                reason=f"Suspicious tool chain: {'; '.join(unique)}",
                patterns=tuple(unique),
                severity="critical",
            )

        return _SAFE


# ---------------------------------------------------------------------------
# Module-level convenience helper
# ---------------------------------------------------------------------------

# A process-wide stateless sanitizer so tools that only need to clean
# external text don't each construct their own instance. OutputSanitizer is
# stateless and re-entrant, so sharing one is safe.
_SHARED_SANITIZER = OutputSanitizer()


def clean_external_text(content: str, source: str) -> str:
    """Strip prompt-injection payloads from untrusted external text.

    Thin module-level wrapper over ``OutputSanitizer.sanitize_external_content``
    for tools (web readers, OCR, MCP responses) that pull text from outside
    and feed it into the LLM context. Returns the cleaned text; on any
    unexpected error it returns the original content unchanged (fail-open on
    CLEANING is acceptable — this is a best-effort second line of defence,
    and dropping the user's content entirely would be worse than passing it
    through for the hard gates downstream to handle).
    """
    if not content:
        return content
    try:
        return _SHARED_SANITIZER.sanitize_external_content(content, source)
    except Exception:  # pragma: no cover - defensive
        log.debug(
            "[output-sanitizer] external-content clean failed for %s",
            source, exc_info=True,
        )
        return content
