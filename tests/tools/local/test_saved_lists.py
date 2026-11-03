"""saved_list_status / mark_list_item: using pages saved as collections."""

from __future__ import annotations

import re
from typing import Any

import pytest

from jarvis.core.request_context import current_user_transcription
from jarvis.memory.collections import CollectionStore, NewItem
from jarvis.tools.local.saved_lists import (
    MarkListItemArgs,
    MarkListItemTool,
    NameSavedListArgs,
    NameSavedListTool,
    SavedListStatusArgs,
    SavedListStatusTool,
    format_number,
)
from tests._typing import output_text


@pytest.fixture
def store(tmp_path) -> CollectionStore:
    return CollectionStore(tmp_path / "collections.db")


@pytest.fixture(autouse=True)
def _russian_request():
    """Replies follow the request's language; speak Russian here."""
    token = current_user_transcription.set("что осталось в блич")
    yield
    current_user_transcription.reset(token)


def _guide(store: CollectionStore) -> None:
    store.save(
        kind="checklist",
        title="Bleach: Rebirth of Souls",
        source_url="https://steam.example/bleach",
        items=[
            NewItem(name="Крылья феникса", role="task", details="купить 10 видов"),
            NewItem(name="Крылья тигра", role="task", details="купить 10 видов"),
            NewItem(name="Король душ", role="task", details="все реальные моменты"),
            NewItem(name="Выполнить все достижения", role="task"),
        ],
    )


def _recipe(store: CollectionStore, servings: float | None = 4) -> None:
    store.save(
        kind="recipe",
        title="Борщ",
        servings=servings,
        source_url="https://food.example/borsch",
        items=[
            NewItem(name="Свёкла", role="ingredient", quantity=2, unit="шт"),
            NewItem(name="Говядина", role="ingredient", quantity=0.5, unit="кг"),
            NewItem(name="Соль", role="ingredient"),
            NewItem(name="Сварить бульон", role="step"),
        ],
    )


async def _status(store, **kw) -> str:
    result = await SavedListStatusTool(store=store).execute(SavedListStatusArgs(**kw))
    assert result.success, result.error
    return output_text(result)


async def _mark(store, **kw):
    return await MarkListItemTool(store=store).execute(MarkListItemArgs(**kw))


class TestStatus:
    @pytest.mark.asyncio
    async def test_nothing_saved(self, store):
        assert "Сохранённых списков пока нет" in await _status(store)

    @pytest.mark.asyncio
    async def test_remaining_lists_open_items_with_details(self, store):
        _guide(store)
        text = await _status(store, action="remaining")
        assert "осталось 4 из 4" in text
        assert "Крылья феникса (купить 10 видов)" in text

    @pytest.mark.asyncio
    async def test_progress_counts_exactly(self, store):
        _guide(store)
        await _mark(store, item="король душ")
        text = await _status(store, action="progress", name="блич")
        assert "выполнено 1 из 4 (25%)" in text

    @pytest.mark.asyncio
    async def test_all_done(self, store):
        _guide(store)
        for name in ("крылья феникса", "крылья тигра", "король душ", "выполнить все достижения"):
            await _mark(store, item=name)
        assert "всё выполнено" in await _status(store, action="remaining")

    @pytest.mark.asyncio
    async def test_long_list_is_shortened(self, store):
        store.save(
            kind="checklist", title="Big",
            items=[NewItem(name=f"Пункт {i}", role="task") for i in range(25)],
        )
        text = await _status(store)
        assert "Пункт 9" in text
        assert "Пункт 10" not in text
        assert "И ещё 15" in text

    @pytest.mark.asyncio
    async def test_remaining_prefers_checklist_over_newer_recipe(self, store):
        _guide(store)
        _recipe(store)
        assert "Bleach" in await _status(store, action="remaining")

    @pytest.mark.asyncio
    async def test_unknown_name_lists_what_exists(self, store):
        _guide(store)
        _recipe(store)
        text = await _status(store, action="remaining", name="элден ринг")
        assert "Не нашёл" in text
        assert "Bleach" in text and "Борщ" in text

    @pytest.mark.asyncio
    async def test_unknown_name_with_single_list_uses_it(self, store):
        _guide(store)
        assert "Bleach" in await _status(store, action="remaining", name="игра")

    @pytest.mark.asyncio
    async def test_list_all(self, store):
        _guide(store)
        _recipe(store)
        text = await _status(store, action="list_all")
        assert "Bleach: Rebirth of Souls (чек-лист, 0/4)" in text
        assert "Борщ (рецепт)" in text


