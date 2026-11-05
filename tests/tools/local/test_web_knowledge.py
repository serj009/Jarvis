"""Tests for jarvis.tools.local.web_knowledge — SavePageKnowledgeTool.

Fully mocked: no real browser, no real LLM, no real database.
"""

from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.web_knowledge import (
    SavePageKnowledgeArgs,
    SavePageKnowledgeTool,
)
from jarvis.tools.registry import EmptyArgs, VoicePattern
from tests._typing import output_text

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_GAME_PAGE_TEXT = """\
Elden Ring Achievements Guide

1. Elden Lord — Achieve the Elden Lord ending.
   This is the default ending of the game.

2. Age of Stars — Achieve the Age of Stars ending.
   Complete Ranni's questline to unlock this ending.
   Requirements: Finish Ranni's quest, choose her ending.
   Location: Erdtree, Throne Room.

3. Lord of Frenzied Flame — Achieve the Frenzied Flame ending.
   Go to the Frenzied Flame Proscription beneath the capital.
   Related achievements: Elden Lord (alternate ending path).
"""

FAKE_RECIPE_PAGE_TEXT = """\
Classic Pancake Recipe

Ingredients:
- 1 cup flour
- 1 egg
- 1 cup milk
- 2 tbsp butter

Steps:
1. Mix dry ingredients
2. Add wet ingredients
3. Cook on griddle until golden
"""

FAKE_COLLECTION_PAGE_TEXT = """\
Rare Pokemon Cards Collection Guide

1. Charizard Base Set — Most valuable card in the original set.
   Value: $5000+
   Condition matters: PSA 10 = $50000+

2. Pikachu Illustrator — Rarest card ever printed.
   Only 39 copies exist.
   Value: $900,000+
"""


def _make_llm_response(data: dict) -> str:
    """Build JSON string as LLM would return."""
    return json.dumps(data)


def _fake_game_llm_response() -> dict:
    return {
        "topic": "Elden Ring Achievements Guide",
        "type": "game_guide",
        "source_url": "https://example.com/elden-ring",
        "items": [
            {
                "name": "Elden Lord",
                "description": "Achieve the Elden Lord ending. Default ending.",
                "category": "achievement",
                "sub_items": [],
                "location": "",
                "requirements": [],
                "tips": "",
                "related_to": [],
            },
            {
                "name": "Age of Stars",
                "description": "Achieve the Age of Stars ending.",
                "category": "achievement",
                "sub_items": [],
                "location": "Erdtree, Throne Room",
                "requirements": ["Finish Ranni's quest"],
                "tips": "Complete Ranni's full questline first.",
                "related_to": ["Elden Lord"],
            },
            {
                "name": "Lord of Frenzied Flame",
                "description": "Achieve the Frenzied Flame ending.",
                "category": "achievement",
                "sub_items": [],
                "location": "Frenzied Flame Proscription",
                "requirements": [],
                "tips": "",
                "related_to": ["Elden Lord"],
            },
        ],
    }


def _fake_recipe_llm_response() -> dict:
    return {
        "topic": "Classic Pancake Recipe",
        "type": "recipe",
        "source_url": "https://example.com/pancakes",
        "items": [
            {
                "name": "Pancake Ingredients",
                "description": "Ingredients for classic pancakes",
                "category": "ingredients",
                "sub_items": ["1 cup flour", "1 egg", "1 cup milk", "2 tbsp butter"],
                "location": "",
                "requirements": [],
                "tips": "",
                "related_to": ["Pancake Steps"],
            },
            {
                "name": "Pancake Steps",
                "description": "Steps to make pancakes",
                "category": "steps",
                "sub_items": [
                    "Mix dry ingredients",
                    "Add wet ingredients",
                    "Cook on griddle until golden",
                ],
                "location": "",
                "requirements": [],
                "tips": "",
                "related_to": ["Pancake Ingredients"],
            },
        ],
    }


def _fake_collection_llm_response() -> dict:
    return {
        "topic": "Rare Pokemon Cards Collection Guide",
        "type": "collection",
        "source_url": "https://example.com/pokemon-cards",
        "items": [
            {
                "name": "Charizard Base Set",
                "description": "Most valuable card in the original set. Value: $5000+",
                "category": "card",
                "sub_items": [],
                "location": "",
                "requirements": [],
                "tips": "Condition matters: PSA 10 = $50000+",
                "related_to": [],
            },
            {
                "name": "Pikachu Illustrator",
                "description": "Rarest card ever printed. Only 39 copies exist.",
                "category": "card",
                "sub_items": [],
                "location": "",
                "requirements": [],
                "tips": "",
                "related_to": [],
            },
        ],
    }


class FakeChunk:
    """Fake ChatChunk for mocking OllamaClient.stream_chat."""
    def __init__(self, content: str = "", done: bool = False):
        self.content = content
        self.done = done


def _build_mock_ollama(response_data: dict):
    """Build a mock OllamaClient that returns structured JSON."""
    mock = MagicMock()
    response_json = json.dumps(response_data)
    chunks = [FakeChunk(content=response_json, done=True)]

    async def fake_stream_chat(messages, **kwargs):
        for chunk in chunks:
            yield chunk

    mock.stream_chat = fake_stream_chat
    return mock


def _build_mock_ollama_invalid():
    """Build a mock that returns invalid JSON both times (retry fails)."""
    mock = MagicMock()

    async def fake_stream_chat(messages, **kwargs):
        yield FakeChunk(content="This is not JSON at all {{{{", done=True)

    mock.stream_chat = fake_stream_chat
    return mock


