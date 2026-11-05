"""Web page text -> a structured collection (checklist / recipe / list).

This replaces the one-shot KnowledgeExtractor call for pages the user wants
to *work with* later ("what is left to get 100%?", "shopping list for this
recipe"). It was written after a live run on a Steam 100%-achievements
guide failed like this:

* the request carried Jarvis' chat persona as the system prompt, at
  temperature 0.7, with no JSON constraint;
* ``num_predict`` was 1024, so the JSON for ~50 achievements was cut off
  mid-object every time (two "invalid JSON" retries, then a plain-text
  fallback);
* only the first 8000 characters were sent, and on that page the first
  ~1000 are Steam's navigation menu, so the end of the guide was never seen.

Design (each point answers one of those):

* **Own system prompt, no persona**, low temperature.
* **Structured output**: the reply is constrained to a JSON schema via
  Ollama's ``format`` field, so it is valid JSON by construction (only a
  length cut-off can break it -- see salvage below).
* **Chunked**: the whole page (up to MAX_PAGE_CHARS) is split on line
  boundaries into CHUNK_CHARS parts with a small line overlap, so an item
  whose name and description straddle a boundary is seen whole in one part.
  Items from all parts are merged and de-duplicated by name.
* **Compact schema** (short keys, ``details`` capped in the prompt) and a
  larger ``num_predict``, so a part's answer fits.
* **Salvage**: if a reply is still cut off, every complete item before the
  cut is kept instead of losing the whole part.

Alternatives considered: asking the model for the whole page at once with a
bigger context (does not fit 12 GB VRAM next to STT once pages get long,
and an 8B model drops items in long outputs); scraping lists from the HTML
structure (works for one site's markup, not for "any page"); a two-pass
"classify, then extract with a per-type prompt" (doubles the slow part for
little gain -- one schema with a ``kind`` field covers both use cases).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.memory.collections import CollectionKind, ItemRole, NewItem, normalize_name

log = logging.getLogger(__name__)

MAX_PAGE_CHARS = 40_000
CHUNK_CHARS = 5_000
OVERLAP_LINES = 3
NUM_PREDICT = 4096
TEMPERATURE = 0.1
# Background reads yield to the user: how long Jarvis must stay free before
# the next part starts, and how often "busy?" is checked.
IDLE_GRACE_S = 2.0
BUSY_POLL_S = 0.25
# A part whose LLM call fails (live: Ollama answered 500 on part 6 of 9) is
# tried once more after this pause before it is skipped.
RETRY_DELAY_S = 15.0


class _Preempted:
    """_ask() result when the user started talking mid-part."""


_PREEMPTED = _Preempted()


class _Failed:
    """_ask() result when the LLM call itself failed (HTTP 500, dropped
    connection) -- unlike None, a reply that held no usable JSON."""


_FAILED = _Failed()

_KINDS: tuple[str, ...] = ("checklist", "recipe", "list", "article")
_ROLES: tuple[str, ...] = ("task", "ingredient", "step", "item")

# Short keys keep the output (the slow part on an 8B model) small.
#   r = role, s = section, n = name, d = details, q = quantity, u = unit
# "items" is last on purpose: if the reply is cut off, everything before
# the items is already complete and the items can be salvaged.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(_KINDS)},
        "title": {"type": "string"},
        "servings": {"type": ["number", "null"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "r": {"type": "string", "enum": list(_ROLES)},
                    "s": {"type": "string"},
                    "n": {"type": "string"},
                    "d": {"type": "string"},
                    "q": {"type": ["number", "null"]},
                    "u": {"type": "string"},
                },
                "required": ["r", "n"],
            },
        },
    },
    "required": ["kind", "title", "items"],
}

SYSTEM_PROMPT = """\
You convert the text of a web page into a structured list for a personal \
assistant. Answer with JSON only, matching the given schema.

kind:
- "checklist": things the user has to DO or GET: achievements, trophies, \
quests, collectibles, missions, tasks for 100% completion.
- "recipe": a cooking recipe.
- "list": any other list of separate items.
- "article": the page has no list (then return "items": []).

title: a short name of what the page is about, naming the game / dish / \
subject (e.g. "Bleach: Rebirth of Souls - achievements", "Borscht"). \
Not the website name.

items (one per achievement / task / ingredient / step; never merge, never \
skip, never invent):
- r: "task" for a checklist entry, "ingredient" or "step" for a recipe, \
"item" otherwise.
- n: the name exactly as written on the page, in the page's language.
- d: how to do / get it, or a note, at most 20 words; "" if none.
- s: the heading the item is under; "" if none.
- q, u: for ingredients only - amount as a number (0.5 for 1/2) and the \
unit as written ("g", "шт", "ст. л."); q null if not stated.
- steps: in order, n = the instruction itself (shortened if very long).
servings: number of portions if the recipe states it, else null.