class TestShoppingList:
    @pytest.mark.asyncio
    async def test_shopping_list(self, store):
        _recipe(store)
        text = await _status(store, action="shopping_list")
        assert "Список продуктов для «Борщ» на 4 порц." in text
        assert "Свёкла — 2 шт" in text
        assert "Говядина — 0.5 кг" in text
        assert "Соль" in text
        assert "Сварить бульон" not in text

    @pytest.mark.asyncio
    async def test_scaled(self, store):
        _recipe(store)
        text = await _status(store, action="shopping_list", servings=6)
        assert "на 6 порц." in text
        assert "Свёкла — 3 шт" in text
        assert "Говядина — 0.8 кг" in text

    @pytest.mark.asyncio
    async def test_cannot_scale_without_servings(self, store):
        _recipe(store, servings=None)
        text = await _status(store, action="shopping_list", servings=6)
        assert "не указано число порций" in text
        assert "Свёкла — 2 шт" in text

    @pytest.mark.asyncio
    async def test_no_recipe(self, store):
        _guide(store)
        assert "рецепта нет" in await _status(store, action="shopping_list")

    @pytest.mark.asyncio
    async def test_remaining_on_a_recipe_explains(self, store):
        _recipe(store)
        assert "это рецепт" in await _status(store, action="remaining", name="борщ")


class TestMark:
    @pytest.mark.asyncio
    async def test_marks_exact_item(self, store):
        _guide(store)
        result = await _mark(store, item="крылья феникса")
        assert result.success
        assert "Отметил «Крылья феникса»" in output_text(result)
        assert "1 из 4" in output_text(result)

    @pytest.mark.asyncio
    async def test_ambiguous_asks(self, store):
        _guide(store)
        result = await _mark(store, item="крылья")
        assert not result.success
        assert "Уточните" in (result.error or "")
        assert "Крылья феникса" in (result.error or "") and "Крылья тигра" in (result.error or "")

    @pytest.mark.asyncio
    async def test_nothing_similar(self, store):
        _guide(store)
        result = await _mark(store, item="драконий меч")
        assert not result.success
        assert "нет пункта" in (result.error or "")

    @pytest.mark.asyncio
    async def test_already_done(self, store):
        _guide(store)
        await _mark(store, item="король душ")
        result = await _mark(store, item="король душ")
        assert result.success
        assert "уже отмечено" in output_text(result)

    @pytest.mark.asyncio
    async def test_untick(self, store):
        _guide(store)
        await _mark(store, item="король душ")
        result = await _mark(store, item="король душ", done=False)
        assert result.success
        assert "Снял отметку" in output_text(result)

    @pytest.mark.asyncio
    async def test_recipe_cannot_be_ticked(self, store):
        _recipe(store)
        result = await _mark(store, item="свёкла")
        assert not result.success

    @pytest.mark.asyncio
    async def test_empty_item(self, store):
        _guide(store)
        result = await _mark(store, item="  ")
        assert not result.success


class TestLanguage:
    @pytest.mark.asyncio
    async def test_ukrainian(self, store):
        _guide(store)
        token = current_user_transcription.set("що залишилось у бліч")
        try:
            text = await _status(store, action="remaining")
        finally:
            current_user_transcription.reset(token)
        assert "залишилось 4 з 4" in text

    @pytest.mark.asyncio
    async def test_english(self, store):
        _guide(store)
        token = current_user_transcription.set("what's left in bleach")
        try:
            text = await _status(store, action="progress")
        finally:
            current_user_transcription.reset(token)
        assert "0 of 4 done (0%)" in text


@pytest.mark.parametrize(
    ("value", "text"), [(2.0, "2"), (0.5, "0.5"), (1.3333, "1.3"), (0.04, "0")]
)
def test_format_number(value, text):
    assert format_number(value) == text


# -- voice patterns ---------------------------------------------------------------


