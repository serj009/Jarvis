"""Official Steam achievement lists for Steam guides (B-P1-11).

The stats page layout below is the one parse_achievements() expects; it
could not be fetched from the cloud session, so the live check is in the
roadmap's "Проверка дома".
"""

from __future__ import annotations

import pytest

from jarvis.memory.collections import NewItem
from jarvis.memory.steam_achievements import (
    Achievement,
    fetch_achievements,
    find_steam_appid,
    is_steam_guide,
    merge_with_official,
    parse_achievements,
)

GUIDE_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id=3012345678"

STATS_PAGE = """<html><body>
<div id="mainContents">
 <div class="achieveRow ">
  <div class="achieveImgHolder"><img src="a.jpg"></div>
  <div class="achieveTxtHolder">
   <div class="achievePercent">41.2%</div>
   <div class="achieveTxt"><h3>Охотник за сокровищами</h3><h5>Найдите 10 сокровищ</h5></div>
  </div>
 </div>
 <div class="achieveRow ">
  <div class="achieveTxtHolder">
   <div class="achieveTxt"><h3>Дельфин</h3><h5>Найдите всех дельфинов</h5></div>
  </div>
 </div>
 <div class="achieveRow ">
  <div class="achieveTxtHolder"><div class="achieveTxt"><h3>Без описания</h3></div></div>
 </div>
 <div class="achieveRow "><div class="achieveTxt"><h3>Дельфин</h3></div></div>
</div></body></html>"""


def test_appid_from_the_guides_store_link():
    html = (
        '<a href="https://store.steampowered.com/app/1659420/UNCHARTED/">Страница в магазине</a>'
        '<a href="https://steamcommunity.com/app/1659420">Центр сообщества</a>'
        '<a href="https://store.steampowered.com/app/570/">another game in the text</a>'
    )
    assert is_steam_guide(GUIDE_URL)
    assert find_steam_appid(html, GUIDE_URL) == "1659420"


def test_no_appid_for_other_pages():
    html = '<a href="https://store.steampowered.com/app/1659420/">store</a>'
    assert find_steam_appid(html, "https://recipes.example/borsch") is None
    assert find_steam_appid("<p>no links</p>", GUIDE_URL) is None


def test_parse_the_stats_page():
    assert parse_achievements(STATS_PAGE) == [
        Achievement("Охотник за сокровищами", "Найдите 10 сокровищ"),
        Achievement("Дельфин", "Найдите всех дельфинов"),
        Achievement("Без описания", ""),
    ]


def test_parse_a_page_without_that_layout_gives_nothing():
    assert parse_achievements("<html><body><h3>Not achievements</h3></body></html>") == []


class _Response:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class _Client:
    def __init__(self, response: _Response | Exception) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, *, params, timeout):
        self.calls.append((url, params))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.asyncio
async def test_fetch_asks_in_the_guides_language():
    client = _Client(_Response(STATS_PAGE))
    found = await fetch_achievements("1659420", "ru", client=client)
    assert [a.name for a in found] == ["Охотник за сокровищами", "Дельфин", "Без описания"]
    assert client.calls == [
        ("https://steamcommunity.com/stats/1659420/achievements/", {"l": "russian"})
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [_Response("", 503), ConnectionError("offline")])
async def test_fetch_failure_gives_no_list(response):
    assert await fetch_achievements("1659420", "uk", client=_Client(response)) == []


def test_merge_keeps_official_items_with_the_guides_details():
    official = [
        Achievement("Охотник за сокровищами", "Найдите 10 сокровищ"),
        Achievement("Дельфин", "Найдите всех дельфинов"),
        Achievement("Без описания", ""),
    ]
    extracted = [
        NewItem("4 глава", role="task"),  # a chapter heading, not an achievement
        NewItem("Достижение «Охотник за сокровищами»", role="task",
                section="Глава 2", details="сокровища у водопада и в храме"),
        NewItem("Дельфин", role="task", details=""),
    ]
    merged = merge_with_official(official, extracted)
    assert [(i.name, i.details, i.section) for i in merged] == [
        ("Охотник за сокровищами", "сокровища у водопада и в храме", "Глава 2"),
        ("Дельфин", "Найдите всех дельфинов", ""),
        ("Без описания", "", ""),
    ]
    assert {i.role for i in merged} == {"task"}