def _build_mock_memory_store():
    """Build a mock MemoryStore with an add() that returns incrementing IDs."""
    mock = MagicMock()
    call_count = 0

    async def fake_add(text, *, category="general", source="user",
                       language="en", tags=None, is_permanent=False):
        nonlocal call_count
        call_count += 1
        return f"fact_{call_count:03d}"

    mock.add = fake_add
    mock.is_open = True
    return mock


def _build_mock_relation_store():
    """Build a mock RelationStore."""
    mock = MagicMock()
    mock.add_relation = MagicMock(return_value=1)
    return mock


def _build_mock_browser(text: str, url: str, title: str, active: bool = True):
    """Build a mock BrowserController."""
    mock = MagicMock()
    mock.driver_active = active
    mock.get_page_text = MagicMock(return_value={
        "success": True,
        "text": text,
        "length": len(text),
        "truncated": False,
    })
    mock.get_current_url = MagicMock(return_value={
        "success": True,
        "url": url,
    })
    mock.get_page_title = MagicMock(return_value={
        "success": True,
        "title": title,
    })
    return mock


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocol:
    def test_has_required_attributes(self):
        tool = SavePageKnowledgeTool()
        assert tool.name == "save_page_knowledge"
        assert tool.description
        assert tool.args_schema is SavePageKnowledgeArgs
        assert tool.requires_confirmation is False

    def test_has_voice_patterns(self):
        assert hasattr(SavePageKnowledgeTool, "voice_patterns")
        assert isinstance(SavePageKnowledgeTool.voice_patterns, tuple)
        assert all(
            isinstance(p, VoicePattern)
            for p in SavePageKnowledgeTool.voice_patterns
        )
        assert len(SavePageKnowledgeTool.voice_patterns) >= 12


# ---------------------------------------------------------------------------
# Voice pattern matching
# ---------------------------------------------------------------------------

class TestVoicePatterns:
    @pytest.mark.parametrize("utterance", [
        "save page information",
        "save this page knowledge",
        "save information from page",
        "save information from this page",
        "remember this page",
        "remember page",
        "extract knowledge from this page",
        "extract knowledge from page",
        "save this page",
        "remember this recipe",
        "save this guide",
        "save walkthrough from this page",
    ])
    def test_en_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in SavePageKnowledgeTool.voice_patterns
        )
        assert matched, f"No EN pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "сохрани информацию со страницы",
        "сохрани информацию с страницы",
        "запомни эту страницу",
        "запомни страницу",
        "сохрани знания со страницы",
        "запомни что на странице",
        "сохрани эту страницу",
        "сохрани страницу",
        "запомни этот рецепт",
        "сохрани рецепт со страницы",
        "сохрани этот гайд",
        "запомни гайд с этой страницы",
        "сохрани это прохождение",
        "сохрани информацию с этой страницы",
        "сохрани данные со страницы",
    ])
    def test_ru_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in SavePageKnowledgeTool.voice_patterns
        )
        assert matched, f"No RU pattern matched: {utterance!r}"

    @pytest.mark.parametrize("utterance", [
        "збережи інформацію зі сторінки",
        "збережи інформацію з сторінки",
        "запамʼятай цю сторінку",
        "запам'ятай сторінку",
        "збережи знання зі сторінки",
        "запамʼятай що на сторінці",
        "запам’ятай цю сторінку",
        "збережи цю сторінку",
        "збережи рецепт",
        "запам'ятай цей рецепт",
        "збережи гайд з цієї сторінки",
        "збережи проходження",
    ])
    def test_uk_patterns_match(self, utterance):
        matched = any(
            re.match(vp.regex, utterance)
            for vp in SavePageKnowledgeTool.voice_patterns
        )
        assert matched, f"No UK pattern matched: {utterance!r}"


# ---------------------------------------------------------------------------
# Execute — game achievements
# ---------------------------------------------------------------------------

class TestGameAchievementsPage:
    @pytest.mark.asyncio
    async def test_extracts_and_saves_game_achievements(self):
        ollama = _build_mock_ollama(_fake_game_llm_response())
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()
        browser = _build_mock_browser(
            FAKE_GAME_PAGE_TEXT,
            "https://example.com/elden-ring",
            "Elden Ring Achievements",
        )

        tool = SavePageKnowledgeTool(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
            browser_controller=browser,
        )

        result = await tool.execute(SavePageKnowledgeArgs())
        assert result.success
        assert "4" in result.output  # 1 topic + 3 achievements
        assert "Elden Ring" in result.output

    @pytest.mark.asyncio
    async def test_relations_created_for_game_page(self):
        ollama = _build_mock_ollama(_fake_game_llm_response())
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()
        browser = _build_mock_browser(
            FAKE_GAME_PAGE_TEXT,
            "https://example.com/elden-ring",
            "Elden Ring Achievements",
        )

        tool = SavePageKnowledgeTool(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
            browser_controller=browser,
        )

        result = await tool.execute(SavePageKnowledgeArgs())
        assert result.success
        # 3 belongs_to + 2 related_to (Age of Stars → Elden Lord, Lord of FF → Elden Lord)
        assert relations.add_relation.call_count >= 3


# ---------------------------------------------------------------------------
# Execute — recipe page
# ---------------------------------------------------------------------------

class TestRecipePage:
    @pytest.mark.asyncio
    async def test_extracts_recipe_structure(self):
        ollama = _build_mock_ollama(_fake_recipe_llm_response())
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()
        browser = _build_mock_browser(
            FAKE_RECIPE_PAGE_TEXT,
            "https://example.com/pancakes",
            "Classic Pancake Recipe",
        )

        tool = SavePageKnowledgeTool(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
            browser_controller=browser,
        )

        result = await tool.execute(SavePageKnowledgeArgs())
        assert result.success
        # 1 topic + 2 items (ingredients, steps)
        assert "3" in result.output
        assert "Pancake" in result.output