def _match(tool, utterance: str) -> dict | None:
    for vp in tool.voice_patterns:
        m = re.match(vp.regex, utterance)
        if m:
            return vp.args(m)
    return None


@pytest.mark.parametrize(("utterance", "expected"), [
    ("что осталось", {"action": "remaining", "name": ""}),
    ("что ещё осталось в блич", {"action": "remaining", "name": "блич"}),
    ("что мне осталось пройти в блич", {"action": "remaining", "name": "блич"}),
    ("какие достижения ещё остались", {"action": "remaining", "name": ""}),
    ("сколько процентов пройдено", {"action": "progress", "name": ""}),
    ("какой прогресс в блич", {"action": "progress", "name": "блич"}),
    ("список продуктов", {"action": "shopping_list", "name": ""}),
    ("список продуктов для борща", {"action": "shopping_list", "name": "борща"}),
    ("пересчитай борщ на 6 порций",
     {"action": "shopping_list", "name": "борщ", "servings": 6.0}),
    ("список продуктов на 4 порции", {"action": "shopping_list", "name": "", "servings": 4.0}),
    ("какие списки я сохранил", {"action": "list_all"}),
    ("що залишилось", {"action": "remaining", "name": ""}),
    ("скільки пройдено", {"action": "progress", "name": ""}),
    ("список продуктів", {"action": "shopping_list", "name": ""}),
    ("what's left", {"action": "remaining", "name": ""}),
    ("what's left in bleach", {"action": "remaining", "name": "bleach"}),
    ("what's my progress", {"action": "progress", "name": ""}),
    ("shopping list for borscht", {"action": "shopping_list", "name": "borscht"}),
    ("scale the recipe to 6 servings", {"action": "shopping_list", "name": "", "servings": 6.0}),
    ("покажи весь список", {"action": "show", "name": ""}),
    ("покажи список достижений в блич", {"action": "show", "name": "блич"}),
    ("покажи все достижения", {"action": "show", "name": ""}),
    ("покажи рецепт", {"action": "show_recipe", "name": ""}),
    ("покажи рецепт борща", {"action": "show_recipe", "name": "борща"}),
    ("покажи весь список досягнень", {"action": "show", "name": ""}),
    ("show the whole list", {"action": "show", "name": ""}),
    ("show me the recipe", {"action": "show_recipe", "name": ""}),
])
def test_status_patterns(utterance, expected):
    assert _match(SavedListStatusTool, utterance) == expected


@pytest.mark.parametrize("utterance", [
    "что осталось на таймере", "what's left on the timer", "сколько осталось",
])
def test_status_patterns_leave_timers_alone(utterance):
    assert _match(SavedListStatusTool, utterance) is None


@pytest.mark.parametrize(("utterance", "expected"), [
    ("отметь крылья феникса выполненным", {"item": "крылья феникса", "done": True}),
    ("отметь король душ как пройденное", {"item": "король душ", "done": True}),
    ("отметь что я получил король душ", {"item": "король душ", "done": True}),
    ("сними отметку с король душ", {"item": "король душ", "done": False}),
    ("познач крила фенікса виконаним", {"item": "крила фенікса", "done": True}),
    ("зніми позначку з король душ", {"item": "король душ", "done": False}),
    ("mark phoenix wings as done", {"item": "phoenix wings", "done": True}),
    ("unmark phoenix wings", {"item": "phoenix wings", "done": False}),
])
def test_mark_patterns(utterance, expected):
    assert _match(MarkListItemTool, utterance) == expected


