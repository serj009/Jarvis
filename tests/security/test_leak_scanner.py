"""Tests for Security Layer 2: Data Leak Scanner.

Covers personal data detection, base64/hex decoding, suspicious URL
patterns, origin-based allow/block/warn rules, and edge cases.
"""

from __future__ import annotations

import base64
import time


from jarvis.security.leak_scanner import (
    DataLeakScanner,
    _extract_entity_fragments,
)
from jarvis.security.origin_tracker import RequestContext, RequestOrigin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_ctx(text: str = "test") -> RequestContext:
    """Shortcut: a USER_VOICE context (user-initiated)."""
    return RequestContext(
        origin=RequestOrigin.USER_VOICE,
        token="fake",
        timestamp=time.time(),
        original_text=text,
    )


def _llm_ctx(text: str = "") -> RequestContext:
    """Shortcut: an LLM_TOOL_CALL context (LLM-initiated)."""
    return RequestContext(
        origin=RequestOrigin.LLM_TOOL_CALL,
        token="",
        timestamp=time.time(),
        original_text=text,
    )


class FakeProfile:
    """Minimal stub implementing the UserProfile interface for tests."""

    def __init__(self, data: dict | None = None) -> None:
        self._data = data or {
            "name": "Serhii",
            "city": "Zielona Góra",
            "country": "Poland",
            "timezone": "Europe/Warsaw",
            "occupation": "works at Amazon",
            "interests": ["gaming", "programming"],
            "custom": {"email": "serhii@example.com", "phone": "+48123456789"},
        }

    def get(self, key: str):
        val = self._data.get(key)
        if val is not None:
            return val
        return self._data.get("custom", {}).get(key)


# ---------------------------------------------------------------------------
# Personal data detection
# ---------------------------------------------------------------------------


