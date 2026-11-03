"""jarvis.memory.page_extractor: page text -> checklist / recipe / list.

The LLM is faked; what is tested is everything around it: chunking, the
request shape (own system prompt, JSON schema, low temperature, enough
num_predict -- the exact things that broke on a real Steam guide), parsing,
salvage of cut-off replies and merging of parts.
"""

from __future__ import annotations

import json

import pytest

from jarvis.memory.page_extractor import (
    NUM_PREDICT,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    PageExtractor,
    is_junk_name,
    merge_parts,
    parse_reply,
    salvage_truncated_json,
    split_into_chunks,
)


class FakeChunk:
    def __init__(self, content: str, done: bool = False) -> None:
        self.content = content
        self.done = done


class FakeLLM:
    """Records requests; answers each call with the next reply."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def stream_chat(self, messages, *, tools=None, format=None, options=None):  # noqa: A002
        self.calls.append({"messages": messages, "format": format, "options": options})
        reply = self.replies.pop(0) if self.replies else "{}"
        yield FakeChunk(reply[: len(reply) // 2])
        yield FakeChunk(reply[len(reply) // 2 :], done=True)


class PlainLLM:
    """An LLM wrapper without format/options (e.g. the API provider)."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.messages = None

    async def stream_chat(self, messages, *, tools=None):
        self.messages = messages
        yield FakeChunk(self.reply, done=True)


def _reply(kind: str, title: str, items: list[dict], servings=None) -> str:
    return json.dumps(
        {"kind": kind, "title": title, "servings": servings, "items": items},
        ensure_ascii=False,
    )


# -- chunking ---------------------------------------------------------------------


class TestSplit:
    def test_short_text_is_one_part(self):
        assert split_into_chunks("a\nb\nc") == ["a\nb\nc"]

    def test_empty(self):
        assert split_into_chunks("") == []
        assert split_into_chunks("\n\n  \n") == []

    def test_parts_respect_size_and_overlap(self):
        lines = [f"line {i:03d} " + "x" * 40 for i in range(100)]
        parts = split_into_chunks("\n".join(lines), chunk_chars=500, overlap_lines=2)
        assert len(parts) > 1
        assert all(len(p) <= 500 + 60 for p in parts)
        # each later part starts with the last two lines of the previous one
        for prev, nxt in zip(parts, parts[1:], strict=False):
            assert nxt.split("\n")[:2] == prev.split("\n")[-2:]
        # nothing is lost
        joined = "\n".join(parts)
        assert all(line in joined for line in lines)

    def test_no_trailing_part_of_only_overlap(self):
        lines = [f"l{i} " + "y" * 45 for i in range(20)]
        parts = split_into_chunks("\n".join(lines), chunk_chars=250, overlap_lines=2)
        last_lines = parts[-1].split("\n")
        assert last_lines[-1] == lines[-1]
        assert len(last_lines) > 2 or len(parts) == 1

    def test_repeated_menu_lines_are_dropped(self):
        text = "Главная\nГлавная\n\nМагазин\nМагазин\nДостижение"
        assert split_into_chunks(text) == ["Главная\nМагазин\nДостижение"]

    def test_max_chars(self):
        parts = split_into_chunks(
            "z" * 200 + "\n" + "w" * 200, chunk_chars=100, max_chars=150, overlap_lines=0
        )
        assert sum(len(p) for p in parts) == 150
        assert "w" not in "".join(parts)


# -- parsing ----------------------------------------------------------------------