# ---------------------------------------------------------------------------
# Execute — collection page
# ---------------------------------------------------------------------------

class TestCollectionPage:
    @pytest.mark.asyncio
    async def test_extracts_collection_structure(self):
        ollama = _build_mock_ollama(_fake_collection_llm_response())
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()
        browser = _build_mock_browser(
            FAKE_COLLECTION_PAGE_TEXT,
            "https://example.com/pokemon-cards",
            "Rare Pokemon Cards",
        )

        tool = SavePageKnowledgeTool(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
            browser_controller=browser,
        )

        result = await tool.execute(SavePageKnowledgeArgs())
        assert result.success
        # 1 topic + 2 cards
        assert "3" in result.output
        assert "Pokemon" in result.output


# ---------------------------------------------------------------------------
# Execute — LLM returns invalid JSON → graceful fallback
# ---------------------------------------------------------------------------

class TestInvalidJsonFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_plain_text_on_invalid_json(self):
        ollama = _build_mock_ollama_invalid()
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()
        browser = _build_mock_browser(
            "Some plain text page content",
            "https://example.com/plain",
            "Plain Page",
        )

        tool = SavePageKnowledgeTool(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
            browser_controller=browser,
        )

        result = await tool.execute(SavePageKnowledgeArgs())
        assert result.success
        assert "single fact" in result.output.lower() or "plain" in result.output.lower()


# ---------------------------------------------------------------------------
# Execute — very long page → truncation
# ---------------------------------------------------------------------------

class TestLongPageTruncation:
    @pytest.mark.asyncio
    async def test_long_page_truncated(self):
        long_text = "A" * 15_000  # Exceeds MAX_PAGE_CHARS (10000)
        ollama = _build_mock_ollama({
            "topic": "Long Page",
            "type": "article",
            "source_url": "https://example.com/long",
            "items": [
                {"name": "Item 1", "description": "Desc", "category": "misc",
                 "sub_items": [], "location": "", "requirements": [],
                 "tips": "", "related_to": []},
            ],
        })
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()
        browser = _build_mock_browser(
            long_text,
            "https://example.com/long",
            "Long Page",
        )

        tool = SavePageKnowledgeTool(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
            browser_controller=browser,
        )

        result = await tool.execute(SavePageKnowledgeArgs())
        assert result.success
        # Should still save — just truncated input to LLM
        assert "Long Page" in result.output


# ---------------------------------------------------------------------------
# Execute — page with no structure → single fact
# ---------------------------------------------------------------------------

class TestNoStructurePage:
    @pytest.mark.asyncio
    async def test_single_item_extracted(self):
        ollama = _build_mock_ollama({
            "topic": "Random Blog Post",
            "type": "article",
            "source_url": "https://example.com/blog",
            "items": [
                {"name": "Main Content", "description": "The entire blog post content",
                 "category": "article", "sub_items": [], "location": "",
                 "requirements": [], "tips": "", "related_to": []},
            ],
        })
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()
        browser = _build_mock_browser(
            "This is just a random blog post with no structure.",
            "https://example.com/blog",
            "Random Blog",
        )

        tool = SavePageKnowledgeTool(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
            browser_controller=browser,
        )

        result = await tool.execute(SavePageKnowledgeArgs())
        assert result.success


# ---------------------------------------------------------------------------
# Execute — error handling: missing dependencies
# ---------------------------------------------------------------------------

class TestMissingDependencies:
    @pytest.mark.asyncio
    async def test_no_ollama_client(self):
        tool = SavePageKnowledgeTool(
            ollama_client=None,
            memory_store=_build_mock_memory_store(),
        )
        result = await tool.execute(SavePageKnowledgeArgs())
        assert not result.success
        assert "LLM" in result.error

    @pytest.mark.asyncio
    async def test_no_memory_store(self):
        tool = SavePageKnowledgeTool(
            ollama_client=MagicMock(),
            memory_store=None,
        )
        result = await tool.execute(SavePageKnowledgeArgs())
        assert not result.success
        assert "memory" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_browser_and_no_url(self):
        tool = SavePageKnowledgeTool(
            ollama_client=MagicMock(),
            memory_store=_build_mock_memory_store(),
            browser_controller=None,
        )
        result = await tool.execute(SavePageKnowledgeArgs(url=""))
        assert not result.success

    @pytest.mark.asyncio
    async def test_empty_page_text(self):
        browser = _build_mock_browser("", "https://example.com", "Empty")
        tool = SavePageKnowledgeTool(
            ollama_client=MagicMock(),
            memory_store=_build_mock_memory_store(),
            browser_controller=browser,
        )
        result = await tool.execute(SavePageKnowledgeArgs())
        assert not result.success
        assert "empty" in result.error.lower()


# ---------------------------------------------------------------------------
# Execute — httpx fallback (no Selenium)
# ---------------------------------------------------------------------------

class TestHttpxFallback:
    @pytest.mark.asyncio
    async def test_uses_httpx_when_no_browser(self):
        """When browser_controller is None but URL is given, uses httpx."""
        ollama = _build_mock_ollama({
            "topic": "Test",
            "type": "article",
            "source_url": "https://example.com",
            "items": [
                {"name": "Item", "description": "Desc", "category": "misc",
                 "sub_items": [], "location": "", "requirements": [],
                 "tips": "", "related_to": []},
            ],
        })
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()

        with patch(
            "jarvis.tools.local.content_reader._httpx_fetch",
            return_value={"success": True, "html": "<html><body>Test content</body></html>", "url": "https://example.com"},
        ), patch(
            "jarvis.tools.local.content_reader._extract_readable",
            return_value="Test content",
        ), patch(
            "jarvis.tools.local.content_reader._extract_title",
            return_value="Test Page",
        ):
            tool = SavePageKnowledgeTool(
                ollama_client=ollama,
                memory_store=memory,
                relation_store=relations,
                browser_controller=None,
            )
            result = await tool.execute(
                SavePageKnowledgeArgs(url="https://example.com")
            )
            assert result.success


