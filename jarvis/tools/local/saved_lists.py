"""Working with saved pages: "what's left", "mark it done", shopping lists.

save_page_knowledge turns a page into a collection (jarvis.memory.collections):
a checklist (game achievements for 100%), a recipe (ingredients + steps) or a
plain list. These two tools are how the user uses it afterwards:

* ``saved_list_status`` (READ) -- what is left, progress in percent, the
  whole list, the shopping list (optionally scaled to N servings), or which
  lists exist.
* ``mark_list_item`` (WRITE) -- tick / untick one item by its spoken name.

Two tools rather than one per question: every extra schema costs prompt
space and makes the 8B model's tool choice worse (see tool_filter), and
the questions differ only in an ``action`` argument.

Replies are built here, in the request's language, not left to the LLM: the
counting ("12 of 46, 26%") must be exact, which an 8B model is not.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.core.phrases import reply_language
from jarvis.memory.collections import Collection, CollectionStore, Item
from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

MAX_SPOKEN_ITEMS = 10
# "Show the whole list" was asked for explicitly, so it reads much more --
# but still bounded, a 300-row page should not keep TTS busy for half an hour.
MAX_SHOWN_ITEMS = 60

# -- phrases (ru / uk / en) -----------------------------------------------------

_T: dict[str, dict[str, str]] = {
    "none_saved": {
        "ru": "Сохранённых списков пока нет. Откройте страницу и скажите «запомни эту страницу».",
        "uk": "Збережених списків поки немає. Відкрийте сторінку і скажіть "
              "«запам'ятай цю сторінку».",
        "en": "No saved lists yet. Open a page and say \"remember this page\".",
    },
    "not_found": {
        "ru": "Не нашёл сохранённый список «{name}». Есть: {titles}.",
        "uk": "Не знайшов збережений список «{name}». Є: {titles}.",
        "en": "No saved list called \"{name}\". Saved: {titles}.",
    },
    "progress": {
        "ru": "«{title}»: выполнено {done} из {total} ({pct}%).",
        "uk": "«{title}»: виконано {done} з {total} ({pct}%).",
        "en": "\"{title}\": {done} of {total} done ({pct}%).",
    },
    "all_done": {
        "ru": "«{title}»: всё выполнено, {total} из {total}. Поздравляю, сэр.",
        "uk": "«{title}»: усе виконано, {total} з {total}. Вітаю, сер.",
        "en": "\"{title}\": everything is done, {total} of {total}. Congratulations, sir.",
    },
    "remaining_head": {
        "ru": "«{title}»: осталось {left} из {total}.",
        "uk": "«{title}»: залишилось {left} з {total}.",
        "en": "\"{title}\": {left} of {total} left.",
    },
    "and_more": {
        "ru": "И ещё {n}.",
        "uk": "І ще {n}.",
        "en": "And {n} more.",
    },
    "not_a_checklist": {
        "ru": "«{title}» — это рецепт, в нём нечего отмечать. Могу дать список продуктов.",
        "uk": "«{title}» — це рецепт, у ньому нічого відмічати. Можу дати список продуктів.",
        "en": "\"{title}\" is a recipe, there is nothing to tick off. "
              "I can give the shopping list.",
    },
    "not_a_recipe": {
        "ru": "Сохранённого рецепта нет.",
        "uk": "Збереженого рецепта немає.",
        "en": "There is no saved recipe.",
    },
    "shopping_head": {
        "ru": "Список продуктов для «{title}»{servings}:",
        "uk": "Список продуктів для «{title}»{servings}:",
        "en": "Shopping list for \"{title}\"{servings}:",
    },
    "servings": {
        "ru": " на {n} порц.",
        "uk": " на {n} порц.",
        "en": " for {n} servings",
    },
    "servings_unknown": {
        "ru": "В рецепте не указано число порций, поэтому пересчитать не могу. "
              "Вот исходные количества.",
        "uk": "У рецепті не вказано кількість порцій, тому перерахувати не можу. "
              "Ось початкові кількості.",
        "en": "The recipe does not say how many servings it makes, so I can't scale it. "
              "Here are the original amounts.",
    },
    "steps_head": {
        "ru": "Приготовление, {n} шагов:",
        "uk": "Приготування, {n} кроків:",
        "en": "Method, {n} steps:",
    },
    "no_steps": {
        "ru": "Шаги приготовления на странице не найдены.",
        "uk": "Кроки приготування на сторінці не знайдені.",
        "en": "No cooking steps were found on the page.",
    },
    "done_mark": {"ru": "выполнено", "uk": "виконано", "en": "done"},
    "lists_head": {
        "ru": "Сохранённые списки:",
        "uk": "Збережені списки:",
        "en": "Saved lists:",
    },
    "marked": {
        "ru": "Отметил «{item}» как выполненное. {progress}",
        "uk": "Позначив «{item}» як виконане. {progress}",
        "en": "Marked \"{item}\" as done. {progress}",
    },
    "unmarked": {
        "ru": "Снял отметку с «{item}». {progress}",
        "uk": "Зняв позначку з «{item}». {progress}",
        "en": "Unmarked \"{item}\". {progress}",
    },
    "already": {
        "ru": "«{item}» уже {state}.",
        "uk": "«{item}» вже {state}.",
        "en": "\"{item}\" is already {state}.",
    },
    "state_done": {"ru": "отмечено", "uk": "позначено", "en": "done"},
    "state_open": {"ru": "не отмечено", "uk": "не позначено", "en": "not done"},
    "no_item": {
        "ru": "В «{title}» нет пункта, похожего на «{item}».",
        "uk": "У «{title}» немає пункту, схожого на «{item}».",
        "en": "Nothing in \"{title}\" looks like \"{item}\".",
    },
    "ambiguous": {
        "ru": "Уточните, какой пункт: {options}?",
        "uk": "Уточніть, який пункт: {options}?",
        "en": "Which one do you mean: {options}?",
    },
    "which_list": {
        "ru": "Какой список: {options}?",
        "uk": "Який список: {options}?",
        "en": "Which list: {options}?",
    },
    "or": {"ru": " или ", "uk": " чи ", "en": " or "},
    "renamed": {
        "ru": "Готово: список «{title}» теперь можно называть «{alias}».",
        "uk": "Готово: список «{title}» тепер можна називати «{alias}».",
        "en": "Done: you can now call \"{title}\" \"{alias}\".",
    },
    "kind_checklist": {"ru": "чек-лист", "uk": "чек-лист", "en": "checklist"},
    "kind_recipe": {"ru": "рецепт", "uk": "рецепт", "en": "recipe"},
    "kind_list": {"ru": "список", "uk": "список", "en": "list"},
}


def _t(key: str, **values: Any) -> str:
    lang = reply_language()
    variants = _T[key]
    return (variants.get(lang) or variants["en"]).format(**values)


# -- formatting helpers (also used by save_page_knowledge) ----------------------


def format_number(value: float) -> str:
    """Short form for speech: 2.0 -> '2', 0.5 -> '0.5', 1.333 -> '1.3'."""
    if abs(value - round(value)) < 0.05:
        return str(round(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def format_ingredient(item: Item, factor: float = 1.0) -> str:
    if item.quantity is None:
        return item.name
    amount = format_number(item.quantity * factor)
    unit = f" {item.unit}" if item.unit else ""
    return f"{item.name} — {amount}{unit}"


def shopping_lines(collection: Collection, factor: float = 1.0) -> list[str]:
    return [format_ingredient(i, factor) for i in collection.ingredients()]


def progress_line(collection: Collection) -> str:
    total = collection.total
    done = collection.done_count
    if total and done == total:
        return _t("all_done", title=collection.label, total=total)
    pct = round(100 * done / total) if total else 0
    return _t("progress", title=collection.label, done=done, total=total, pct=pct)


def _describe_item(item: Item) -> str:
    return f"{item.name} ({item.details})" if item.details else item.name


def _spoken_list(lines: list[str], limit: int = MAX_SPOKEN_ITEMS) -> str:
    shown = lines[:limit]
    text = "; ".join(shown)
    if len(lines) > len(shown):
        text += ". " + _t("and_more", n=len(lines) - len(shown))
    return text


# -- choosing the collection ----------------------------------------------------


class ListContext:
    """Where the user is right now, for requests that name no list.

    Both sources are optional callables returning a value or None; they are
    read only when a request names no list, and any error counts as None.
    """

    def __init__(
        self,
        *,
        game_title: Callable[[], str | None] | None = None,
        page_url: Callable[[], str | None] | None = None,
    ) -> None:
        self.game_title = game_title
        self.page_url = page_url


def _safe(source: Callable[[], str | None] | None) -> str | None:
    if source is None:
        return None
    try:
        return source()
    except Exception:
        log.debug("saved lists: context source failed", exc_info=True)
        return None


def _ambiguous(scored: list[tuple[float, Collection]]) -> bool:
    """Two lists match the spoken name about equally well (and neither
    exactly): ask instead of guessing."""
    if len(scored) < 2:
        return False
    best, second = scored[0][0], scored[1][0]
    return best < 1.0 and best - second < 0.08


def _pick(
    store: CollectionStore,
    name: str,
    kinds: tuple[str, ...] | None,
    context: ListContext | None = None,
) -> tuple[Collection | None, str | None]:
    """(collection with items, error message) for the list the user means.

    With a name: the best match of alias / title / page title; two close
    matches -> ask which; no match -> the only list if there is one, else
    say what exists. Without a name, in this order:

    1. the list of the game being played (game mode's Steam title);
    2. the list of the page open in the browser (its URL is saved);
    3. the most recently used list (saved, viewed, ticked or renamed).

    *kinds* is a preference: lists of other kinds are used only when no
    list of these kinds exists.
    """
    every = store.list_all()
    if not every:
        return None, _t("none_saved")
    pool = [c for c in every if kinds is None or c.kind in kinds] or every
    pool_ids = {c.id for c in pool}

    if name.strip():
        scored = store.candidates(name, kinds=kinds) or (
            store.candidates(name) if kinds else []
        )
        if scored:
            if _ambiguous(scored):
                options = _t("or").join(f"«{c.label}»" for _s, c in scored[:3])
                return None, _t("which_list", options=options)
            return store.get(scored[0][1].id), None
        if len(every) == 1:
            return store.get(every[0].id), None
        titles = ", ".join(f"«{c.label}»" for c in every[:5])
        return None, _t("not_found", name=name.strip(), titles=titles)

    ctx = context or ListContext()
    game = _safe(ctx.game_title)
    if game:
        # Stricter than for spoken names: the game title is precise, and a
        # weak match would hijack every "what's left" while playing.
        playing = [
            c for _s, c in store.candidates(game, threshold=0.75) if c.id in pool_ids
        ]
        if playing:
            log.info("[saved_lists] using the list of the running game")
            return store.get(playing[0].id), None
    url = _safe(ctx.page_url)
    if url:
        on_page = store.find_by_url(url)
        if on_page is not None and on_page.id in pool_ids:
            log.info("[saved_lists] using the list of the page open in the browser")
            return on_page, None
    return store.get(pool[0].id), None


class SavedListToolBase:
    """Shared plumbing: the store and where-the-user-is context."""

    def __init__(
        self,
        *,
        store: CollectionStore | None = None,
        page_url: Callable[[], str | None] | None = None,
        game_title: Callable[[], str | None] | None = None,
    ) -> None:
        self._store = store
        self._context = ListContext(game_title=game_title, page_url=page_url)

    def set_game_title_source(self, source: Callable[[], str | None] | None) -> None:
        """Wire game mode in after construction: the app creates GameMode
        after the local tools are registered."""
        self._context.game_title = source

    def _get_store(self) -> CollectionStore:
        if self._store is None:
            self._store = CollectionStore()
        return self._store


# -- saved_list_status ----------------------------------------------------------


class SavedListStatusArgs(BaseModel):
    action: Literal[
        "remaining", "progress", "show", "show_recipe", "shopping_list", "list_all",
    ] = Field(
        default="remaining",
        description=(
            "remaining: what is still not done; progress: done count and percent; "
            "show: the whole checklist with each item's description and done marks; "
            "show_recipe: a saved recipe's ingredients and cooking steps; "
            "shopping_list: ingredients of a saved recipe; "
            "list_all: which lists are saved."
        ),
    )
    name: str = Field(
        default="",
        description=(
            "Which saved list (game, recipe or page name as the user said it). "
            "Empty = the most recently used one."
        ),
    )
    servings: float | None = Field(
        default=None,
        description="For shopping_list: scale the recipe to this many servings.",
    )


def _name(m: Any, group: int = 1) -> str:
    try:
        return (m.group(group) or "").strip()
    except IndexError:
        return ""


class SavedListStatusTool(SavedListToolBase):
    name: str = "saved_list_status"
    description: str = (
        "Answers questions about lists the user saved from web pages with "
        "save_page_knowledge: what is still left to do (e.g. achievements for "
        "100% in a game), progress in percent, the full list with each item's "
        "description, a saved recipe (ingredients and steps), its shopping "
        "list (optionally for N servings), or which lists are saved. "
        "(RU: что осталось, сколько пройдено, покажи весь список, покажи "
        "рецепт, список продуктов, пересчитай на 4 порции / UK: що залишилось, "
        "скільки пройдено, покажи весь список, список продуктів)"
    )
    args_schema = SavedListStatusArgs
    requires_confirmation: bool = False

    # 300-312: below take_note (440) and above every catch-all; none of these
    # phrasings overlaps another tool's (see tests/tools/test_voice_routing_
    # collisions.py). The negative look-aheads keep timer questions
    # ("what's left on the timer", "что осталось на таймере") with timers.
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # RU
        VoicePattern(
            regex=r"^что\s+(?!.*таймер)(?:мне\s+)?(?:ещ[её]\s+)?осталось"
            r"(?:\s+(?:сделать|пройти|выполнить|получить|купить))?"
            r"(?:\s+(?:в|во|по|для|из)\s+(.+?))?$",
            priority=300,
            args=lambda m: {"action": "remaining", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^какие\s+(?:достижения|ачивки|задания|пункты)\s+(?:ещ[её]\s+)?"
            r"(?:остались|не\s+(?:выполнены|получены))(?:\s+(?:в|по)\s+(.+?))?$",
            priority=301,
            args=lambda m: {"action": "remaining", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^(?:сколько\s+(?:процентов\s+)?(?:я\s+)?(?:прош[её]л|пройдено|выполнено|сделано)"
            r"|какой\s+(?:у\s+меня\s+)?прогресс)(?:\s+(?:в|во|по)\s+(.+?))?$",
            priority=302,
            args=lambda m: {"action": "progress", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^(?:пересчитай|посчитай|список\s+(?:продуктов|покупок))\s+"
            r"(?:(?:рецепт\s+)?(.+?)\s+)?на\s+(\d+(?:[.,]\d+)?)\s+(?:порци\w*|человек\w*)$",
            priority=303,
            args=lambda m: {
                "action": "shopping_list", "name": _name(m),
                "servings": float(m.group(2).replace(",", ".")),
            },
        ),
        VoicePattern(
            regex=r"^(?:дай\s+|покажи\s+|составь\s+|какой\s+)?список\s+(?:продуктов|покупок)"
            r"(?:\s+(?:для|на)\s+(.+?))?$",
            priority=304,
            args=lambda m: {"action": "shopping_list", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^(?:какие|что)\s+(?:списки|страницы|рецепты|гайды)\s+(?:я\s+)?"
            r"(?:сохранил|сохранены|сохранено|у\s+меня\s+сохранены)$",
            priority=305,
            args=lambda m: {"action": "list_all"},
        ),
        # UK
        VoicePattern(
            regex=r"^що\s+(?!.*таймер)(?:мені\s+)?(?:ще\s+)?(?:залишилось|залишилося|лишилось)"
            r"(?:\s+(?:зробити|пройти|виконати|отримати|купити))?"
            r"(?:\s+(?:в|у|по|для|з)\s+(.+?))?$",
            priority=306,
            args=lambda m: {"action": "remaining", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^(?:скільки\s+(?:відсотків\s+)?(?:я\s+)?(?:пройшов|пройдено|виконано|зроблено)"
            r"|який\s+(?:у\s+мене\s+)?прогрес)(?:\s+(?:в|у|по)\s+(.+?))?$",
            priority=307,
            args=lambda m: {"action": "progress", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^(?:дай\s+|покажи\s+|склади\s+)?список\s+(?:продуктів|покупок)"
            r"(?:\s+(?:для|на)\s+(.+?))?$",
            priority=308,
            args=lambda m: {"action": "shopping_list", "name": _name(m)},
        ),
        # EN
        VoicePattern(
            regex=r"^what(?:'s|\s+is)\s+(?!.*\btim(?:e|er)\b)(?:still\s+)?left"
            r"(?:\s+to\s+(?:do|get|complete|buy))?(?:\s+(?:in|for|on)\s+(.+?))?$",
            priority=309,
            args=lambda m: {"action": "remaining", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^(?:what(?:'s|\s+is)\s+my\s+progress|how\s+(?:much|far)\s+(?:have\s+i\s+)?"
            r"(?:done|completed|progressed))(?:\s+(?:in|on|for)\s+(.+?))?$",
            priority=310,
            args=lambda m: {"action": "progress", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^(?:show\s+|give\s+me\s+|make\s+)?(?:the\s+|a\s+)?shopping\s+list"
            r"(?:\s+for\s+(.+?))?$",
            priority=311,
            args=lambda m: {"action": "shopping_list", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^scale\s+(?:the\s+)?(?:recipe\s+)?(?:(.+?)\s+)?(?:to|for)\s+(\d+)\s+(?:servings?|people)$",
            priority=312,
            args=lambda m: {
                "action": "shopping_list", "name": _name(m), "servings": float(m.group(2)),
            },
        ),
        # "Show me everything that was saved" -- to check how the page was
        # understood (which description went with which achievement, what
        # counted as an ingredient and what as a step).
        VoicePattern(
            regex=r"^покажи\s+(?:весь\s+|полный\s+|мой\s+)?(?:список|чек-?лист)"
            r"(?:\s+(?:достижений|ачивок|заданий|пунктов))?(?:\s+(?:в|по|для|из)\s+(.+?))?$",
            priority=320,
            args=lambda m: {"action": "show", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^покажи\s+(?:все|всё)\s+(?:достижения|ачивки|задания|пункты)"
            r"(?:\s+(?:в|по|из)\s+(.+?))?$",
            priority=321,
            args=lambda m: {"action": "show", "name": _name(m)},
        ),
        VoicePattern(
            # RU and UK alike: "покажи рецепт", "покажи рецепт борща"
            regex=r"^покажи\s+(?:весь\s+|повний\s+|полный\s+)?рецепт(?:\s+(.+?))?$",
            priority=322,
            args=lambda m: {"action": "show_recipe", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^покажи\s+(?:весь\s+|повний\s+|мій\s+)?(?:список|чек-?лист)"
            r"(?:\s+(?:досягнень|завдань|пунктів))?(?:\s+(?:в|у|по|для|з)\s+(.+?))?$",
            priority=323,
            args=lambda m: {"action": "show", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^show\s+(?:me\s+)?(?:the\s+|my\s+)?(?:whole\s+|full\s+)?(?:list|checklist)"
            r"(?:\s+(?:for|of|in)\s+(.+?))?$",
            priority=324,
            args=lambda m: {"action": "show", "name": _name(m)},
        ),
        VoicePattern(
            regex=r"^show\s+(?:me\s+)?(?:the\s+)?(?:whole\s+|full\s+)?recipe"
            r"(?:\s+(?:for\s+)?(.+?))?$",
            priority=325,
            args=lambda m: {"action": "show_recipe", "name": _name(m)},
        ),
    )

    async def execute(self, args: SavedListStatusArgs) -> ToolResult:
        try:
            text = await asyncio.to_thread(self._answer, args)
        except Exception as exc:
            log.exception("saved_list_status failed")
            return ToolResult(success=False, error=f"Saved lists error: {exc}")
        return ToolResult(success=True, output=text)

    def _answer(self, args: SavedListStatusArgs) -> str:
        store = self._get_store()
        if args.action == "list_all":
            every = store.list_all()
            if not every:
                return _t("none_saved")
            lines = [
                (f"{c.alias} — {c.title}" if c.alias else c.title)
                + f" ({_t('kind_' + c.kind)}"
                + (f", {c.done_count}/{c.total}" if c.kind != "recipe" else "")
                + ")"
                for c in every
            ]
            return _t("lists_head") + " " + _spoken_list(lines)

        if args.action in ("shopping_list", "show_recipe"):
            col, err = _pick(store, args.name, ("recipe",), self._context)
            if col is None:
                return err or ""
            store.touch(col.id)
            if col.kind != "recipe":
                return _t("not_a_recipe")
            if args.action == "show_recipe":
                return self._recipe(col, args.servings)
            return self._shopping(col, args.servings)

        col, err = _pick(store, args.name, ("checklist", "list"), self._context)
        if col is None:
            return err or ""
        store.touch(col.id)
        if col.kind == "recipe":
            if args.action == "show":
                return self._recipe(col, None)
            return _t("not_a_checklist", title=col.label)
        if args.action == "progress":
            return progress_line(col)
        if args.action == "show":
            mark = _t("done_mark")
            lines = [
                _describe_item(i) + (f" — {mark}" if i.done else "") for i in col.tasks()
            ]
            return progress_line(col) + " " + _spoken_list(lines, MAX_SHOWN_ITEMS)
        # remaining
        left = col.remaining()
        if not left:
            return progress_line(col)
        head = _t("remaining_head", title=col.label, left=len(left), total=col.total)
        return head + " " + _spoken_list([_describe_item(i) for i in left])

    @classmethod
    def _recipe(cls, col: Collection, servings: float | None) -> str:
        """Ingredients (scaled if asked) and then every step, in order."""
        steps = col.steps()
        if not steps:
            return cls._shopping(col, servings) + " " + _t("no_steps")
        numbered = [f"{n}. {step.name}" for n, step in enumerate(steps, start=1)]
        return (
            cls._shopping(col, servings) + " " + _t("steps_head", n=len(steps))
            + " " + " ".join(numbered)
        )

    @staticmethod
    def _shopping(col: Collection, servings: float | None) -> str:
        factor = 1.0
        prefix = ""
        if servings:
            if col.servings:
                factor = servings / col.servings
            else:
                prefix = _t("servings_unknown") + " "
        shown_servings = servings if (servings and col.servings) else col.servings
        head = _t(
            "shopping_head", title=col.label,
            servings=_t("servings", n=format_number(shown_servings)) if shown_servings else "",
        )
        lines = shopping_lines(col, factor)
        # A shopping list is read in full: a missing ingredient is worse
        # than a long answer.
        return prefix + head + " " + "; ".join(lines) + "."


# -- mark_list_item -------------------------------------------------------------


class MarkListItemArgs(BaseModel):
    item: str = Field(description="The item to tick, as the user said it (e.g. 'Крылья феникса').")
    name: str = Field(
        default="",
        description="Which saved list. Empty = the most recently used one.",
    )
    done: bool = Field(default=True, description="False to remove the tick.")


_DONE_WORDS_RU = r"(?:выполненн\w*|сделанн\w*|пройденн\w*|полученн\w*|готов\w*)"
_DONE_WORDS_UK = r"(?:виконан\w*|зроблен\w*|пройден\w*|отриман\w*|готов\w*)"


class MarkListItemTool(SavedListToolBase):
    name: str = "mark_list_item"
    description: str = (
        "Ticks an item as done (or removes the tick) in a list the user saved "
        "from a web page: an achievement, a quest, a task. Use when the user "
        "says they got / completed something from a saved checklist. "
        "(RU: отметь <пункт> выполненным, сними отметку с <пункт> / UK: познач "
        "<пункт> виконаним)"
    )
    args_schema = MarkListItemArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # RU
        VoicePattern(
            regex=r"^(?:отметь|пометь|отмечай)\s+(.+?)\s+(?:как\s+)?" + _DONE_WORDS_RU + r"$",
            priority=313,
            args=lambda m: {"item": m.group(1), "done": True},
        ),
        VoicePattern(
            regex=r"^(?:отметь|пометь),?\s+что\s+(?:я\s+)?"
            r"(?:выполнил|получил|сделал|прош[её]л|открыл)\s+(.+)$",
            priority=314,
            args=lambda m: {"item": m.group(1), "done": True},
        ),
        VoicePattern(
            regex=r"^сними\s+(?:отметку|галочку)\s+(?:с|со)\s+(.+)$",
            priority=315,
            args=lambda m: {"item": m.group(1), "done": False},
        ),
        # UK
        VoicePattern(
            regex=r"^(?:познач|відміть|позначь)\s+(.+?)\s+(?:як\s+)?" + _DONE_WORDS_UK + r"$",
            priority=316,
            args=lambda m: {"item": m.group(1), "done": True},
        ),
        VoicePattern(
            regex=r"^зніми\s+(?:позначку|галочку)\s+(?:з|із|зі)\s+(.+)$",
            priority=317,
            args=lambda m: {"item": m.group(1), "done": False},
        ),
        # EN
        VoicePattern(
            regex=r"^(?:mark|tick)\s+(.+?)\s+(?:as\s+)?(?:done|completed|complete|finished|got)$",
            priority=318,
            args=lambda m: {"item": m.group(1), "done": True},
        ),
        VoicePattern(
            regex=r"^(?:unmark|untick|uncheck)\s+(.+)$",
            priority=319,
            args=lambda m: {"item": m.group(1), "done": False},
        ),
    )

    async def execute(self, args: MarkListItemArgs) -> ToolResult:
        item = args.item.strip()
        if not item:
            return ToolResult(success=False, error="Which item should I mark?")
        try:
            return await asyncio.to_thread(self._mark, args.name, item, args.done)
        except Exception as exc:
            log.exception("mark_list_item failed")
            return ToolResult(success=False, error=f"Saved lists error: {exc}")

    def _mark(self, name: str, query: str, done: bool) -> ToolResult:
        store = self._get_store()
        col, err = _pick(store, name, ("checklist", "list"), self._context)
        if col is None:
            return ToolResult(success=False, error=err)
        if col.kind == "recipe":
            return ToolResult(success=False, error=_t("not_a_checklist", title=col.label))

        matches = [(s, i) for s, i in store.match_items(col, query) if i.role != "ingredient"]
        if not matches:
            return ToolResult(success=False, error=_t("no_item", title=col.label, item=query))
        best_score, best = matches[0]
        runner_up = matches[1][0] if len(matches) > 1 else 0.0
        clear_winner = best_score >= 0.97 or best_score - runner_up >= 0.1
        if not clear_winner:
            options = ", ".join(f"«{i.name}»" for _s, i in matches[:4])
            return ToolResult(success=False, error=_t("ambiguous", options=options))

        if best.done == done:
            state = _t("state_done" if done else "state_open")
            return ToolResult(success=True, output=_t("already", item=best.name, state=state))
        store.set_done(col.id, [best.id], done=done)
        refreshed = store.get(col.id) or col
        key = "marked" if done else "unmarked"
        return ToolResult(
            success=True, output=_t(key, item=best.name, progress=progress_line(refreshed))
        )


# -- name_saved_list ------------------------------------------------------------


class NameSavedListArgs(BaseModel):
    new_name: str = Field(description="The name the user wants to call the list by.")
    name: str = Field(
        default="",
        description=(
            "Which saved list to name. Empty = the current one (the running "
            "game's, the open page's, or the most recently used)."
        ),
    )


def _clean_alias(text: str) -> str:
    return text.strip().strip("«»\"'").strip()


class NameSavedListTool(SavedListToolBase):
    name: str = "name_saved_list"
    description: str = (
        "Gives a saved list (checklist / recipe from a web page) the user's "
        "own short name, so it can be asked for by that name later. "
        "(RU: назови этот список блич, переименуй список X в Y / UK: назви "
        "цей список ...)"
    )
    args_schema = NameSavedListArgs
    requires_confirmation: bool = False

    # 294-298: before file_manager's generic "переименуй X в Y" / "rename X
    # to Y" (300), which would otherwise treat "список блич" as a file path.
    # These all need a list word (список / гайд / recipe ...), so renaming
    # files is unaffected. Two-name forms come before one-name forms, so
    # "переименуй список блич в бличик" is not read as naming the current
    # list "блич в бличик".
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^переименуй\s+(?:список|чек-?лист|гайд|рецепт)\s+"
            r"(?!(?:этот|текущий)\b)(.+?)\s+в\s+(.+)$",
            priority=294,
            args=lambda m: {"name": m.group(1), "new_name": m.group(2)},
        ),
        VoicePattern(
            regex=r"^(?:назови|переименуй)\s+(?:этот\s+|текущий\s+)?"
            r"(?:список|чек-?лист|гайд|рецепт)\s+(?:в\s+|как\s+)?(.+)$",
            priority=295,
            args=lambda m: {"new_name": m.group(1)},
        ),
        VoicePattern(
            regex=r"^(?:назви|перейменуй)\s+(?:цей\s+|поточний\s+)?"
            r"(?:список|чек-?лист|гайд|рецепт)\s+(?:в\s+|на\s+|як\s+)?(.+)$",
            priority=296,
            args=lambda m: {"new_name": m.group(1)},
        ),
        VoicePattern(
            regex=r"^rename\s+(?:the\s+)?(?:list|checklist|guide|recipe)\s+"
            r"(?!this\b)(.+?)\s+to\s+(.+)$",
            priority=297,
            args=lambda m: {"name": m.group(1), "new_name": m.group(2)},
        ),
        VoicePattern(
            regex=r"^(?:(?:call|name)\s+(?:this|the\s+current)|rename\s+this)\s+"
            r"(?:list|checklist|guide|recipe)\s+(?:to\s+)?(.+)$",
            priority=298,
            args=lambda m: {"new_name": m.group(1)},
        ),
    )

    async def execute(self, args: NameSavedListArgs) -> ToolResult:
        alias = _clean_alias(args.new_name)
        if not alias:
            return ToolResult(success=False, error="What should the list be called?")
        try:
            return await asyncio.to_thread(self._rename, args.name, alias)
        except Exception as exc:
            log.exception("name_saved_list failed")
            return ToolResult(success=False, error=f"Saved lists error: {exc}")

    def _rename(self, name: str, alias: str) -> ToolResult:
        store = self._get_store()
        col, err = _pick(store, name, None, self._context)
        if col is None:
            return ToolResult(success=False, error=err)
        store.set_alias(col.id, alias)
        return ToolResult(success=True, output=_t("renamed", title=col.title, alias=alias))