class TestShow:
    """"Show everything" -- to check how the page was understood."""

    @pytest.mark.asyncio
    async def test_checklist_pairs_name_with_its_description(self, store):
        _guide(store)
        await _mark(store, item="король душ")
        text = await _status(store, action="show")
        assert "выполнено 1 из 4" in text
        assert "Крылья феникса (купить 10 видов)" in text
        assert "Король душ (все реальные моменты) — выполнено" in text
        assert "Выполнить все достижения" in text

    @pytest.mark.asyncio
    async def test_show_reads_far_more_than_remaining(self, store):
        store.save(
            kind="checklist", title="Big",
            items=[NewItem(name=f"Пункт {i}", role="task") for i in range(70)],
        )
        text = await _status(store, action="show")
        assert "Пункт 59" in text
        assert "Пункт 60" not in text
        assert "И ещё 10" in text

    @pytest.mark.asyncio
    async def test_recipe_separates_ingredients_and_steps(self, store):
        store.save(
            kind="recipe", title="Борщ", servings=4,
            items=[
                NewItem(name="Свёкла", role="ingredient", quantity=2, unit="шт"),
                NewItem(name="Сварить бульон", role="step"),
                NewItem(name="Говядина", role="ingredient", quantity=0.5, unit="кг"),
                NewItem(name="Добавить свёклу", role="step"),
            ],
        )
        text = await _status(store, action="show_recipe")
        shopping, _, method = text.partition("Приготовление, 2 шагов:")
        assert "Свёкла — 2 шт" in shopping and "Говядина — 0.5 кг" in shopping
        assert "Сварить бульон" not in shopping
        assert "1. Сварить бульон" in method and "2. Добавить свёклу" in method
        assert "Свёкла — 2 шт" not in method

    @pytest.mark.asyncio
    async def test_show_recipe_scaled(self, store):
        _recipe(store)
        text = await _status(store, action="show_recipe", servings=8)
        assert "Свёкла — 4 шт" in text
        assert "1. Сварить бульон" in text

    @pytest.mark.asyncio
    async def test_show_recipe_prefers_recipe_over_newer_checklist(self, store):
        _recipe(store)
        _guide(store)
        assert "Борщ" in await _status(store, action="show_recipe")

    @pytest.mark.asyncio
    async def test_show_on_recipe_lists_steps(self, store):
        _recipe(store)
        assert "1. Сварить бульон" in await _status(store, action="show", name="борщ")

    @pytest.mark.asyncio
    async def test_recipe_without_steps(self, store):
        store.save(kind="recipe", title="Салат",
                   items=[NewItem(name="Огурец", role="ingredient", quantity=1, unit="шт")])
        text = await _status(store, action="show_recipe")
        assert "Огурец — 1 шт" in text
        assert "Шаги приготовления на странице не найдены" in text

    @pytest.mark.asyncio
    async def test_show_recipe_without_recipe(self, store):
        _guide(store)
        assert "рецепта нет" in await _status(store, action="show_recipe")


# -- which list, when there are many ----------------------------------------------


def _many(store: CollectionStore) -> None:
    store.save(
        kind="checklist", title="Bleach: Rebirth of Souls - achievements",
        source_url="https://steam.example/bleach",
        items=[NewItem(name="Король душ", role="task")],
    )
    store.save(
        kind="checklist", title="Elden Ring - all bosses",
        source_url="https://wiki.example/elden",
        items=[NewItem(name="Маления", role="task")],
    )
    _recipe(store)  # most recent


def _tool(store, *, game=None, page=None, cls: Any = SavedListStatusTool) -> Any:
    """A saved-list tool of *cls* with a fixed running game / open page."""
    return cls(store=store, game_title=(lambda: game), page_url=(lambda: page))


async def _status_with(tool, **kw) -> str:
    result = await tool.execute(SavedListStatusArgs(**kw))
    assert result.success, result.error
    return output_text(result)