# ---------------------------------------------------------------------------
# Which page: jarvis.browser.current_page (user's own browser, last opened)
# ---------------------------------------------------------------------------

_SIMPLE_EXTRACTION = {
    "topic": "Borsch",
    "type": "recipe",
    "source_url": "",
    "items": [
        {"name": "Beetroot", "description": "2 pcs", "category": "ingredient",
         "sub_items": [], "location": "", "requirements": [], "tips": "",
         "related_to": []},
    ],
}


def _patch_httpx(fetched_urls: list[str], text: str = "Recipe text"):
    """Patch content_reader's fetch helpers; record which URL was fetched."""

    def fake_fetch(url, **kwargs):
        fetched_urls.append(url)
        return {"success": True, "html": "<html></html>", "url": url}

    return (
        patch("jarvis.tools.local.content_reader._httpx_fetch", side_effect=fake_fetch),
        patch("jarvis.tools.local.content_reader._extract_readable", return_value=text),
        patch("jarvis.tools.local.content_reader._extract_title", return_value="Borsch"),
    )


class TestCurrentPage:
    @pytest.mark.asyncio
    async def test_reads_page_open_in_users_browser(self, monkeypatch):
        """No Selenium, no URL: the address bar of the user's browser."""
        from jarvis.browser import current_page as cp

        monkeypatch.setattr(cp, "foreground_browser_url", lambda: "https://recipes.example/borsch")
        fetched: list[str] = []
        p1, p2, p3 = _patch_httpx(fetched)
        with p1, p2, p3:
            tool = SavePageKnowledgeTool(
                ollama_client=_build_mock_ollama(_SIMPLE_EXTRACTION),
                memory_store=_build_mock_memory_store(),
                relation_store=_build_mock_relation_store(),
            )
            result = await tool.execute(SavePageKnowledgeArgs())
        assert result.success, result.error
        assert fetched == ["https://recipes.example/borsch"]

    @pytest.mark.asyncio
    async def test_reads_page_jarvis_opened_last(self):
        """"open <site>" then "remember this page", browser bar unreadable."""
        from jarvis.browser import current_page as cp

        cp.note_opened_url("https://guides.example/bleach-100")
        fetched: list[str] = []
        p1, p2, p3 = _patch_httpx(fetched)
        with p1, p2, p3:
            tool = SavePageKnowledgeTool(
                ollama_client=_build_mock_ollama(_SIMPLE_EXTRACTION),
                memory_store=_build_mock_memory_store(),
                relation_store=_build_mock_relation_store(),
            )
            result = await tool.execute(SavePageKnowledgeArgs())
        assert result.success, result.error
        assert fetched == ["https://guides.example/bleach-100"]

    @pytest.mark.asyncio
    async def test_no_page_anywhere_gives_actionable_error(self):
        tool = SavePageKnowledgeTool(
            ollama_client=MagicMock(),
            memory_store=_build_mock_memory_store(),
        )
        result = await tool.execute(SavePageKnowledgeArgs())
        assert not result.success
        assert "browser" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_browser_provider_is_lazy_and_used(self):
        """Registration passes a provider; it is called on first use only."""
        calls: list[int] = []
        browser = _build_mock_browser(
            "Live DOM recipe text", "https://recipes.example/borsch", "Borsch"
        )

        def provider():
            calls.append(1)
            return browser

        tool = SavePageKnowledgeTool(
            ollama_client=_build_mock_ollama(_SIMPLE_EXTRACTION),
            memory_store=_build_mock_memory_store(),
            relation_store=_build_mock_relation_store(),
            browser_provider=provider,
        )
        assert calls == []
        result = await tool.execute(SavePageKnowledgeArgs())
        assert result.success, result.error
        assert calls == [1]
        browser.get_page_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_explicit_url_never_starts_selenium(self):
        """An inactive Selenium controller must not open a tab for a URL."""
        browser = _build_mock_browser("", "", "", active=False)
        fetched: list[str] = []
        p1, p2, p3 = _patch_httpx(fetched)
        with p1, p2, p3:
            tool = SavePageKnowledgeTool(
                ollama_client=_build_mock_ollama(_SIMPLE_EXTRACTION),
                memory_store=_build_mock_memory_store(),
                relation_store=_build_mock_relation_store(),
                browser_controller=browser,
            )
            result = await tool.execute(SavePageKnowledgeArgs(url="https://a.example/"))
        assert result.success, result.error
        browser.open_tab.assert_not_called()
        assert fetched == ["https://a.example/"]

    @pytest.mark.asyncio
    async def test_selenium_on_other_page_fetches_the_asked_url(self):
        browser = _build_mock_browser("Other page text", "https://other.example/", "Other")
        fetched: list[str] = []
        p1, p2, p3 = _patch_httpx(fetched)
        with p1, p2, p3:
            tool = SavePageKnowledgeTool(
                ollama_client=_build_mock_ollama(_SIMPLE_EXTRACTION),
                memory_store=_build_mock_memory_store(),
                relation_store=_build_mock_relation_store(),
                browser_controller=browser,
            )
            result = await tool.execute(SavePageKnowledgeArgs(url="https://a.example/"))
        assert result.success, result.error
        browser.open_tab.assert_not_called()
        browser.get_page_text.assert_not_called()
        assert fetched == ["https://a.example/"]

    @pytest.mark.asyncio
    async def test_selenium_on_same_page_uses_live_dom(self):
        browser = _build_mock_browser("Live text", "https://a.example/#top", "A")
        fetched: list[str] = []
        p1, p2, p3 = _patch_httpx(fetched)
        with p1, p2, p3:
            tool = SavePageKnowledgeTool(
                ollama_client=_build_mock_ollama(_SIMPLE_EXTRACTION),
                memory_store=_build_mock_memory_store(),
                relation_store=_build_mock_relation_store(),
                browser_controller=browser,
            )
            result = await tool.execute(SavePageKnowledgeArgs(url="https://a.example"))
        assert result.success, result.error
        browser.get_page_text.assert_called_once()
        assert fetched == []