How the text is laid out:
- Lines starting with "## " are headings. In a guide, a heading is usually \
ONE item (an achievement / quest / collectible) and the text under it, up \
to the next heading, is that item's description (d). Never make a \
description sentence an item of its own.
- Lines starting with "- " are bullet points, "1. " numbered points: in a \
recipe the bullets are usually ingredients and the numbered points steps.

NOT items: the page or guide title itself; a table of contents (names \
listed at the top without descriptions - they appear again below, take \
them from there); the guide's introduction or summary; section names like \
"Ingredients" or "Description"; author, dates, view / like / favourite \
counters; buttons and links such as "Наградить", "В избранное", \
"Поделиться", "Like", "Share"; links to Discord or other guides; comments; \
website menus, navigation, login, footers, ads.
"""


@dataclass
class ExtractedPage:
    kind: CollectionKind | str
    title: str
    servings: float | None = None
    items: list[NewItem] = field(default_factory=list)
    # Parts of the page whose items are missing (the LLM failed twice, or
    # gave no usable JSON), 1-based, out of total_parts.
    unread_parts: list[int] = field(default_factory=list)
    total_parts: int = 0

    @property
    def is_collection(self) -> bool:
        return self.kind in ("checklist", "recipe", "list") and bool(self.items)


# -- text chunking -------------------------------------------------------------


def split_into_chunks(
    text: str,
    *,
    chunk_chars: int = CHUNK_CHARS,
    overlap_lines: int = OVERLAP_LINES,
    max_chars: int = MAX_PAGE_CHARS,
) -> list[str]:
    """Split *text* on line boundaries into parts of at most ~chunk_chars.

    Consecutive duplicate lines and blank lines are dropped first (page
    text from HTML repeats menu labels a lot). Each part after the first
    starts with the last *overlap_lines* lines of the previous one.
    A single line longer than chunk_chars is cut hard.
    """
    lines: list[str] = []
    for raw in text[:max_chars].splitlines():
        line = raw.strip()
        if not line or (lines and lines[-1] == line):
            continue
        while len(line) > chunk_chars:
            lines.append(line[:chunk_chars])
            line = line[chunk_chars:]
        lines.append(line)

    chunks: list[str] = []
    current: list[str] = []
    fresh = 0  # lines in `current` not already sent in the previous part
    size = 0
    for line in lines:
        if fresh and size + len(line) + 1 > chunk_chars:
            chunks.append("\n".join(current))
            current = current[-overlap_lines:] if overlap_lines else []
            fresh = 0
            size = sum(len(x) + 1 for x in current)
        current.append(line)
        fresh += 1
        size += len(line) + 1
    if fresh:
        chunks.append("\n".join(current))
    return chunks


# -- JSON handling -------------------------------------------------------------


def _strip_wrappers(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    start = text.find("{")
    return text[start:] if start > 0 else text


def salvage_truncated_json(text: str) -> dict | None:
    """Recover the complete items of a reply cut off mid-way.

    Walks the text tracking strings and nesting; remembers where the last
    object directly inside the "items" array closed; returns the prefix up
    to there with the array and the outer object closed.
    """
    key = text.find('"items"')
    if key < 0:
        return None
    array_start = text.find("[", key)
    if array_start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    last_item_end = -1
    for pos in range(array_start, len(text)):
        ch = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 1 and ch == "}":
                last_item_end = pos
            if depth == 0:
                break  # the array closed normally; nothing to salvage
    if last_item_end < 0:
        candidate = text[: array_start + 1] + "]}"
    else:
        candidate = text[: last_item_end + 1] + "]}"
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_reply(text: str) -> dict | None:
    """Parse a model reply into a dict; salvage a truncated one."""
    body = _strip_wrappers(text)
    try:
        data = json.loads(body)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    data = salvage_truncated_json(body)
    if data is not None:
        log.warning(
            "[page_extractor] reply was cut off; kept %d complete items",
            len(data.get("items") or []),
        )
    return data


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", value)
        if m and int(m.group(2)):
            return int(m.group(1)) / int(m.group(2))
        try:
            return float(value.replace(",", ".").strip())
        except ValueError:
            return None
    return None


# Page chrome an 8B model sometimes returns as items despite the prompt.
# Compared after normalize_name (lower-case, ё -> е, words only).
_UI_WORDS: frozenset[str] = frozenset({
    # ru
    "наградить", "в избранное", "в избранном", "избранное", "удалить",
    "поделиться", "войти", "выйти", "подписаться", "отписаться", "нравится",
    "мне нравится", "лайкнуть", "лайк", "комментарии", "комментировать",
    "ответить", "пожаловаться", "сообщить о проблеме", "оглавление",
    "оглавление руководства", "содержание", "описание", "читать далее",
    "показать еще", "назад", "далее", "вперед", "ингредиенты", "приготовление",
    "способ приготовления", "отзывы", "похожие рецепты",
    # uk
    "нагородити", "до обраного", "в обраному", "поділитися", "увійти",
    "подобається", "коментарі", "відповісти", "поскаржитися", "зміст", "опис",
    "інгредієнти", "приготування",
    # en
    "award", "favorite", "favorites", "add to favorites", "share", "like",
    "login", "log in", "sign in", "subscribe", "comments", "reply", "report",
    "table of contents", "contents", "description", "read more", "next",
    "previous", "ingredients", "directions", "method", "instructions",
})


# Community links guides put among their entries ("русскоязычный дискорд
# сервер по игре") -- never an achievement, task or ingredient.
_COMMUNITY_WORDS: tuple[str, ...] = (
    "discord", "дискорд", "telegram", "телеграм", "patreon", "boosty", "бусти",
)


def is_junk_name(name: str, page_titles: tuple[str, ...] = ()) -> bool:
    """True for "items" that are page chrome, not content: no letters at all
    (counters, "1", "|"), a known UI word, or the page's own title -- also
    when the tab title wraps it ("Steam :: Guide :: <guide name>")."""
    key = normalize_name(name)
    if not key or not any(ch.isalpha() for ch in key):
        return True
    if key in _UI_WORDS:
        return True
    if any(word in key for word in _COMMUNITY_WORDS):
        return True
    for title in page_titles:
        title_key = normalize_name(title)
        if not title_key:
            continue
        if key == title_key or (len(key) >= 12 and f" {key} " in f" {title_key} "):
            return True
    return False


def _items_from(data: dict, kind: str, page_titles: tuple[str, ...] = ()) -> list[NewItem]:
    items: list[NewItem] = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        # Accept the long key names too, in case a model ignores the schema.
        name = str(raw.get("n") or raw.get("name") or "").strip()
        if not name or is_junk_name(name, page_titles):
            continue
        role = str(raw.get("r") or raw.get("role") or "item").strip().lower()
        if role not in _ROLES:
            role = "item"
        if kind == "checklist" and role == "item":
            role = "task"
        if kind == "recipe" and role in ("task", "item"):
            role = "step"
        if kind == "list" and role == "task":
            role = "item"
        quantity = _to_float(raw.get("q", raw.get("quantity")))
        items.append(NewItem(
            name=name[:300],
            role=_as_role(role),
            section=str(raw.get("s") or raw.get("section") or "").strip()[:200],
            details=str(raw.get("d") or raw.get("details") or "").strip()[:500],
            quantity=quantity if role == "ingredient" else None,
            unit=str(raw.get("u") or raw.get("unit") or "").strip()[:30]
            if role == "ingredient" else "",
        ))
    return items


def _as_role(role: str) -> ItemRole:
    if role == "task":
        return "task"
    if role == "ingredient":
        return "ingredient"
    if role == "step":
        return "step"
    return "item"


def merge_parts(parts: list[dict], page_title: str = "") -> ExtractedPage | None:
    """Combine per-part replies into one page: kind by item-weighted vote,
    first non-empty title / servings, items de-duplicated by (role, name)
    keeping the first position and the longest details."""
    if not parts:
        return None
    votes: dict[str, int] = {}
    for part in parts:
        kind = str(part.get("kind") or "article")
        if kind not in _KINDS:
            kind = "list"
        votes[kind] = votes.get(kind, 0) + 1 + len(part.get("items") or [])
    kind = max(votes, key=lambda k: votes[k])
    if kind == "article" and any(part.get("items") for part in parts):
        kind = max((k for k in votes if k != "article"), key=lambda k: votes[k])

    title = next((str(p.get("title")).strip() for p in parts if p.get("title")), "")
    servings = next(
        (s for s in (_to_float(p.get("servings")) for p in parts) if s), None
    )

    titles = (title, page_title)
    merged: dict[tuple[str, str], NewItem] = {}
    for part in parts:
        for item in _items_from(part, kind, titles):
            key = (item.role, normalize_name(item.name))
            seen = merged.get(key)
            if seen is None:
                merged[key] = item
            elif len(item.details) > len(seen.details):
                merged[key] = NewItem(
                    name=seen.name, role=seen.role, section=seen.section or item.section,
                    details=item.details, quantity=seen.quantity or item.quantity,
                    unit=seen.unit or item.unit,
                )
    return ExtractedPage(kind=kind, title=title, servings=servings, items=list(merged.values()))


# -- the extractor -------------------------------------------------------------


def _accepts(fn: Any, name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


class PageExtractor:
    """Turns page text into an ExtractedPage with the local LLM."""

    def __init__(
        self,
        llm: Any,
        *,
        chunk_chars: int = CHUNK_CHARS,
        is_busy: Callable[[], bool] | None = None,
        idle_grace_s: float = IDLE_GRACE_S,
        poll_s: float = BUSY_POLL_S,
        retry_delay_s: float = RETRY_DELAY_S,
    ) -> None:
        self._llm = llm
        self._chunk_chars = chunk_chars
        # The user's turn comes first. A part takes up to a minute of
        # Ollama time; with is_busy (live: "is Jarvis mid-turn?") every
        # part waits until Jarvis is free, and a part being generated when
        # the user starts talking is abandoned and redone later. Without it
        # a question asked during a background read stalled for 60 s and
        # failed (live session).
        self._is_busy = is_busy
        self._idle_grace_s = idle_grace_s
        self._poll_s = poll_s
        self._retry_delay_s = retry_delay_s

    async def extract(self, text: str, url: str, title: str) -> ExtractedPage | None:
        chunks = split_into_chunks(text, chunk_chars=self._chunk_chars)
        if not chunks:
            return None
        log.info("[page_extractor] %d chars -> %d part(s)", len(text), len(chunks))
        parts: list[dict] = []
        unread: list[int] = []
        for index, chunk in enumerate(chunks, start=1):
            reply = await self._read_part(chunk, index, len(chunks), url, title)
            if reply is None:
                log.warning("[page_extractor] part %d/%d gave no usable JSON", index, len(chunks))
                unread.append(index)
                continue
            if isinstance(reply, _Failed):
                unread.append(index)
                continue
            parts.append(reply)
        page = merge_parts(parts, title)
        if page is not None:
            page.unread_parts = unread
            page.total_parts = len(chunks)
            log.info(
                "[page_extractor] kind=%s items=%d (from %d/%d parts; unread: %s)",
                page.kind, len(page.items), len(parts), len(chunks), unread or "none",
            )
        return page

    async def _read_part(
        self, chunk: str, index: int, total: int, url: str, title: str
    ) -> dict | None | _Failed:
        """One part, redone when the user interrupts it and retried once
        after retry_delay_s when the LLM call fails."""
        retried = False
        while True:
            await self._wait_until_free()
            reply = await self._ask(chunk, index, total, url, title)
            if isinstance(reply, _Preempted):
                log.info(
                    "[page_extractor] part %d/%d paused: the user is talking; will redo it",
                    index, total,
                )
                continue
            if isinstance(reply, _Failed):
                if not retried:
                    retried = True
                    log.warning(
                        "[page_extractor] part %d/%d failed; retrying once in %.0f s",
                        index, total, self._retry_delay_s,
                    )
                    await asyncio.sleep(self._retry_delay_s)
                    continue
                log.warning(
                    "[page_extractor] part %d/%d failed again; skipped, its items are missing",
                    index, total,
                )
            return reply

    async def _ask(
        self, chunk: str, index: int, total: int, url: str, title: str
    ) -> dict | None | _Preempted | _Failed:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Page title: {title or 'unknown'}\nURL: {url}\n"
                    f"Part {index} of {total} of the page text "
                    "(extract only what is in this part):\n\n" + chunk
                ),
            },
        ]
        kwargs: dict[str, Any] = {}
        if _accepts(self._llm.stream_chat, "format"):
            kwargs["format"] = RESPONSE_SCHEMA
        if _accepts(self._llm.stream_chat, "options"):
            kwargs["options"] = {"temperature": TEMPERATURE, "num_predict": NUM_PREDICT}
        reply = ""
        stream = self._llm.stream_chat(messages, **kwargs)
        try:
            async for piece in stream:
                if self._busy():
                    # Closing the stream drops the HTTP request, and Ollama
                    # stops generating, so the user's turn gets the GPU.
                    return _PREEMPTED
                reply += piece.content
                if piece.done:
                    break
        except Exception:
            log.exception("[page_extractor] LLM call failed on part %d/%d", index, total)
            return _FAILED
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    log.debug("[page_extractor] closing the stream failed", exc_info=True)
        return parse_reply(reply)

    def _busy(self) -> bool:
        if self._is_busy is None:
            return False
        try:
            return bool(self._is_busy())
        except Exception:
            log.debug("[page_extractor] is_busy check failed", exc_info=True)
            return False

    async def _wait_until_free(self) -> None:
        """Return once Jarvis has been free for idle_grace_s, so a follow-up
        question right after an answer still goes before the next part."""
        if self._is_busy is None:
            return
        while True:
            while self._busy():
                await asyncio.sleep(self._poll_s)
            free_for = 0.0
            while free_for < self._idle_grace_s and not self._busy():
                await asyncio.sleep(self._poll_s)
                free_for += self._poll_s
            if not self._busy():
                return