class TestWhichList:
    @pytest.mark.asyncio
    async def test_running_game_wins(self, store):
        _many(store)
        tool = _tool(store, game="BLEACH Rebirth of Souls", page="https://wiki.example/elden")
        assert "Bleach" in await _status_with(tool)

    @pytest.mark.asyncio
    async def test_unrelated_game_is_ignored(self, store):
        _many(store)
        tool = _tool(store, game="Cyberpunk 2077", page="https://wiki.example/elden")
        assert "Elden Ring" in await _status_with(tool)

    @pytest.mark.asyncio
    async def test_open_page_beats_last_used(self, store):
        _many(store)
        tool = _tool(store, page="https://wiki.example/elden#bosses")
        assert "Elden Ring" in await _status_with(tool)

    @pytest.mark.asyncio
    async def test_open_page_that_is_not_saved_falls_back(self, store):
        _many(store)
        tool = _tool(store, page="https://news.example/today")
        # last used checklist (the recipe is newer but not a checklist)
        assert "Elden Ring" in await _status_with(tool)

    @pytest.mark.asyncio
    async def test_named_list_beats_context(self, store):
        _many(store)
        tool = _tool(store, game="BLEACH Rebirth of Souls")
        assert "Elden Ring" in await _status_with(tool, name="элден ринг")

    @pytest.mark.asyncio
    async def test_context_errors_are_ignored(self, store):
        _many(store)

        def boom() -> str:
            raise RuntimeError("uia")

        tool = SavedListStatusTool(store=store, game_title=boom, page_url=boom)
        assert "Elden Ring" in await _status_with(tool)

    @pytest.mark.asyncio
    async def test_viewing_makes_a_list_current(self, store):
        _many(store)
        tool = _tool(store)
        await _status_with(tool, action="show", name="блич")
        assert "Bleach" in await _status_with(tool)  # no name now

    @pytest.mark.asyncio
    async def test_similar_names_ask(self, store):
        store.save(kind="checklist", title="Bleach Rebirth of Souls",
                   source_url="u1", items=[NewItem(name="A", role="task")])
        store.save(kind="checklist", title="Bleach Brave Souls",
                   source_url="u2", items=[NewItem(name="B", role="task")])
        text = await _status_with(_tool(store), name="блич")
        assert text.startswith("Какой список:")
        assert "«Bleach Rebirth of Souls»" in text and "«Bleach Brave Souls»" in text

    @pytest.mark.asyncio
    async def test_set_game_title_source_later(self, store):
        _many(store)
        tool = SavedListStatusTool(store=store)
        tool.set_game_title_source(lambda: "ELDEN RING")
        assert "Elden Ring" in await _status_with(tool)

    @pytest.mark.asyncio
    async def test_mark_uses_the_game_list(self, store):
        _many(store)
        tool = _tool(store, game="BLEACH Rebirth of Souls", cls=MarkListItemTool)
        result = await tool.execute(MarkListItemArgs(item="король душ"))
        assert result.success, result.error
        assert "Bleach" in output_text(result)


class TestNaming:
    @pytest.mark.asyncio
    async def test_name_current_list_and_use_it(self, store):
        _many(store)
        namer = _tool(store, game="BLEACH Rebirth of Souls", cls=NameSavedListTool)
        result = await namer.execute(NameSavedListArgs(new_name="«мой блич»"))
        assert result.success, result.error
        assert "теперь можно называть «мой блич»" in output_text(result)
        text = await _status_with(_tool(store), action="progress", name="мой блич")
        assert text.startswith("«мой блич»")

    @pytest.mark.asyncio
    async def test_rename_named_list(self, store):
        _many(store)
        namer = _tool(store, cls=NameSavedListTool)
        result = await namer.execute(NameSavedListArgs(name="элден ринг", new_name="боссы"))
        assert result.success, result.error
        assert "Elden Ring" in output_text(result)
        assert "боссы — Elden Ring" in await _status_with(_tool(store), action="list_all")

    @pytest.mark.asyncio
    async def test_empty_name_rejected(self, store):
        _many(store)
        result = await _tool(store, cls=NameSavedListTool).execute(NameSavedListArgs(new_name=" "))
        assert not result.success

    @pytest.mark.asyncio
    async def test_nothing_saved(self, store):
        result = await _tool(store, cls=NameSavedListTool).execute(
            NameSavedListArgs(new_name="x")
        )
        assert not result.success


@pytest.mark.parametrize(("utterance", "expected"), [
    ("назови этот список блич", {"new_name": "блич"}),
    ("назови список блич", {"new_name": "блич"}),
    ("переименуй этот список в блич", {"new_name": "блич"}),
    ("переименуй гайд в блич", {"new_name": "блич"}),
    ("переименуй список блич в бличик", {"name": "блич", "new_name": "бличик"}),
    ("назви цей список бліч", {"new_name": "бліч"}),
    ("перейменуй цей список на бліч", {"new_name": "бліч"}),
    ("call this list bleach", {"new_name": "bleach"}),
    ("rename this list to bleach", {"new_name": "bleach"}),
    ("rename the list bleach to bl", {"name": "bleach", "new_name": "bl"}),
])
def test_naming_patterns(utterance, expected):
    assert _match(NameSavedListTool, utterance) == expected