# ---------------------------------------------------------------------------
# Collection path: checklist / recipe stored in a CollectionStore
# ---------------------------------------------------------------------------


def _llm_replying(*replies: dict):
    """Fake LLM answering extraction calls in order; records requests."""
    mock = MagicMock()
    queue = [json.dumps(r, ensure_ascii=False) for r in replies]
    mock.calls = []

    async def fake_stream_chat(messages, **kwargs):
        mock.calls.append(kwargs)
        yield FakeChunk(content=queue.pop(0) if queue else "{}", done=True)

    mock.stream_chat = fake_stream_chat
    return mock


def _collection_tool(store, llm, memory=None, speak=None, announce=None):
    browser = _build_mock_browser(
        "Крылья феникса\nкупить 10 видов\nКороль душ", "https://steam.example/g", "Steam guide",
    )
    return SavePageKnowledgeTool(
        ollama_client=llm,
        memory_store=memory or _build_mock_memory_store(),
        relation_store=_build_mock_relation_store(),
        browser_controller=browser,
        collection_store=store,
        on_speak=speak,
        announce=announce,
    )


class TestCollectionPath:
    @pytest.fixture
    def store(self, tmp_path):
        from jarvis.memory.collections import CollectionStore

        return CollectionStore(tmp_path / "collections.db")

    @pytest.mark.asyncio
    async def test_checklist_saved_with_items(self, store):
        llm = _llm_replying({
            "kind": "checklist", "title": "Bleach: Rebirth of Souls", "servings": None,
            "items": [
                {"r": "task", "n": "Крылья феникса", "d": "купить 10 видов"},
                {"r": "task", "n": "Король душ"},
            ],
        })
        spoken: list[str] = []

        async def speak(text):
            spoken.append(text)

        result = await _collection_tool(store, llm, speak=speak).execute(SavePageKnowledgeArgs())
        assert result.success, result.error
        assert "checklist" in output_text(result)
        assert "2 items" in output_text(result)
        col = store.find("")
        assert col is not None
        assert [i.name for i in col.items] == ["Крылья феникса", "Король душ"]
        assert col.source_url == "https://steam.example/g"
        assert spoken, "the user is told that reading takes a while"
        assert "format" in llm.calls[0]  # structured output was requested

    @pytest.mark.asyncio
    async def test_summary_fact_written_to_memory(self, store):
        added: list[tuple[str, list]] = []
        memory = MagicMock()

        async def fake_add(text, *, category="general", source="user",
                           language="en", tags=None, is_permanent=False):
            added.append((text, tags or []))
            return "f1"

        memory.add = fake_add
        llm = _llm_replying({
            "kind": "checklist", "title": "Bleach", "items": [{"r": "task", "n": "A"}],
        })
        await _collection_tool(store, llm, memory=memory).execute(SavePageKnowledgeArgs())
        assert len(added) == 1
        text, tags = added[0]
        assert "Bleach" in text
        assert any(t.startswith("collection:") for t in tags)

    @pytest.mark.asyncio
    async def test_recipe_writes_shopping_note(self, store):
        from jarvis.core.request_context import current_user_transcription
        from jarvis.tools.local.notes_store import list_notes

        llm = _llm_replying({
            "kind": "recipe", "title": "Борщ", "servings": 4,
            "items": [
                {"r": "ingredient", "n": "Свёкла", "q": 2, "u": "шт"},
                {"r": "ingredient", "n": "Соль", "q": None, "u": ""},
                {"r": "step", "n": "Сварить бульон"},
            ],
        })
        token = current_user_transcription.set("сохрани этот рецепт")
        try:
            result = await _collection_tool(store, llm).execute(SavePageKnowledgeArgs())
        finally:
            current_user_transcription.reset(token)
        assert result.success, result.error
        assert "2 ingredients, 1 steps" in output_text(result)
        notes = [n for n in list_notes() if n.title == "Список продуктов: Борщ"]
        assert len(notes) == 1
        assert "- [ ] Свёкла — 2 шт" in notes[0].content
        assert "- [ ] Соль" in notes[0].content

    @pytest.mark.asyncio
    async def test_article_falls_back_to_facts(self, store):
        llm = _llm_replying(
            {"kind": "article", "title": "News", "items": []},
            {"topic": "News", "type": "article", "items": [
                {"name": "Point", "description": "d", "category": "c"},
            ]},
        )
        result = await _collection_tool(store, llm).execute(SavePageKnowledgeArgs())
        assert result.success, result.error
        assert "facts" in output_text(result)
        assert store.list_all() == []

    @pytest.mark.asyncio
    async def test_resaving_keeps_ticks(self, store):
        reply = {"kind": "checklist", "title": "Bleach", "items": [
            {"r": "task", "n": "Крылья феникса"}, {"r": "task", "n": "Король душ"},
        ]}
        await _collection_tool(store, _llm_replying(reply)).execute(SavePageKnowledgeArgs())
        col = store.find("")
        assert col is not None
        store.set_done(col.id, [col.items[1].id])
        result = await _collection_tool(store, _llm_replying(reply)).execute(
            SavePageKnowledgeArgs()
        )
        assert "1 already marked done" in output_text(result)
        assert len(store.list_all()) == 1

    @pytest.mark.asyncio
    async def test_resaving_replaces_the_summary_fact(self, store, tmp_path):
        # Live memory held both "(78 items)" and "(36 items)" for one page.
        from jarvis.memory.store import MemoryStore

        memory = MemoryStore(tmp_path / "memory.db")
        await memory.open()
        long_list = {"kind": "checklist", "title": "Bleach", "items": [
            {"r": "task", "n": "Крылья феникса"}, {"r": "task", "n": "Король душ"},
            {"r": "task", "n": "Панцирь черепахи"},
        ]}
        short_list = {"kind": "checklist", "title": "Bleach", "items": [
            {"r": "task", "n": "Крылья феникса"}, {"r": "task", "n": "Король душ"},
        ]}
        for reply in (long_list, short_list):
            await _collection_tool(store, _llm_replying(reply), memory=memory).execute(
                SavePageKnowledgeArgs()
            )
        col = store.find("")
        assert col is not None
        facts = await memory.search("Saved", tag=f"collection:{col.id}", limit=10)
        assert [f.text for f in facts] == [
            'Saved checklist "Bleach" from https://steam.example/g (2 items). '
            'Ask "what is left" / "shopping list" to use it.'
        ]

    @pytest.mark.asyncio
    async def test_background_save_answers_at_once_and_announces(self, store):
        # Measured live: ~40 s of extraction for a 36-item guide. With an
        # announcer the tool returns at once and says the result later.
        from jarvis.core.request_context import current_user_transcription

        announced: list[str] = []
        llm = _llm_replying({"kind": "checklist", "title": "Bleach", "items": [
            {"r": "task", "n": "Крылья феникса"}, {"r": "task", "n": "Король душ"},
        ]})
        tool = _collection_tool(store, llm, announce=announced.append)
        token = current_user_transcription.set("запомни эту страницу")
        try:
            result = await tool.execute(SavePageKnowledgeArgs())
        finally:
            current_user_transcription.reset(token)
        assert result.success
        assert "NOT saved yet" in output_text(result)
        await asyncio.gather(*tool._background)
        assert announced == ["Сохранил «Bleach», пунктов: 2, сэр."]
        col = store.find("")
        assert col is not None and len(col.items) == 2

    @pytest.mark.asyncio
    async def test_background_save_announces_failure(self, store):
        announced: list[str] = []
        llm = _llm_replying({"kind": "checklist", "title": "Bleach", "items": [
            {"r": "task", "n": "Крылья феникса"},
        ]})
        tool = _collection_tool(store, llm, announce=announced.append)
        with patch.object(store, "save", side_effect=RuntimeError("disk full")):
            result = await tool.execute(SavePageKnowledgeArgs())
            await asyncio.gather(*tool._background)
        assert result.success
        assert announced == ["I could not read that page, sir."]

    @pytest.mark.asyncio
    async def test_second_request_for_the_same_page_starts_nothing(self, store):
        # Live: said twice, two extractions of one page shared the GPU.
        announced: list[str] = []
        llm = _llm_replying(
            {"kind": "checklist", "title": "Bleach", "items": [{"r": "task", "n": "A"}]},
        )
        tool = _collection_tool(store, llm, announce=announced.append)
        first = await tool.execute(SavePageKnowledgeArgs())
        second = await tool.execute(SavePageKnowledgeArgs())
        assert "NOT saved yet" in output_text(first)
        assert "already being read" in output_text(second)
        assert len(tool._background) == 1
        await asyncio.gather(*tool._background)
        assert len(announced) == 1

    @pytest.mark.asyncio
    async def test_without_an_announcer_the_save_waits(self, store):
        llm = _llm_replying({"kind": "checklist", "title": "Bleach", "items": [
            {"r": "task", "n": "Крылья феникса"},
        ]})
        result = await _collection_tool(store, llm).execute(SavePageKnowledgeArgs())
        assert "1 items" in output_text(result)

    @pytest.mark.asyncio
    async def test_speak_failure_is_not_fatal(self, store):
        async def broken(text):
            raise RuntimeError("tts down")

        llm = _llm_replying({"kind": "list", "title": "L", "items": [{"r": "item", "n": "A"}]})
        result = await _collection_tool(store, llm, speak=broken).execute(SavePageKnowledgeArgs())
        assert result.success, result.error


