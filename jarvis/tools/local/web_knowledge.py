"""Save structured knowledge from a web page into Jarvis memory.

Reads the current browser page (Selenium or httpx fallback), sends the
text to a local LLM for structured extraction, and persists each
extracted item as a separate memory fact with relations.

Works for ANY content type: game guides, recipes, tech tutorials,
collections, reference pages, etc.  The LLM decides the structure.

Dependencies (all optional, checked at runtime):
    - browser_controller (Selenium) for live page text
    - content_reader helpers (httpx + BeautifulSoup) as fallback
    - ollama_client for LLM extraction
    - memory_store + relation_store for persistence

Offline handling:
    Network-dependent operations catch connection errors and return a
    clear error message so the voice loop continues.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from jarvis.memory.collections import CollectionKind
from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

# How much page text is read. The collection extractor processes it in
# parts (jarvis.memory.page_extractor.MAX_PAGE_CHARS); the older
# KnowledgeExtractor cuts it to its own 8000-character limit.
_MAX_PAGE_CHARS = 40_000

_READING_PHRASES = {
    "ru": "Читаю страницу, сэр. Это займёт около минуты.",
    "uk": "Читаю сторінку, сер. Це займе близько хвилини.",
    "en": "Reading the page, sir. This will take a minute or so.",
}


def _same_page(a: str, b: str) -> bool:
    """True when two URLs name the same page (ignoring #fragment and a
    trailing slash), e.g. the Selenium tab vs. the URL we were asked for."""

    def norm(u: str) -> str:
        return u.split("#", 1)[0].rstrip("/").lower()

    return norm(a) == norm(b)


class SavePageKnowledgeArgs(BaseModel):
    url: str = Field(
        default="",
        description=(
            "URL to extract knowledge from. Leave empty to use the page "
            "currently open in the user's browser."
        ),
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class SavePageKnowledgeTool:
    """Extract structured knowledge from a web page and save to memory."""

    name: str = "save_page_knowledge"
    description: str = (
        "Remembers what is ON the web page the user has open (or on a given "
        "URL): reads the page, extracts structured knowledge (items, steps, "
        "ingredients, achievements, requirements) with an LLM and saves it "
        "to Jarvis memory for later questions. Use when the user asks to "
        "remember / save this page, recipe, guide, walkthrough or article. "
        "Works for game guides, recipes, tech docs, collections — any "
        "structured content. To save only the link, use bookmarks. "
        "(RU: запомни эту страницу, сохрани этот рецепт, сохрани гайд, "
        "сохрани информацию со страницы / UK: запамʼятай цю сторінку, "
        "збережи рецепт, збережи інформацію зі сторінки)"
    )
    args_schema = SavePageKnowledgeArgs
    requires_confirmation: bool = False

    # Priority 432: these must beat take_note's catch-all "^запомни (.+)$" /
    # "^remember this ..." (440). Before, "запомни эту страницу" became a
    # note reading "эту страницу" and the page was never read. Bookmarks'
    # save-link patterns (434) do not overlap these: they all need
    # "ссылк" / "посилання" / "link" / "в закладки".
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^save\s+(?:this\s+)?(?:page\s+)?(?:information|knowledge|info)$",
            priority=432,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^save\s+(?:information|knowledge)\s+from\s+(?:this\s+)?page$",
            priority=432,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^(?:remember|save)\s+(?:this\s+|the\s+current\s+|current\s+|the\s+open\s+)?"
            r"(?:page|recipe|guide|walkthrough|article)(?:\s+from\s+(?:this\s+)?page)?$",
            priority=432,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^extract\s+knowledge\s+from\s+(?:this\s+)?page$",
            priority=432,
            args=lambda m: {},
        ),
        # RU
        VoicePattern(
            regex=r"^сохрани(?:те)?\s+(?:информацию|данные)\s+(?:с[ое]?\s+|из\s+)?"
            r"(?:этой\s+)?страниц[ыуи]$",
            priority=432,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^сохрани\s+знания\s+(?:с[ое]?\s+|из\s+)?(?:этой\s+)?страниц[ыуи]$",
            priority=432,
            args=lambda m: {},
        ),
        VoicePattern(
            # "запомни эту страницу", "сохрани этот рецепт", "запомни гайд
            # с этой страницы", "сохрани это прохождение"
            regex=r"^(?:запомни|сохрани)(?:те)?\s+"
            r"(?:эт(?:от|у|о|и)\s+|текущ(?:ую|ий|ее)\s+|открыт(?:ую|ый|ое)\s+)?"
            r"(?:страниц[уа]|рецепт|гайд|руководство|прохождение|статью)"
            r"(?:\s+(?:с[ое]?|из)\s+(?:этой\s+)?страниц[ыи])?$",
            priority=432,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^запомни(?:те)?\s+что\s+на\s+(?:этой\s+|текущей\s+|открытой\s+)?страниц[еу]$",
            priority=432,
            args=lambda m: {},
        ),
        # UK
        VoicePattern(
            regex=r"^збереж(?:и|іть)\s+(?:інформацію|дані)\s+(?:зі?\s+|із\s+)?"
            r"(?:цієї\s+)?сторінк[иу]$",
            priority=432,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^збережи\s+знання\s+(?:зі?\s+|із\s+)?(?:цієї\s+)?сторінк[иу]$",
            priority=432,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^(?:запам['ʼ’]?ятай(?:те)?|збереж(?:и|іть))\s+"
            r"(?:ц(?:ю|ей|е)\s+|поточн(?:у|ий|е)\s+|відкрит(?:у|ий|е)\s+)?"
            r"(?:сторінк[уа]|рецепт|гайд|посібник|проходження|статтю)"
            r"(?:\s+(?:з|зі|із)\s+(?:цієї\s+)?сторінки)?$",
            priority=432,
            args=lambda m: {},
        ),
        VoicePattern(
            regex=r"^запам['ʼ’]?ятай\s+що\s+на\s+(?:цій\s+)?сторінц[іу]$",
            priority=432,
            args=lambda m: {},
        ),
    )

    def __init__(
        self,
        *,
        ollama_client: Any = None,
        memory_store: Any = None,
        relation_store: Any = None,
        browser_controller: Any = None,
        browser_provider: Callable[[], Any] | None = None,
        collection_store: Any = None,
        on_speak: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._ollama = ollama_client
        self._memory_store = memory_store
        self._relation_store = relation_store
        self._browser_controller = browser_controller
        # With a CollectionStore the page becomes a checklist / recipe the
        # user can query and tick later (jarvis.memory.page_extractor);
        # without one the older fact-per-item KnowledgeExtractor is used.
        self._collections = collection_store
        # Extraction takes from ~20 s to a few minutes on an 8B model; say
        # so before starting, the way deep research does, so a silent
        # Jarvis is not taken for a hang.
        self._on_speak = on_speak
        # Lazy alternative to browser_controller: building a BrowserController
        # probes Selenium, which is not worth doing at startup for a tool the
        # user may never call.
        self._browser_provider = browser_provider

    def _browser(self) -> Any:
        if self._browser_controller is None and self._browser_provider is not None:
            try:
                self._browser_controller = self._browser_provider()
            except Exception:
                log.debug("browser provider failed", exc_info=True)
                self._browser_provider = None
        return self._browser_controller

    # -- execute ------------------------------------------------------------

    async def execute(self, args: SavePageKnowledgeArgs) -> ToolResult:
        """Main entry point: fetch page → extract → save."""
        # 0. Pre-flight checks
        if not self._ollama:
            return ToolResult(
                success=False,
                error="Knowledge extraction requires an LLM client, but none is available.",
            )
        if not self._memory_store:
            return ToolResult(
                success=False,
                error="Knowledge extraction requires a memory store, but none is available.",
            )

        # 1. Get page content
        page_text, page_url, page_title, err = await self._get_page_content(
            args.url
        )
        if err:
            return ToolResult(success=False, error=err)
        if not page_text or not page_text.strip():
            return ToolResult(
                success=False,
                error="Page appears to be empty — no text content found.",
            )

        # Security Layer 3: the page is untrusted. Strip prompt-injection
        # payloads before the text is handed to the LLM extractor and
        # persisted into long-term memory.
        from jarvis.security.output_sanitizer import clean_external_text

        page_text = clean_external_text(page_text, source=f"web_knowledge:{page_url}")

        # 2a. Structured collection (checklist / recipe / list).
        if self._collections is not None:
            collection_result = await self._save_as_collection(
                page_text, page_url, page_title
            )
            if collection_result is not None:
                return collection_result
            log.info("[web_knowledge] no list found on the page; saving it as facts")

        # 2b. Extract and save via KnowledgeExtractor
        from jarvis.memory.knowledge_extractor import KnowledgeExtractor

        extractor = KnowledgeExtractor(
            ollama_client=self._ollama,
            memory_store=self._memory_store,
            relation_store=self._relation_store,
        )
        result = await extractor.extract_and_save(page_text, page_url, page_title)

        # 3. Format response
        if result.get("error"):
            return ToolResult(success=False, error=result["error"])

        topic = result.get("topic", "unknown")
        items_saved = result.get("items_saved", 0)
        relations = result.get("relations_created", 0)
        is_fallback = result.get("fallback", False)

        if is_fallback:
            return ToolResult(
                success=True,
                output=(
                    f"Page saved as a single fact (LLM could not extract structure). "
                    f"Topic: {topic}"
                ),
            )

        return ToolResult(
            success=True,
            output=(
                f"Saved {items_saved} facts about \"{topic}\" with "
                f"{relations} relations, sir."
            ),
        )

    # -- collection path ----------------------------------------------------

    async def _say_reading(self) -> None:
        if self._on_speak is None:
            return
        from jarvis.core.phrases import reply_language

        lang = reply_language()
        try:
            await self._on_speak(_READING_PHRASES.get(lang, _READING_PHRASES["en"]))
        except Exception:
            log.debug("reading notice could not be spoken", exc_info=True)

    async def _save_as_collection(
        self, text: str, url: str, title: str
    ) -> ToolResult | None:
        """Extract a checklist / recipe / list and store it.

        Returns None when the page holds no list (an article), so the caller
        falls back to saving facts."""
        from jarvis.memory.page_extractor import PageExtractor

        await self._say_reading()
        page = await PageExtractor(self._ollama).extract(text, url, title)
        if page is None or not page.is_collection:
            return None
        kind = _collection_kind(page.kind)
        collection = await asyncio.to_thread(
            self._collections.save,
            kind=kind,
            title=page.title or title or url,
            items=page.items,
            source_url=url,
            source_title=title,
            servings=page.servings,
        )
        await self._remember_collection(collection)
        note_title = None
        if collection.kind == "recipe" and collection.ingredients():
            note_title = await asyncio.to_thread(_write_shopping_note, collection)
        return ToolResult(success=True, output=_describe_saved(collection, note_title))

    async def _remember_collection(self, collection: Any) -> None:
        """One short fact so auto-recall knows the list exists ("what did I
        save about Bleach?"); the rows themselves live in the CollectionStore."""
        count = len(collection.items)
        text = (
            f"Saved {collection.kind} \"{collection.title}\" from {collection.source_url} "
            f"({count} items). Ask \"what is left\" / \"shopping list\" to use it."
        )
        try:
            await self._memory_store.add(
                text,
                category="web_import",
                source="import",
                tags=[
                    "source:web", f"url:{collection.source_url}",
                    f"collection:{collection.id}", f"type:{collection.kind}",
                ],
            )
        except Exception:
            log.warning("could not write the collection summary fact", exc_info=True)

    # -- page content retrieval ---------------------------------------------

    async def _get_page_content(
        self, url: str
    ) -> tuple[str, str, str, str | None]:
        """Fetch page text, url, title.  Returns (text, url, title, error).

        Which page: the explicit *url*, else the page the user is looking at
        (``jarvis.browser.current_page``: Jarvis' Selenium session, the
        user's own browser via UI Automation, or the page Jarvis opened
        last). How it is read: the live DOM when that page is the one shown
        in an already-running Selenium session, otherwise httpx.

        Selenium is never STARTED here: launching an automated Chrome window
        just to read a URL the user did not open there would be a surprise
        window on their desktop.
        """
        bc = self._browser()

        if not url:
            from jarvis.browser.current_page import current_page

            page = await asyncio.to_thread(current_page, bc)
            if page is None:
                return "", "", "", (
                    "Could not tell which page is open: no browser window with "
                    "a web page was found. Open the page in the browser, or "
                    "give me its address."
                )
            url = page.url

        # Live DOM when Jarvis' own Selenium session is showing this page.
        if bc is not None and getattr(bc, "driver_active", False):
            text, page_url, title, err = await self._try_selenium(url)
            if err is None:
                return text, page_url, title, None

        return await asyncio.to_thread(self._fetch_with_httpx, url)

    async def _try_selenium(
        self, url: str
    ) -> tuple[str, str, str, str | None]:
        """Read the page shown in the running Selenium session.

        Only when that session is already on *url* (or no url is given);
        otherwise returns an error so the caller fetches *url* over HTTP.
        Returns (text, url, title, error)."""
        bc = self._browser()
        if bc is None or not bc.driver_active:
            return "", "", "", "No active browser session"

        try:
            # Get URL
            url_result = bc.get_current_url()
            page_url = url_result.get("url", url or "")
            if url and not _same_page(page_url, url):
                return "", page_url, "", "Selenium session shows another page"

            # Get title
            title_result = bc.get_page_title()
            page_title = title_result.get("title", "")

            # The live DOM's main content (menus, buttons, footers dropped);
            # the plain visible text only if the source is unavailable.
            source = bc.get_page_source()
            ok = isinstance(source, dict) and source.get("success")
            html = source.get("html") if ok else None
            if isinstance(html, str) and html:
                text = _main_text(html)
                if text:
                    return text, page_url, page_title, None

            text_result = bc.get_page_text(max_chars=_MAX_PAGE_CHARS)
            if not text_result.get("success"):
                return "", page_url, page_title, text_result.get("error", "Failed to get page text")

            return text_result["text"], page_url, page_title, None
        except Exception as exc:
            return "", "", "", f"Selenium error: {exc}"

    @staticmethod
    def _fetch_with_httpx(url: str) -> tuple[str, str, str, str | None]:
        """Fetch and extract text via httpx + BeautifulSoup."""
        from jarvis.tools.local.content_reader import (
            _extract_readable,
            _extract_title,
            _httpx_fetch,
        )

        fetched = _httpx_fetch(url, timeout=20.0)
        if not fetched.get("success"):
            return "", url, "", fetched.get("error", "Fetch failed")

        html = fetched["html"]
        text = _main_text(html) or _extract_readable(html, max_chars=_MAX_PAGE_CHARS)
        title = _extract_title(html)
        final_url = fetched.get("url", url)

        return text, final_url, title, None


# -- helpers for the collection path ---------------------------------------------


def _main_text(html: str) -> str:
    """The page's main content (jarvis.browser.readable), or "" when that
    cleaning removed so much that it probably removed the content too --
    then the caller uses the whole page text instead."""
    from jarvis.browser.readable import extract_main_text
    from jarvis.tools.local.content_reader import _extract_readable

    main = extract_main_text(html, max_chars=_MAX_PAGE_CHARS)
    full = _extract_readable(html, max_chars=_MAX_PAGE_CHARS)
    if len(main) < 300 and len(main) < len(full) * 0.15:
        log.info("[web_knowledge] main-content cleaning kept too little; using full text")
        return ""
    log.info("[web_knowledge] page text %d chars, main content %d chars", len(full), len(main))
    return main


def _collection_kind(kind: str) -> CollectionKind:
    if kind == "checklist":
        return "checklist"
    if kind == "recipe":
        return "recipe"
    return "list"


_SHOPPING_TITLE = {"ru": "Список продуктов", "uk": "Список продуктів", "en": "Shopping list"}


def _write_shopping_note(collection: Any) -> str | None:
    """Put the recipe's ingredients into a note right away, as a checkbox
    list, so the shopping list is there without asking. Returns its title."""
    from jarvis.core.phrases import reply_language
    from jarvis.tools.local.notes_store import create_note
    from jarvis.tools.local.saved_lists import format_ingredient, format_number

    head = _SHOPPING_TITLE.get(reply_language(), _SHOPPING_TITLE["en"])
    title = f"{head}: {collection.title}"
    lines = [f"- [ ] {format_ingredient(item)}" for item in collection.ingredients()]
    if collection.servings:
        lines.insert(0, f"({format_number(collection.servings)} порц.)")
    lines.append("")
    lines.append(collection.source_url)
    try:
        create_note(title, "\n".join(lines))
    except Exception:
        log.warning("could not write the shopping-list note", exc_info=True)
        return None
    return title


def _describe_saved(collection: Any, note_title: str | None) -> str:
    """What the tool reports back (the LLM phrases it for the user)."""
    from jarvis.tools.local.saved_lists import format_number

    if collection.kind == "recipe":
        servings = (
            f", {format_number(collection.servings)} servings" if collection.servings else ""
        )
        text = (
            f"Saved recipe \"{collection.title}\": {len(collection.ingredients())} "
            f"ingredients, {len(collection.steps())} steps{servings}."
        )
        if note_title:
            text += f" Shopping list saved as the note \"{note_title}\"."
        return text
    kept = (
        f" {collection.done_count} already marked done (kept from before)."
        if collection.done_count else ""
    )
    return (
        f"Saved {collection.kind} \"{collection.title}\": {collection.total} items.{kept} "
        "The user can now ask what is left, the progress, or say "
        "\"mark <item> as done\"."
    )

