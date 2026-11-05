"""The official achievement list of a Steam game, for Steam guides (B-P1-11).

Live: a 100%-achievement guide for Uncharted gave 221 "achievements" for the
game's 101. Its headings mark chapters, not achievements, and the model cut
description paragraphs into items. Steam publishes every game's achievement
list on an open page (no key, no login)::

    https://steamcommunity.com/stats/<appid>/achievements/?l=<language>

so for a Steam guide that list is the list of items, and the guide text is
used only for how to get each one (the "details").

Where the appid comes from: a guide page links the game's store page
("store.steampowered.com/app/<appid>") or its community hub
("steamcommunity.com/app/<appid>").

Page layout parsed here (the global achievement stats page)::

    <div class="achieveRow">
      ...
      <div class="achieveTxt"><h3>Name</h3><h5>How to get it</h5></div>
    </div>

Everything degrades to the old behaviour: no appid, a network error, or a
page that no longer has that layout gives no official list, and the items
the model extracted are kept as they are.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Any

from jarvis.memory.collections import NewItem, normalize_name

log = logging.getLogger(__name__)

_STATS_URL = "https://steamcommunity.com/stats/{appid}/achievements/"
_TIMEOUT_S = 10.0
_GUIDE_URL = re.compile(r"^https?://(?:www\.)?steamcommunity\.com/sharedfiles/filedetails", re.I)
_APP_LINK = re.compile(
    r"(?:store\.steampowered\.com|steamcommunity\.com)/app/(\d{2,10})", re.I
)
# Steam's ?l= values for the languages Jarvis speaks.
_STEAM_LANGUAGES = {"ru": "russian", "uk": "ukrainian", "en": "english"}


@dataclass(frozen=True)
class Achievement:
    name: str
    description: str = ""


def is_steam_guide(url: str) -> bool:
    return bool(_GUIDE_URL.match(url or ""))


def find_steam_appid(html: str, url: str) -> str | None:
    """The game's appid linked from a Steam guide page, or None for any
    other page. The most often linked appid wins (a guide may also link
    other games in its text)."""
    if not is_steam_guide(url) or not html:
        return None
    counts: dict[str, int] = {}
    for appid in _APP_LINK.findall(html):
        counts[appid] = counts.get(appid, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda a: counts[a])


def parse_achievements(html: str) -> list[Achievement]:
    """Achievements on a Steam global-stats page, in page order."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html, "html.parser")
    found: list[Achievement] = []
    seen: set[str] = set()
    for row in soup.select(".achieveRow"):
        text_box = row.select_one(".achieveTxt") or row
        name_tag = text_box.find("h3")
        if name_tag is None:
            continue
        name = name_tag.get_text(" ", strip=True)
        key = normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        desc_tag = text_box.find("h5")
        description = desc_tag.get_text(" ", strip=True) if desc_tag is not None else ""
        found.append(Achievement(name=name, description=description))
    return found


async def fetch_achievements(
    appid: str, lang: str = "en", *, client: Any = None
) -> list[Achievement]:
    """Download and parse the official list; [] on any failure."""
    import httpx

    params = {"l": _STEAM_LANGUAGES.get(lang, "english")}
    url = _STATS_URL.format(appid=appid)
    try:
        if client is None:
            async with httpx.AsyncClient(follow_redirects=True) as own:
                response = await own.get(url, params=params, timeout=_TIMEOUT_S)
        else:
            response = await client.get(url, params=params, timeout=_TIMEOUT_S)
        response.raise_for_status()
        achievements = parse_achievements(response.text)
    except Exception as exc:
        log.warning("[steam] achievement list for app %s unavailable: %s", appid, exc)
        return []
    log.info("[steam] app %s: %d official achievements", appid, len(achievements))
    return achievements


def merge_with_official(
    official: list[Achievement], extracted: list[NewItem]
) -> list[NewItem]:
    """One task per official achievement, in Steam's order. Its details are
    the guide's "how to get it" when the model found that achievement in
    the guide, else Steam's own description. Model "items" that are not
    achievements (chapters, description fragments) are dropped."""
    by_key: dict[str, NewItem] = {}
    for item in extracted:
        by_key.setdefault(normalize_name(item.name), item)

    merged: list[NewItem] = []
    for achievement in official:
        key = normalize_name(achievement.name)
        match = by_key.get(key) or _loose_match(key, by_key)
        details = (match.details if match is not None else "") or achievement.description
        section = match.section if match is not None else ""
        merged.append(
            NewItem(name=achievement.name, role="task", section=section, details=details[:500])
        )
    return merged


def _loose_match(key: str, by_key: dict[str, NewItem]) -> NewItem | None:
    """A guide often writes the name with a prefix or quotes ("Достижение
    «Охотник»"): accept one name contained in the other, for names long
    enough not to match by accident."""
    if len(key) < 6:
        return None
    for other, item in by_key.items():
        if len(other) >= 6 and (key in other or other in key):
            return item
    return None


def with_items(page: Any, items: list[NewItem]) -> Any:
    """``page`` (an ExtractedPage) as a checklist of ``items``."""
    return replace(page, kind="checklist", items=items)