# ---------------------------------------------------------------------------
# What text is read: main content, not the site around it
# ---------------------------------------------------------------------------

_GUIDE_HTML = (
    "<html><head><title>Steam :: Guide</title></head><body>"
    "<div id='global_header'><a>Магазин</a><a>Сообщество</a></div>"
    "<div class='workshopItemControls'><span>Наградить</span><span>Поделиться</span></div>"
    "<div class='guide'>"
    + "".join(
        f"<div class='subSectionTitle'>Достижение {i}</div>"
        f"<div class='subSectionDesc'>Как получить достижение номер {i} в игре</div>"
        for i in range(20)
    )
    + "</div><div id='footer'>© Valve</div></body></html>"
)


class TestMainContent:
    def test_httpx_path_sends_main_content(self):
        with patch(
            "jarvis.tools.local.content_reader._httpx_fetch",
            return_value={"success": True, "html": _GUIDE_HTML, "url": "https://x"},
        ):
            text, url, title, err = SavePageKnowledgeTool._fetch_with_httpx("https://x")
        assert err is None
        assert "## Достижение 3" in text
        assert "Как получить достижение номер 3" in text
        assert "Наградить" not in text and "Магазин" not in text and "Valve" not in text
        assert title == "Steam :: Guide"

    def test_cleaning_that_removes_everything_falls_back_to_full_text(self):
        html = (
            "<html><body><div class='comments'>"
            + "<p>Единственный текст страницы, но в блоке комментариев. </p>" * 30
            + "</div></body></html>"
        )
        with patch(
            "jarvis.tools.local.content_reader._httpx_fetch",
            return_value={"success": True, "html": html, "url": "https://x"},
        ):
            text, _url, _title, err = SavePageKnowledgeTool._fetch_with_httpx("https://x")
        assert err is None
        assert "Единственный текст страницы" in text

    @pytest.mark.asyncio
    async def test_selenium_path_uses_page_source(self):
        browser = _build_mock_browser("Наградить Магазин body text", "https://x", "Guide")
        browser.get_page_source = MagicMock(return_value={"success": True, "html": _GUIDE_HTML})
        tool = SavePageKnowledgeTool(
            ollama_client=MagicMock(), memory_store=_build_mock_memory_store(),
            browser_controller=browser,
        )
        text, _url, _title, err = await tool._try_selenium("")
        assert err is None
        assert "## Достижение 0" in text
        assert "Наградить" not in text
        browser.get_page_text.assert_not_called()


