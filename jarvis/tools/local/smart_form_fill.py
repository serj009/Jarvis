"""Smart Form Fill — auto-fill forms using user profile data.

Reads the user profile (name, email, city, phone, address, etc.) and
matches form field labels to profile fields.  Fills input/select/textarea
elements but does NOT submit the form — the user confirms manually.

Requires Selenium for DOM interaction; gracefully fails if not available.

Dependencies (optional, checked at runtime):
    - selenium for DOM access
    - jarvis.memory.user_profile for profile data
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from jarvis.tools.registry import EmptyArgs, ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Field-matching map: label patterns → profile keys
# ---------------------------------------------------------------------------

# Each entry is (regex_pattern_for_label, profile_key).
# Checked case-insensitively against the field's label, name, id,
# placeholder, and aria-label attributes.

_FIELD_MAP: list[tuple[str, str]] = [
    # Name variants
    (r"(?:full[_ ]?name|ваше[_ ]?имя|ім['\u02bc]?я)", "name"),
    (r"(?:first[_ ]?name|имя|ім['\u02bc]?я)", "name"),
    (r"(?:last[_ ]?name|фамилия|прізвище)", "name"),  # partial match
    # Email
    (r"(?:e[_-]?mail|почта|пошта|електронна)", "email"),
    # Phone
    (r"(?:phone|tel|телефон)", "phone"),
    # City
    (r"(?:city|город|місто)", "city"),
    # Address
    (r"(?:address|адрес|адреса)", "address"),
    # Country
    (r"(?:country|страна|країна)", "country"),
    # Zip / postal code
    (r"(?:zip|postal|індекс|индекс)", "zip"),
]


def _load_profile() -> dict[str, Any]:
    """Load the user profile and return a flat dict of fillable fields."""
    try:
        from jarvis.memory.user_profile import UserProfile

        import os
        if os.name == "nt":
            appdata = os.environ.get("APPDATA")
            if appdata:
                profile_path = Path(appdata) / "Jarvis" / "user_profile.json"
            else:
                profile_path = Path.home() / ".jarvis" / "user_profile.json"
        else:
            profile_path = Path.home() / ".jarvis" / "user_profile.json"

        profile = UserProfile(path=profile_path)
        data: dict[str, Any] = {}
        for key in ("name", "email", "phone", "city", "address", "country", "zip"):
            val = profile.get(key)
            if val:
                data[key] = val
        return data
    except Exception:
        log.debug("Could not load user profile", exc_info=True)
        return {}


def _match_field(attrs: dict[str, str], profile: dict[str, Any]) -> tuple[str, Any] | None:
    """Match a form field's attributes against the profile.

    Returns (profile_key, value) or None.
    """
    # Build a single search string from all useful attributes.
    search_text = " ".join(
        (attrs.get(k) or "") for k in ("name", "id", "placeholder", "aria-label", "label")
    ).lower()
    if not search_text.strip():
        return None
    for pattern, key in _FIELD_MAP:
        if key not in profile:
            continue
        if re.search(pattern, search_text, re.IGNORECASE):
            return key, profile[key]
    return None


def _fill_via_selenium(driver: Any) -> tuple[int, int, list[str]]:
    """Fill form fields using Selenium.

    Returns (filled_count, total_fields, list_of_filled_labels).
    """
    profile = _load_profile()
    if not profile:
        return 0, 0, []

    # Gather all input-like elements.
    elements = driver.find_elements("css selector", "input, select, textarea")
    total = len(elements)
    filled = 0
    filled_labels: list[str] = []

    for elem in elements:
        try:
            input_type = (elem.get_attribute("type") or "text").lower()
            # Skip submit, hidden, button, checkbox, radio, file.
            if input_type in ("submit", "hidden", "button", "checkbox", "radio", "file", "image"):
                continue
            attrs = {
                "name": elem.get_attribute("name") or "",
                "id": elem.get_attribute("id") or "",
                "placeholder": elem.get_attribute("placeholder") or "",
                "aria-label": elem.get_attribute("aria-label") or "",
                "label": "",
            }
            # Try to find associated <label>.
            elem_id = attrs["id"]
            if elem_id:
                try:
                    label_elem = driver.find_element(
                        "css selector", f'label[for="{elem_id}"]'
                    )
                    attrs["label"] = label_elem.text or ""
                except Exception:
                    pass

            match = _match_field(attrs, profile)
            if match is None:
                continue

            key, value = match
            elem.clear()
            elem.send_keys(str(value))
            filled += 1
            label = attrs.get("label") or attrs.get("placeholder") or attrs.get("name") or key
            filled_labels.append(f"{label} → {key}")
        except Exception:
            log.debug("Could not fill element", exc_info=True)
            continue

    return filled, total, filled_labels


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class SmartFormFillTool:
    """Auto-fill a web form using user profile data."""

    name: str = "fill_form"
    description: str = (
        "Fills a web form with data from the user profile (name, email, phone, "
        "city, address). Does NOT submit — user confirms manually. "
        "Requires Selenium (active browser tab). "
        "(RU: заполни форму, заполни форму моими данными, автозаполнение / "
        "UK: заповни форму, заповни форму моїми даними, автозаповнення)"
    )
    args_schema = EmptyArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^fill\s+(?:this\s+)?form$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^fill\s+form\s+with\s+my\s+data$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^auto[- ]?fill$",
            priority=460,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^заполни\s+форму$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^заполни\s+форму\s+моими\s+данными$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^автозаполнение$",
            priority=460,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^заповни\s+форму$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^заповни\s+форму\s+моїми\s+даними$",
            priority=460,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^автозаповнення$",
            priority=460,
            args=lambda m: {},
        ),
    )

    def __init__(self, *, browser_controller: Any = None) -> None:
        self._browser_controller = browser_controller

    async def execute(self, args: EmptyArgs) -> ToolResult:
        # Check browser availability.
        bc = self._browser_controller
        if bc is None:
            # Try importing the singleton.
            try:
                from jarvis.tools.local.browser_tabs import _get_browser
                bc = _get_browser()
            except Exception:
                pass
        if bc is None or not getattr(bc, "driver_active", False):
            return ToolResult(
                success=False,
                error=(
                    "Form fill requires an active Selenium browser session. "
                    "Open a page first."
                ),
            )
        driver = getattr(bc, "_driver", None)
        if driver is None:
            return ToolResult(
                success=False,
                error="No Selenium WebDriver available.",
            )

        try:
            filled, total, labels = await asyncio.to_thread(
                _fill_via_selenium, driver,
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"Form fill failed: {exc}")

        if filled == 0:
            return ToolResult(
                success=True,
                output=(
                    f"Found {total} form fields but could not match any to "
                    "your profile data. Check that your profile has name, "
                    "email, phone, etc."
                ),
            )

        label_list = "\n".join(f"  • {l}" for l in labels)
        return ToolResult(
            success=True,
            output=(
                f"Filled {filled}/{total} form fields, sir:\n{label_list}\n"
                "Please review and submit the form yourself."
            ),
        )