class TestParse:
    def test_plain_json(self):
        assert parse_reply('{"kind": "list", "title": "x", "items": []}') == {
            "kind": "list", "title": "x", "items": [],
        }

    def test_think_and_fence(self):
        data = parse_reply(
            '<think>hm</think>\n```json\n{"kind":"list","title":"x","items":[]}\n```'
        )
        assert data == {"kind": "list", "title": "x", "items": []}

    def test_salvage_cut_off_reply(self):
        text = (
            '{"kind":"checklist","title":"Bleach","servings":null,"items":['
            '{"r":"task","n":"Крылья феникса","d":"купить {10} видов"},'
            '{"r":"task","n":"Панцирь черепахи","d":"также пок'
        )
        data = salvage_truncated_json(text)
        assert data is not None
        assert [i["n"] for i in data["items"]] == ["Крылья феникса"]

    def test_salvage_with_no_complete_item(self):
        data = salvage_truncated_json('{"kind":"checklist","title":"B","items":[{"r":"ta')
        assert data == {"kind": "checklist", "title": "B", "items": []}

    def test_garbage(self):
        assert parse_reply("not json at all") is None


# -- merging ----------------------------------------------------------------------


class TestMerge:
    def test_dedupes_across_parts_and_keeps_longer_details(self):
        page = merge_parts([
            {"kind": "checklist", "title": "Bleach", "items": [
                {"r": "task", "n": "Крылья феникса", "d": ""},
            ]},
            {"kind": "checklist", "title": "", "items": [
                {"r": "task", "n": "крылья феникса", "d": "купить 10 видов"},
                {"r": "task", "n": "Король душ"},
            ]},
        ])
        assert page is not None
        assert page.kind == "checklist"
        assert page.title == "Bleach"
        assert [(i.name, i.details) for i in page.items] == [
            ("Крылья феникса", "купить 10 видов"), ("Король душ", ""),
        ]

    def test_recipe_roles_and_quantities(self):
        page = merge_parts([{"kind": "recipe", "title": "Борщ", "servings": 4, "items": [
            {"r": "ingredient", "n": "Свёкла", "q": "1/2", "u": "кг"},
            {"r": "ingredient", "n": "Соль", "q": None, "u": ""},
            {"r": "task", "n": "Сварить бульон"},
        ]}])
        assert page is not None
        assert page.servings == 4
        roles = [(i.role, i.name, i.quantity, i.unit) for i in page.items]
        assert roles == [
            ("ingredient", "Свёкла", 0.5, "кг"),
            ("ingredient", "Соль", None, ""),
            ("step", "Сварить бульон", None, ""),
        ]

    def test_checklist_items_become_tasks(self):
        page = merge_parts(
            [{"kind": "checklist", "title": "t", "items": [{"r": "item", "n": "A"}]}]
        )
        assert page is not None
        assert page.items[0].role == "task"

    def test_long_key_names_accepted(self):
        page = merge_parts([{"kind": "list", "title": "t", "items": [
            {"role": "item", "name": "A", "details": "d"},
        ]}])
        assert page is not None
        assert (page.items[0].name, page.items[0].details) == ("A", "d")

    def test_article_with_no_items_is_not_a_collection(self):
        page = merge_parts([{"kind": "article", "title": "News", "items": []}])
        assert page is not None
        assert not page.is_collection

    def test_article_vote_loses_to_a_part_with_items(self):
        page = merge_parts([
            {"kind": "article", "title": "Guide", "items": []},
            {"kind": "article", "title": "", "items": []},
            {"kind": "checklist", "title": "", "items": [{"r": "task", "n": "A"}]},
        ])
        assert page is not None
        assert page.kind == "checklist"
        assert page.is_collection

    def test_nothing(self):
        assert merge_parts([]) is None


# -- the extractor ----------------------------------------------------------------


