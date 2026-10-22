"""Unified security gate for all tool executions (Security Layers 1+2).

ToolGuard sits between IntentRouter and tool.execute().  It checks every
tool call through all security layers before allowing execution.

Enforcement rules:
    ┌──────────────────┬────────────┬──────────────────┐
    │ Origin           │ Leak found │ Action           │
    ├──────────────────┼────────────┼──────────────────┤
    │ USER_VOICE/PAT   │ No         │ ALLOW            │
    │ USER_VOICE/PAT   │ Yes        │ WARN (allow)     │
    │ LLM_TOOL_CALL    │ No         │ ALLOW *          │
    │ LLM_TOOL_CALL    │ Yes        │ BLOCK + alert    │
    └──────────────────┴────────────┴──────────────────┘

    * For EXEC_TOOLS, LLM-initiated calls are ALWAYS blocked (regardless
      of leak scan) because they can execute arbitrary code or exfiltrate
      data through indirect channels (DNS, spawned processes, etc.).

Two tool categories are checked:
    - NETWORK_TOOLS: tools that make direct HTTP requests — scanned for
      data leaks in URLs/queries.
    - EXEC_TOOLS: tools that can execute arbitrary code or commands —
      blocked for LLM-initiated calls (code_runner, run_command).  These
      can reach the network indirectly (ping, nslookup, spawned scripts)
      so the data-leak scanner alone is insufficient.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from jarvis.security.leak_scanner import DataLeakScanner, ScanResult
from jarvis.security.origin_tracker import RequestContext, RequestOrigin

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Network-capable tools that require leak scanning
# ---------------------------------------------------------------------------

NETWORK_TOOLS: frozenset[str] = frozenset({
    # Browser / navigation
    "open_url",
    "full_page_screenshot",
    # Web research
    "web_search",
    "web_page_fetch",
    "save_page_knowledge",
    # Downloading
    "download_file",
    # Price / shopping
    "price_lookup",
    # Page monitoring
    "start_page_monitor",
    "check_page_changes",
    "compare_pages",
    # Content processing
    "translate_page",
    "youtube_summary",
    "scrape_images",
    # Network utilities
    "speed_test",
    # RSS / news
    "subscribe_rss",
    "get_latest_news",
})


# ---------------------------------------------------------------------------
# Exec-capable tools — can run arbitrary code or reach the network indirectly
# ---------------------------------------------------------------------------

EXEC_TOOLS: frozenset[str] = frozenset({
    "code_runner",   # run_script / run_tests execute arbitrary Python
    "run_command",   # CLI commands (ping, nslookup can exfiltrate via DNS)
})

# All tools that need any form of security check.
_CHECKED_TOOLS: frozenset[str] = NETWORK_TOOLS | EXEC_TOOLS


# ---------------------------------------------------------------------------
# Guard result
# ---------------------------------------------------------------------------

@dataclass
class GuardResult:
    """Outcome of a ToolGuard check."""

    allowed: bool
    reason: str = ""
    scan_result: ScanResult | None = None


# ---------------------------------------------------------------------------
# ToolGuard
# ---------------------------------------------------------------------------

class ToolGuard:
    """Unified security gate checked before every tool execution.

    Construction requires a ``DataLeakScanner`` (which holds the user's
    sensitive data set).  The IntentRouter creates a ToolGuard lazily
    from its ``PersonalizationContext`` — no wiring changes in app.py.
    """

    def __init__(
        self,
        leak_scanner: DataLeakScanner | None = None,
    ) -> None:
        self._leak_scanner = leak_scanner or DataLeakScanner()

    @property
    def leak_scanner(self) -> DataLeakScanner:
        return self._leak_scanner

    # -- main entry point -------------------------------------------------

    def check(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: RequestContext,
    ) -> GuardResult:
        """Run all security checks on a pending tool call.

        Returns ``GuardResult(allowed=True)`` when execution may proceed,
        or ``GuardResult(allowed=False, reason=...)`` when the call must
        be blocked.
        """
        # ── EXEC_TOOLS: LLM-initiated calls are ALWAYS blocked ──────
        # These tools can execute arbitrary code or exfiltrate data
        # through indirect channels.  The user must confirm via the
        # normal risk-level confirmation gate; if the call originates
        # from the LLM (prompt injection risk), we block it outright.
        if tool_name in EXEC_TOOLS and not context.is_user_initiated:
            log.warning(
                "[security] BLOCKED LLM-initiated exec tool %r "
                "(origin=%s) — code execution requires user confirmation",
                tool_name,
                context.origin.name if context.origin else "unknown",
            )
            return GuardResult(
                allowed=False,
                reason=(
                    f"Security blocked: '{tool_name}' cannot be executed "
                    "from an LLM tool call. Please use a voice command or "
                    "the chat panel instead."
                ),
            )

        # ── Non-checked tools pass through ───────────────────────────
        if tool_name not in _CHECKED_TOOLS:
            return GuardResult(allowed=True)

        # ── NETWORK_TOOLS: leak scanning ─────────────────────────────
        url = self._extract_url(tool_name, args)

        if not url:
            return GuardResult(allowed=True)

        scan = self._leak_scanner.scan_url(url, context)

        # --- user-initiated: never block, at most warn ---
        if context.is_user_initiated:
            if scan.action == "block":
                scan = ScanResult(
                    action="warn",
                    reasons=scan.reasons,
                    matched_data=scan.matched_data,
                )
            if scan.action == "warn":
                log.warning(
                    "[security] user-initiated %r: data-leak warning: %s",
                    tool_name,
                    "; ".join(scan.reasons),
                )
            return GuardResult(allowed=True, scan_result=scan)

        # --- LLM-initiated: strict enforcement ---
        if scan.action == "block":
            log.warning(
                "[security] BLOCKED LLM-initiated tool %r: %s  "
                "(matched: %s)",
                tool_name,
                "; ".join(scan.reasons),
                ", ".join(scan.matched_data),
            )
            return GuardResult(
                allowed=False,
                reason=f"Security blocked: {'; '.join(scan.reasons)}",
                scan_result=scan,
            )

        # LLM-initiated, no issues detected → allow.
        return GuardResult(allowed=True, scan_result=scan)

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _extract_url(tool_name: str, args: dict[str, Any]) -> str:
        """Best-effort extraction of the outgoing URL from tool args.

        Different tools use different argument names for the URL.  We
        look at the most common ones; if nothing is found, scanning is
        skipped (fail-open for this specific check — the tool may not
        even be making a request with user data).
        """
        if "url" in args:
            return str(args["url"])
        if "query" in args:
            return str(args["query"])
        for v in args.values():
            if isinstance(v, str) and ("http://" in v or "https://" in v):
                return v
        return ""