# ---------------------------------------------------------------------------
# B-P2-10: parts that could not be read are announced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_background_save_says_some_parts_were_not_read(tmp_path):
    from unittest.mock import AsyncMock

    from jarvis.core.request_context import current_user_transcription
    from jarvis.memory.collections import CollectionStore, NewItem
    from jarvis.memory.page_extractor import ExtractedPage

    store = CollectionStore(tmp_path / "collections.db")
    page = ExtractedPage(
        kind="checklist", title="Uncharted",
        items=[NewItem(name="A", role="task"), NewItem(name="B", role="task")],
        unread_parts=[6], total_parts=9,
    )
    announced: list[str] = []
    tool = _collection_tool(store, _llm_replying(), announce=announced.append)
    token = current_user_transcription.set("запомни эту страницу")
    try:
        with patch(
            "jarvis.memory.page_extractor.PageExtractor.extract",
            new=AsyncMock(return_value=page),
        ):
            await tool.execute(SavePageKnowledgeArgs())
            await asyncio.gather(*tool._background)
    finally:
        current_user_transcription.reset(token)
    assert announced == [
        "Сохранил «Uncharted», пунктов: 2, сэр. "
        "Не прочитано частей страницы: 1 из 9, список может быть неполным."
    ]


@pytest.mark.asyncio
async def test_foreground_save_tells_the_model_some_parts_were_not_read(tmp_path):
    from unittest.mock import AsyncMock

    from jarvis.memory.collections import CollectionStore, NewItem
    from jarvis.memory.page_extractor import ExtractedPage

    store = CollectionStore(tmp_path / "collections.db")
    page = ExtractedPage(
        kind="checklist", title="Uncharted", items=[NewItem(name="A", role="task")],
        unread_parts=[2, 6], total_parts=9,
    )
    tool = _collection_tool(store, _llm_replying())
    with patch(
        "jarvis.memory.page_extractor.PageExtractor.extract",
        new=AsyncMock(return_value=page),
    ):
        result = await tool.execute(SavePageKnowledgeArgs())
    assert "2 of 9 parts of the page could not be read" in output_text(result)


# ---------------------------------------------------------------------------
# B-P2-8: a page still being read is stopped on shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_stops_a_background_read(tmp_path, caplog):
    """Live: after "shutdown" the read kept calling the model being
    unloaded ("LLM call failed on part 2/9")."""
    import logging

    from jarvis.memory.collections import CollectionStore

    started = asyncio.Event()
    calls: list[int] = []

    class HangingLLM:
        async def stream_chat(self, messages, **kwargs):
            calls.append(1)
            started.set()
            await asyncio.Event().wait()  # a part that takes "forever"
            yield FakeChunk(content="{}", done=True)  # pragma: no cover

    store = CollectionStore(tmp_path / "collections.db")
    announced: list[str] = []
    tool = _collection_tool(store, HangingLLM(), announce=announced.append)
    await tool.execute(SavePageKnowledgeArgs())
    await asyncio.wait_for(started.wait(), timeout=2)
    (task,) = tool._background

    with caplog.at_level(logging.WARNING, logger="jarvis.tools.local.web_knowledge"):
        await asyncio.wait_for(tool.cancel_background_work(), timeout=2)

    assert task.cancelled()
    assert tool._background == set()
    assert calls == [1]  # no further parts sent
    assert announced == []  # nothing saved, nothing announced as saved
    assert store.list_all() == []
    assert "stopped reading https://steam.example/g; the page is NOT saved" in caplog.text


@pytest.mark.asyncio
async def test_cancel_with_nothing_running_is_a_no_op(tmp_path):
    from jarvis.memory.collections import CollectionStore

    tool = _collection_tool(CollectionStore(tmp_path / "c.db"), _llm_replying())
    await tool.cancel_background_work()


@pytest.mark.asyncio
async def test_registry_cancels_background_work_of_every_tool():
    from jarvis.core.config import ToolsConfig
    from jarvis.tools.registry import ToolRegistry

    cancelled: list[str] = []

    class _Tool:
        def __init__(self, name, fail=False):
            self.name = name
            self.fail = fail

        async def cancel_background_work(self):
            if self.fail:
                raise RuntimeError("boom")
            cancelled.append(self.name)

    class _Plain:
        name = "plain"

    registry = ToolRegistry(ToolsConfig())
    for tool in (_Tool("a", fail=True), _Plain(), _Tool("b")):
        registry.register(tool)  # type: ignore[arg-type]
    await registry.cancel_background_work()
    assert cancelled == ["b"]  # a failing tool does not stop the others


def test_shutdown_cancels_background_tool_work_before_unloading():
    from pathlib import Path

    import jarvis.app

    source = Path(jarvis.app.__file__).read_text(encoding="utf-8")
    cancel = source.index("await registry.cancel_background_work()")
    assert cancel < source.index("await mcp_manager.shutdown()")
    assert cancel < source.index("await lm.unload_all()", cancel)


