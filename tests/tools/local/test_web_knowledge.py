"""Tests for jarvis.tools.local.web_knowledge — SavePageKnowledgeTool.

Fully mocked: no real browser, no real LLM, no real database.
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from jarvis.tools.local.web_knowledge import (
    SavePageKnowledgeArgs,
    SavePageKnowledgeTool,
)
from jarvis.tools.registry import VoicePattern

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
