"""Pure, Qt-free helpers for research-source favicons (Task 9a).

Kept out of research_panel.py (which imports PySide6) so the logic —
domain validation, URL building, avatar letter/color, size cap — is unit-
testable in the headless .test_venv. The Qt widget/network code in
research_panel.py imports these.

No network, no Qt, stdlib only.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Max favicon payload we are willing to download (bytes). Larger replies are
# aborted — a favicon is tiny; anything big is either wrong or hostile.
FAVICON_SIZE_CAP = 64 * 1024

# A hostname: dot-separated labels of [A-Za-z0-9-], each 1..63 chars, TLD
# present. Deliberately strict — the domain comes from search results and is
# interpolated into a URL, so anything odd is rejected rather than fetched.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)

# Deterministic avatar palette (hashed by domain so a site keeps its color
# across runs). Muted, readable-on-white tones.
_AVATAR_COLORS = (
    "#4F6D7A", "#C0675A", "#5B8C5A", "#8A6DA8", "#C89B3C",
    "#4A8DB7", "#B5656B", "#6B8E7F", "#A0724E", "#7A7FB0",
)


def normalize_domain(raw: str) -> str:
    """Extract a bare hostname from a domain or URL, lowercased, no port.

    Accepts "example.com", "https://example.com/x", "Example.COM:8443" and
    returns "example.com". Returns "" when nothing usable is found."""
    if not raw:
        return ""
    s = raw.strip()
    if "://" in s:
        s = urlparse(s).hostname or ""
    else:
        # Could still be "host:port" or "host/path".
        s = s.split("/", 1)[0]
        s = urlparse("//" + s).hostname or s
    return (s or "").strip().lower()


def is_valid_favicon_domain(domain: str) -> bool:
    """True when `domain` is a plain hostname safe to build a URL from."""
    return bool(_DOMAIN_RE.match(domain or ""))


def favicon_url(domain: str, mode: str) -> str | None:
    """Build the favicon URL for a validated domain and mode, or None.

    None means "do not fetch" — for mode "off", an invalid domain, or an
    unknown mode (treated as off). Only https is ever produced."""
    d = normalize_domain(domain)
    if not is_valid_favicon_domain(d):
        return None
    if mode == "direct":
        return f"https://{d}/favicon.ico"
    if mode == "google":
        # sz=32 matches the rendered size; domain is already validated.
        return f"https://www.google.com/s2/favicons?domain={d}&sz=32"
    # "off" or any unknown mode -> no network.
    return None


def avatar_letter(domain: str) -> str:
    """Uppercase first alphanumeric character of the domain, or '?'.

    Used for the no-network letter avatar."""
    d = normalize_domain(domain)
    for ch in d:
        if ch.isalnum():
            return ch.upper()
    return "?"


def avatar_color(domain: str) -> str:
    """Deterministic hex color for the letter avatar, chosen by hashing the
    domain so the same site always gets the same color across runs.

    Uses a stable hash (not Python's randomized hash())."""
    import hashlib

    d = normalize_domain(domain) or "?"
    digest = hashlib.sha1(d.encode("utf-8")).digest()
    idx = digest[0] % len(_AVATAR_COLORS)
    return _AVATAR_COLORS[idx]


def exceeds_size(received_bytes: int, cap: int = FAVICON_SIZE_CAP) -> bool:
    """True when a download has grown past the cap and should be aborted."""
    return received_bytes > cap
