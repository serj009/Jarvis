"""Tests for jarvis.memory.knowledge_extractor — KnowledgeExtractor.

Unit tests for JSON parsing, relation creation, tagging, and fallback.
All LLM, memory store, and relation store dependencies are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from jarvis.memory.knowledge_extractor import (
    EXTRACTION_PROMPT,
    MAX_PAGE_CHARS,
    KnowledgeExtractor,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

class FakeChunk:
    def __init__(self, content: str = "", done: bool = False):
        self.content = content
        self.done = done


def _build_mock_ollama(response_data: dict):
    mock = MagicMock()
    response_json = json.dumps(response_data)

    async def fake_stream_chat(messages, **kwargs):
        yield FakeChunk(content=response_json, done=True)

    mock.stream_chat = fake_stream_chat
    return mock


def _build_mock_ollama_text(raw_text: str):
    """Returns a mock that yields arbitrary text (may or may not be JSON)."""
    mock = MagicMock()

    async def fake_stream_chat(messages, **kwargs):
        yield FakeChunk(content=raw_text, done=True)

    mock.stream_chat = fake_stream_chat
    return mock


def _build_mock_memory_store():
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
    mock = MagicMock()
    mock.add_relation = MagicMock(return_value=1)
    return mock


SAMPLE_STRUCTURED_RESPONSE = {
    "topic": "Test Topic",
    "type": "game_guide",
    "source_url": "https://example.com",
    "items": [
        {
            "name": "Item Alpha",
            "description": "First item description",
            "category": "quest",
            "sub_items": ["sub1", "sub2"],
            "location": "Forest",
            "requirements": ["Level 10", "Sword"],
            "tips": "Be careful",
            "related_to": ["Item Beta"],
        },
        {
            "name": "Item Beta",
            "description": "Second item description",
            "category": "quest",
            "sub_items": [],
            "location": "Castle",
            "requirements": [],
            "tips": "",
            "related_to": [],
        },
    ],
}


# ---------------------------------------------------------------------------
# Test: extract_and_save with structured LLM response
# ---------------------------------------------------------------------------

class TestExtractAndSave:
    @pytest.mark.asyncio
    async def test_structured_extraction(self):
        ollama = _build_mock_ollama(SAMPLE_STRUCTURED_RESPONSE)
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()

        extractor = KnowledgeExtractor(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
        )

        result = await extractor.extract_and_save(
            "Some page text", "https://example.com", "Test Page"
        )

        assert result["topic"] == "Test Topic"
        assert result["type"] == "game_guide"
        # 1 topic + 2 items = 3 facts
        assert result["items_saved"] == 3
        assert len(result["fact_ids"]) == 3

    @pytest.mark.asyncio
    async def test_relations_created(self):
        ollama = _build_mock_ollama(SAMPLE_STRUCTURED_RESPONSE)
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()

        extractor = KnowledgeExtractor(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
        )

        result = await extractor.extract_and_save(
            "Page text", "https://example.com", "Test"
        )

        # 2 belongs_to (Alpha → topic, Beta → topic) + 1 related_to (Alpha → Beta)
        assert result["relations_created"] == 3
        assert relations.add_relation.call_count == 3

    @pytest.mark.asyncio
    async def test_belongs_to_relation_type(self):
        ollama = _build_mock_ollama(SAMPLE_STRUCTURED_RESPONSE)
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()

        extractor = KnowledgeExtractor(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
        )

        await extractor.extract_and_save("text", "https://example.com", "Test")

        # Check that belongs_to was used
        call_args_list = relations.add_relation.call_args_list
        relation_types = [c[0][2] for c in call_args_list]
        assert "belongs_to" in relation_types

    @pytest.mark.asyncio
    async def test_related_to_cross_relation(self):
        ollama = _build_mock_ollama(SAMPLE_STRUCTURED_RESPONSE)
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()

        extractor = KnowledgeExtractor(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
        )

        await extractor.extract_and_save("text", "https://example.com", "Test")

        call_args_list = relations.add_relation.call_args_list
        relation_types = [c[0][2] for c in call_args_list]
        assert "related_to" in relation_types


# ---------------------------------------------------------------------------
# Test: no relation store → graceful (no crash)
# ---------------------------------------------------------------------------

class TestNoRelationStore:
    @pytest.mark.asyncio
    async def test_works_without_relation_store(self):
        ollama = _build_mock_ollama(SAMPLE_STRUCTURED_RESPONSE)
        memory = _build_mock_memory_store()

        extractor = KnowledgeExtractor(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=None,
        )

        result = await extractor.extract_and_save(
            "text", "https://example.com", "Test"
        )

        assert result["items_saved"] == 3
        assert result["relations_created"] == 0


# ---------------------------------------------------------------------------
# Test: no LLM client
# ---------------------------------------------------------------------------

class TestNoLLM:
    @pytest.mark.asyncio
    async def test_returns_error_without_ollama(self):
        memory = _build_mock_memory_store()

        extractor = KnowledgeExtractor(
            ollama_client=None,
            memory_store=memory,
        )

        result = await extractor.extract_and_save(
            "text", "https://example.com", "Test"
        )

        assert "error" in result
        assert result["items_saved"] == 0


# ---------------------------------------------------------------------------
# Test: no memory store
# ---------------------------------------------------------------------------

class TestNoMemoryStore:
    @pytest.mark.asyncio
    async def test_returns_error_without_memory(self):
        ollama = _build_mock_ollama(SAMPLE_STRUCTURED_RESPONSE)

        extractor = KnowledgeExtractor(
            ollama_client=ollama,
            memory_store=None,
        )

        result = await extractor.extract_and_save(
            "text", "https://example.com", "Test"
        )

        assert "error" in result
        assert result["items_saved"] == 0


# ---------------------------------------------------------------------------
# Test: JSON parsing — various formats
# ---------------------------------------------------------------------------

class TestJsonParsing:
    def test_direct_json(self):
        text = json.dumps({"topic": "Test", "items": []})
        result = KnowledgeExtractor._parse_json_response(text)
        assert result is not None
        assert result["topic"] == "Test"

    def test_json_in_markdown_fences(self):
        text = '```json\n{"topic": "Test", "items": []}\n```'
        result = KnowledgeExtractor._parse_json_response(text)
        assert result is not None
        assert result["topic"] == "Test"

    def test_json_with_preamble(self):
        text = 'Here is the extracted data:\n{"topic": "Test", "items": []}'
        result = KnowledgeExtractor._parse_json_response(text)
        assert result is not None
        assert result["topic"] == "Test"

    def test_completely_invalid(self):
        text = "This is not JSON at all"
        result = KnowledgeExtractor._parse_json_response(text)
        assert result is None

    def test_empty_string(self):
        result = KnowledgeExtractor._parse_json_response("")
        assert result is None

    def test_json_with_trailing_text(self):
        text = '{"topic": "Test", "items": []}\n\nI hope this helps!'
        result = KnowledgeExtractor._parse_json_response(text)
        assert result is not None
        assert result["topic"] == "Test"


# ---------------------------------------------------------------------------
# Test: LLM invalid JSON → fallback to plain text
# ---------------------------------------------------------------------------

class TestInvalidJsonFallback:
    @pytest.mark.asyncio
    async def test_fallback_saves_plain_text(self):
        ollama = _build_mock_ollama_text("Not valid JSON at all {{{")
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()

        extractor = KnowledgeExtractor(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
        )

        result = await extractor.extract_and_save(
            "Some plain text content",
            "https://example.com/plain",
            "Plain Page",
        )

        assert result.get("fallback") is True
        assert result["items_saved"] == 1
        assert result["relations_created"] == 0


# ---------------------------------------------------------------------------
# Test: text truncation
# ---------------------------------------------------------------------------

class TestTextTruncation:
    def test_short_text_not_truncated(self):
        text = "Short text"
        result = KnowledgeExtractor._prepare_text(text)
        assert result == text
        assert "truncated" not in result

    def test_long_text_truncated(self):
        text = "A" * 15_000
        result = KnowledgeExtractor._prepare_text(text)
        assert len(result) < len(text)
        assert "truncated" in result
        assert result.startswith("A" * 100)  # Begins with original content

    def test_exactly_at_limit_not_truncated(self):
        text = "B" * MAX_PAGE_CHARS
        result = KnowledgeExtractor._prepare_text(text)
        assert result == text

    def test_one_over_limit_truncated(self):
        text = "C" * (MAX_PAGE_CHARS + 1)
        result = KnowledgeExtractor._prepare_text(text)
        assert len(result) < len(text)
        assert "truncated" in result


# ---------------------------------------------------------------------------
# Test: empty items list from LLM
# ---------------------------------------------------------------------------

class TestEmptyItems:
    @pytest.mark.asyncio
    async def test_empty_items_saves_only_topic(self):
        ollama = _build_mock_ollama({
            "topic": "Empty Page",
            "type": "article",
            "source_url": "https://example.com",
            "items": [],
        })
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()

        extractor = KnowledgeExtractor(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
        )

        result = await extractor.extract_and_save(
            "Some text", "https://example.com", "Test"
        )

        assert result["items_saved"] == 1  # Only the topic fact
        assert result["relations_created"] == 0


# ---------------------------------------------------------------------------
# Test: items with no name are skipped
# ---------------------------------------------------------------------------

class TestItemsWithNoName:
    @pytest.mark.asyncio
    async def test_nameless_items_skipped(self):
        ollama = _build_mock_ollama({
            "topic": "Test",
            "type": "article",
            "source_url": "https://example.com",
            "items": [
                {"name": "", "description": "No name", "category": "misc",
                 "sub_items": [], "location": "", "requirements": [],
                 "tips": "", "related_to": []},
                {"name": "Valid Item", "description": "Has a name",
                 "category": "misc", "sub_items": [], "location": "",
                 "requirements": [], "tips": "", "related_to": []},
            ],
        })
        memory = _build_mock_memory_store()
        relations = _build_mock_relation_store()

        extractor = KnowledgeExtractor(
            ollama_client=ollama,
            memory_store=memory,
            relation_store=relations,
        )

        result = await extractor.extract_and_save(
            "text", "https://example.com", "Test"
        )

        # 1 topic + 1 valid item (nameless skipped)
        assert result["items_saved"] == 2


# ---------------------------------------------------------------------------
# Test: extraction prompt format
# ---------------------------------------------------------------------------

class TestPromptFormat:
    def test_prompt_contains_placeholders(self):
        assert "{page_text}" in EXTRACTION_PROMPT
        assert "{source_url}" in EXTRACTION_PROMPT
        assert "{page_title}" in EXTRACTION_PROMPT

    def test_prompt_asks_for_json(self):
        assert "JSON" in EXTRACTION_PROMPT
        assert '"topic"' in EXTRACTION_PROMPT
        assert '"items"' in EXTRACTION_PROMPT


# ---------------------------------------------------------------------------
# Test: multi-chunk LLM response
# ---------------------------------------------------------------------------

class TestMultiChunkResponse:
    @pytest.mark.asyncio
    async def test_accumulates_chunks(self):
        mock = MagicMock()
        data = {"topic": "Chunked", "type": "article",
                "source_url": "https://x.com", "items": []}
        full_json = json.dumps(data)
        half = len(full_json) // 2

        async def fake_stream_chat(messages, **kwargs):
            yield FakeChunk(content=full_json[:half], done=False)
            yield FakeChunk(content=full_json[half:], done=True)

        mock.stream_chat = fake_stream_chat

        extractor = KnowledgeExtractor(
            ollama_client=mock,
            memory_store=_build_mock_memory_store(),
            relation_store=_build_mock_relation_store(),
        )

        result = await extractor.extract_and_save("text", "https://x.com", "T")
        assert result["topic"] == "Chunked"