class TestExtractor:
    @pytest.mark.asyncio
    async def test_request_shape(self):
        llm = FakeLLM([_reply("checklist", "Bleach", [{"r": "task", "n": "A"}])])
        page = await PageExtractor(llm).extract("Achievement A\nget it", "https://x", "Steam")
        assert page is not None
        call = llm.calls[0]
        assert call["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
        assert "Jarvis" not in SYSTEM_PROMPT  # no chat persona in extraction
        assert call["format"] == RESPONSE_SCHEMA
        assert call["options"]["num_predict"] == NUM_PREDICT >= 4096
        assert call["options"]["temperature"] <= 0.2
        assert "Part 1 of 1" in call["messages"][1]["content"]

    @pytest.mark.asyncio
    async def test_every_part_is_sent_and_merged(self):
        text = "\n".join(f"Достижение {i} " + "z" * 60 for i in range(60))
        llm = FakeLLM([
            _reply("checklist", "Bleach", [{"r": "task", "n": "Достижение 1"}]),
            _reply("checklist", "", [{"r": "task", "n": "Достижение 30"}]),
            _reply("checklist", "", [{"r": "task", "n": "Достижение 59"}]),
        ])
        page = await PageExtractor(llm, chunk_chars=1500).extract(text, "u", "t")
        assert len(llm.calls) >= 3
        assert page is not None
        assert {i.name for i in page.items} >= {"Достижение 1", "Достижение 30", "Достижение 59"}

    @pytest.mark.asyncio
    async def test_bad_part_is_skipped_not_fatal(self):
        text = "\n".join(f"row {i} " + "q" * 60 for i in range(40))
        llm = FakeLLM(["garbage", _reply("list", "L", [{"r": "item", "n": "row 30"}])])
        page = await PageExtractor(llm, chunk_chars=1500).extract(text, "u", "t")
        assert page is not None
        assert [i.name for i in page.items] == ["row 30"]

    @pytest.mark.asyncio
    async def test_llm_without_format_support(self):
        llm = PlainLLM(_reply("list", "L", [{"r": "item", "n": "A"}]))
        page = await PageExtractor(llm).extract("A", "u", "t")
        assert page is not None
        assert page.items[0].name == "A"
        assert llm.messages is not None
        assert llm.messages[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_llm_error_gives_none(self):
        class Broken:
            async def stream_chat(self, messages, **kwargs):
                raise ConnectionError("ollama down")
                yield  # pragma: no cover

        assert await PageExtractor(Broken()).extract("A", "u", "t") is None


# -- junk items -------------------------------------------------------------------


class TestJunk:
    @pytest.mark.parametrize("name", [
        "Наградить", "В избранное", "в избранном", "Поделиться", "1", "632", "|",
        "Оглавление руководства", "Описание", "Ингредиенты", "Share", "Like", "Поділитися",
        "русскоязычный дискорд сервер по игре", "Our Discord", "Наш телеграм-канал",
    ])
    def test_ui_words_and_counters(self, name):
        assert is_junk_name(name)

    @pytest.mark.parametrize("name", [
        "Крылья феникса", "Король душ", "Выполнить все достижения", "Свёкла", "Соль",
        "Пройти обучение", "Like a Dragon",
    ])
    def test_real_items_kept(self, name):
        assert not is_junk_name(name)

    def test_page_title_and_tab_title(self):
        tab = "Сообщество Steam :: Руководство :: Все достижения и как их получить (100%)"
        assert is_junk_name("Все достижения и как их получить (100%)", (tab,))
        assert is_junk_name("Bleach - achievements", ("Bleach - achievements",))
        # short names are not dropped just because the title contains them
        assert not is_junk_name("Steam", (tab,))

    def test_merge_drops_junk(self):
        page = merge_parts(
            [{"kind": "checklist", "title": "Bleach", "items": [
                {"r": "task", "n": "Наградить"},
                {"r": "task", "n": "Крылья феникса", "d": "купить 10 видов"},
                {"r": "task", "n": "632"},
                {"r": "task", "n": "Все достижения и как их получить (100%)"},
            ]}],
            page_title="Steam :: Руководство :: Все достижения и как их получить (100%)",
        )
        assert page is not None
        assert [i.name for i in page.items] == ["Крылья феникса"]


def test_prompt_explains_layout_and_exclusions():
    assert '"## "' in SYSTEM_PROMPT
    assert "description" in SYSTEM_PROMPT
    for excluded in ("table of contents", "Наградить", "Поделиться", "counters"):
        assert excluded in SYSTEM_PROMPT