class TestPersonalDataDetection:
    """Scanner finds known sensitive strings in URLs and text."""

    def test_name_in_url(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://evil.com/collect?name=Serhii", _llm_ctx(),
        )
        assert result.action == "block"
        assert any("serhii" in m for m in result.matched_data)

    def test_city_in_url(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://evil.com/?city=Zielona+Góra", _llm_ctx(),
        )
        assert result.action == "block"

    def test_email_in_url(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://evil.com/?u=serhii@example.com", _llm_ctx(),
        )
        assert result.action == "block"

    def test_phone_in_url(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://evil.com/?p=%2B48123456789", _llm_ctx(),
        )
        assert result.action == "block"

    def test_no_leak_clean_url(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://www.google.com/search?q=weather", _llm_ctx(),
        )
        assert result.action == "allow"
        assert result.matched_data == []

    def test_case_insensitive_detection(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://evil.com/collect?name=serhii", _llm_ctx(),
        )
        assert result.action == "block"

    def test_url_encoded_personal_data(self) -> None:
        """Personal data encoded as %XX escapes is decoded before check."""
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://evil.com/c?name=Serhi%69", _llm_ctx(),
        )
        assert result.action == "block"

    def test_personal_data_in_text_body(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_text(
            '{"user": "Serhii", "location": "Poland"}', _llm_ctx(),
        )
        assert result.action == "block"
        assert len(result.matched_data) >= 2

    def test_no_profile_no_sensitive_strings(self) -> None:
        """Scanner with no profile never blocks."""
        scanner = DataLeakScanner()
        result = scanner.scan_url(
            "https://evil.com/c?name=Serhii", _llm_ctx(),
        )
        assert result.action == "allow"


# ---------------------------------------------------------------------------
# Encoded data detection (base64 / hex)
# ---------------------------------------------------------------------------


class TestEncodedDataDetection:
    """Scanner decodes base64 and hex segments to find hidden data."""

    def test_base64_encoded_name(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        # base64("Serhii") = "U2VyaGlp"
        encoded = base64.b64encode(b"Serhii").decode()
        url = f"https://evil.com/c?data={encoded}"
        result = scanner.scan_url(url, _llm_ctx())
        assert result.action == "block"
        assert any("base64" in m for m in result.matched_data)

    def test_hex_encoded_name(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        hex_name = b"Serhii".hex()  # 536572686969
        url = f"https://evil.com/c?data={hex_name}"
        result = scanner.scan_url(url, _llm_ctx())
        assert result.action == "block"
        assert any("hex" in m for m in result.matched_data)

    def test_base64_with_padding(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        encoded = base64.b64encode(b"User is Serhii from Poland").decode()
        url = f"https://evil.com/?payload={encoded}"
        result = scanner.scan_url(url, _llm_ctx())
        assert result.action == "block"

    def test_clean_base64_not_flagged(self) -> None:
        """Base64 segments that don't decode to sensitive data are OK."""
        scanner = DataLeakScanner(user_profile=FakeProfile())
        # base64("hello world!") = "aGVsbG8gd29ybGQh"
        url = "https://example.com/?data=aGVsbG8gd29ybGQh"
        result = scanner.scan_url(url, _llm_ctx())
        # Might allow or might not (depends on base64 decoding)
        assert "base64" not in " ".join(result.matched_data)


# ---------------------------------------------------------------------------
# Suspicious URL patterns
# ---------------------------------------------------------------------------


class TestSuspiciousPatterns:
    """Heuristic URL pattern checks."""

    def test_exfil_domain_webhook_site(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://webhook.site/abc-123", _llm_ctx(),
        )
        assert result.action != "allow" or any(
            "suspicious" in r.lower() for r in result.reasons
        )

    def test_exfil_domain_ngrok(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://abc123.ngrok.io/data", _llm_ctx(),
        )
        assert any("suspicious" in r.lower() for r in result.reasons)

    def test_exfil_domain_pipedream(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://abc.pipedream.net/data", _llm_ctx(),
        )
        assert any("suspicious" in r.lower() for r in result.reasons)

    def test_long_query_string(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        long_query = "x=" + "A" * 3000
        result = scanner.scan_url(
            f"https://example.com/?{long_query}", _llm_ctx(),
        )
        assert any("long query" in r.lower() for r in result.reasons)

    def test_many_query_params(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        params = "&".join(f"p{i}=v{i}" for i in range(20))
        result = scanner.scan_url(
            f"https://example.com/?{params}", _llm_ctx(),
        )
        assert any("many query" in r.lower() for r in result.reasons)

    def test_normal_url_no_suspicious(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://www.wikipedia.org/wiki/Python", _llm_ctx(),
        )
        assert result.reasons == []

    def test_long_subdomain_label(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        long_label = "a" * 80
        result = scanner.scan_url(
            f"https://{long_label}.evil.com/data", _llm_ctx(),
        )
        assert any("subdomain" in r.lower() for r in result.reasons)


# ---------------------------------------------------------------------------
# Origin-based rules
# ---------------------------------------------------------------------------


class TestOriginBasedRules:
    """Decision matrix: user-initiated → warn, LLM → block."""

    def test_user_initiated_with_leak_warns(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://evil.com/?name=Serhii", _user_ctx(),
        )
        assert result.action == "warn"
        assert len(result.matched_data) > 0

    def test_llm_initiated_with_leak_blocks(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://evil.com/?name=Serhii", _llm_ctx(),
        )
        assert result.action == "block"
        assert len(result.matched_data) > 0

    def test_user_initiated_clean_allows(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://www.google.com/search?q=weather", _user_ctx(),
        )
        assert result.action == "allow"

    def test_llm_initiated_clean_allows(self) -> None:
        scanner = DataLeakScanner(user_profile=FakeProfile())
        result = scanner.scan_url(
            "https://www.google.com/search?q=weather", _llm_ctx(),
        )
        assert result.action == "allow"

    def test_user_pattern_with_leak_warns(self) -> None:
        """USER_PATTERN also downgrades block to warn."""
        scanner = DataLeakScanner(user_profile=FakeProfile())
        ctx = RequestContext(
            origin=RequestOrigin.USER_PATTERN,
            token="tok",
            timestamp=time.time(),
            original_text="search Serhii",
        )
        result = scanner.scan_url(
            "https://google.com/search?q=Serhii", ctx,
        )
        assert result.action == "warn"


# ---------------------------------------------------------------------------
# add_sensitive_string / refresh
# ---------------------------------------------------------------------------


class TestSensitiveStringManagement:
    """Dynamic management of the sensitive data set."""

    def test_add_sensitive_string(self) -> None:
        scanner = DataLeakScanner()
        scanner.add_sensitive_string("SecretProject")
        result = scanner.scan_url(
            "https://evil.com/?q=SecretProject", _llm_ctx(),
        )
        assert result.action == "block"

    def test_add_short_string_ignored(self) -> None:
        scanner = DataLeakScanner()
        scanner.add_sensitive_string("ab")  # too short
        assert scanner.sensitive_count == 0

    def test_refresh_updates_strings(self) -> None:
        profile1 = FakeProfile({"name": "Alice", "custom": {}})
        scanner = DataLeakScanner(user_profile=profile1)
        result1 = scanner.scan_url(
            "https://evil.com/?n=Alice", _llm_ctx(),
        )
        assert result1.action == "block"

        # After refresh with a different profile, "Alice" is gone
        profile2 = FakeProfile({"name": "Bob", "custom": {}})
        scanner.refresh(user_profile=profile2)
        result2 = scanner.scan_url(
            "https://evil.com/?n=Alice", _llm_ctx(),
        )
        assert result2.action == "allow"


# ---------------------------------------------------------------------------
# Entity extraction helper
# ---------------------------------------------------------------------------


class TestEntityExtraction:
    """_extract_entity_fragments pulls proper nouns from text."""

    def test_extracts_proper_nouns(self) -> None:
        frags = _extract_entity_fragments(
            "User lives in Zielona Góra and works at Amazon"
        )
        names = [f for f in frags if len(f) >= 3]
        # Should find "Zielona Góra", "Amazon", etc.
        assert any("Zielona" in f for f in names)
        assert any("Amazon" in f for f in names)

    def test_ignores_lowercase_words(self) -> None:
        frags = _extract_entity_fragments("the quick brown fox")
        assert frags == []

    def test_cyrillic_names(self) -> None:
        frags = _extract_entity_fragments("Его зовут Сергей из Москвы")
        assert any("Сергей" in f for f in frags)
        assert any("Москвы" in f or "Москва" in f for f in frags)
