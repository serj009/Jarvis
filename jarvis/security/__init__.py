"""Security package — defense-in-depth layers protecting JARVIS from LLM compromise.

Layer 1: OriginTracker    — HMAC-based cryptographic proof that a tool call
                            originated from real user input (voice/keyboard).
Layer 2: DataLeakScanner  — scans all outgoing URLs and request bodies for
                            the user's personal data (plaintext, base64, hex).
Layer 1+2 gate: ToolGuard — unified security check point sitting between
                            IntentRouter and tool.execute().
Layer 3: OutputSanitizer  — inspects LLM responses and external content
                            before tool calls are executed.
Layer 4: SecurityAuditLog — tamper-evident, encrypted, append-only audit
                            trail of every security-relevant event.
"""

from jarvis.security.audit_log import SecurityAuditLog
from jarvis.security.leak_scanner import DataLeakScanner, ScanResult
from jarvis.security.origin_tracker import (
    OriginTracker,
    RequestContext,
    RequestOrigin,
    get_request_context,
    set_request_context,
)
from jarvis.security.output_sanitizer import OutputSanitizer
from jarvis.security.password_vault import PasswordVault, VaultEntry, VaultError, VaultLockedError
from jarvis.security.tool_guard import GuardResult, ToolGuard

__all__ = [
    # Layer 1
    "OriginTracker",
    "RequestContext",
    "RequestOrigin",
    "get_request_context",
    "set_request_context",
    # Layer 2
    "DataLeakScanner",
    "ScanResult",
    # Layer 1+2 gate
    "ToolGuard",
    "GuardResult",
    # Layer 3
    "OutputSanitizer",
    # Layer 4
    "SecurityAuditLog",
    # Password vault
    "PasswordVault",
    "VaultEntry",
    "VaultError",
    "VaultLockedError",
]