# ---------------------------------------------------------------------------
# B-P1-10: progress of a background read
# ---------------------------------------------------------------------------


def test_short_title_keeps_the_guide_name():
    from jarvis.tools.local.web_knowledge import _short_title

    tab = "Сообщество Steam :: Руководство :: Все достижения и как их получить (100%)"
    assert _short_title(tab, "u") == "Все достижения и как их получить (100%)"
    assert _short_title("", "https://x.example/g") == "https://x.example/g"
    assert len(_short_title("x" * 200, "u")) == 60


@pytest.mark.asyncio
async def test_background_read_reports_progress_and_answers_how_is_it_going(tmp_path):
    from jarvis.core.events import BackgroundProgress
    from jarvis.core.request_context import current_user_transcription
    from jarvis.memory.collections import CollectionStore
    from jarvis.tools.local.web_knowledge import PageReadingStatusTool

    release = asyncio.Event()
    started = asyncio.Event()
    reply = json.dumps({"kind": "checklist", "title": "Bleach", "items": [
        {"r": "task", "n": "Крылья феникса"}, {"r": "task", "n": "Король душ"},
    ]}, ensure_ascii=False)

    class SlowLLM:
        async def stream_chat(self, messages, **kwargs):
            started.set()
            await release.wait()
            yield FakeChunk(content=reply, done=True)

    events: list[BackgroundProgress] = []
    store = CollectionStore(tmp_path / "collections.db")
    tool = _collection_tool(store, SlowLLM(), announce=lambda text: None)
    tool._on_progress = events.append
    status_tool = PageReadingStatusTool(tool)

    token = current_user_transcription.set("запомни эту страницу")
    try:
        await tool.execute(SavePageKnowledgeArgs())
    finally:
        current_user_transcription.reset(token)
    await asyncio.wait_for(started.wait(), timeout=2)

    token = current_user_transcription.set("как там страница")
    try:
        answer = output_text(await status_tool.execute(EmptyArgs()))
    finally:
        current_user_transcription.reset(token)
    assert answer == "Читаю «Steam guide»: часть 1 из 1."

    release.set()
    await asyncio.gather(*tool._background)

    key = "save-page:https://steam.example/g"
    assert [(e.key, e.status, e.notice) for e in events] == [
        (key, "Читаю «Steam guide»…", "Читаю «Steam guide» в фоне. Скажу, когда сохраню."),
        (key, "Читаю «Steam guide»: часть 1 из 1", None),
        (key, None, "Сохранил «Bleach», пунктов: 2, сэр."),
    ]
    assert tool._reading == {}


@pytest.mark.asyncio
async def test_status_when_nothing_is_being_read(tmp_path):
    from jarvis.core.request_context import current_user_transcription
    from jarvis.memory.collections import CollectionStore
    from jarvis.tools.local.web_knowledge import PageReadingStatusTool

    tool = _collection_tool(CollectionStore(tmp_path / "c.db"), _llm_replying())
    token = current_user_transcription.set("як там сторінка")
    try:
        answer = output_text(await PageReadingStatusTool(tool).execute(EmptyArgs()))
    finally:
        current_user_transcription.reset(token)
    assert answer == "Зараз жодної сторінки не читаю, сер."


@pytest.mark.asyncio
async def test_cancelled_read_clears_the_tray_status(tmp_path):
    from jarvis.core.events import BackgroundProgress
    from jarvis.memory.collections import CollectionStore

    started = asyncio.Event()

    class HangingLLM:
        async def stream_chat(self, messages, **kwargs):
            started.set()
            await asyncio.Event().wait()
            yield FakeChunk(content="{}", done=True)  # pragma: no cover

    events: list[BackgroundProgress] = []
    tool = _collection_tool(
        CollectionStore(tmp_path / "c.db"), HangingLLM(), announce=lambda text: None
    )
    tool._on_progress = events.append
    await tool.execute(SavePageKnowledgeArgs())
    await asyncio.wait_for(started.wait(), timeout=2)
    await tool.cancel_background_work()
    assert events[-1].status is None and events[-1].notice is None
    assert tool._reading == {}


@pytest.mark.parametrize(
    "phrase",
    ["как там страница", "что там со страницей", "як там сторінка", "how's the page going"],
)
def test_how_is_the_page_routes_to_the_status_tool(phrase):
    import re

    from jarvis.tools.catalogue import voice_pattern_catalogue

    for tool_name, vp in voice_pattern_catalogue():
        if re.match(vp.regex, phrase, re.IGNORECASE):
            assert tool_name == "page_reading_status"
            return
    raise AssertionError(f"no pattern for {phrase!r}")


# ---------------------------------------------------------------------------
# B-P1-11: a long guide is read to the end
# ---------------------------------------------------------------------------


def test_long_guide_is_read_past_40000_chars():
    """Live: an 85 139-character Uncharted guide was cut to its first 40 000
    characters, so half of the achievements were never read."""
    from jarvis.tools.local.web_knowledge import _main_text

    rows = "".join(
        f"<h2>Достижение {i}</h2><p>Как получить достижение номер {i}: "
        + "пройдите главу и найдите сокровище. " * 4 + "</p>"
        for i in range(500)
    )
    text = _main_text(f"<html><body><article>{rows}</article></body></html>")
    assert len(text) > 85_000
    assert "## Достижение 499" in text


def test_one_page_limit_for_reading_and_extraction():
    from jarvis.memory import page_extractor
    from jarvis.tools.local import web_knowledge

    assert web_knowledge._MAX_PAGE_CHARS == page_extractor.MAX_PAGE_CHARS >= 100_000
