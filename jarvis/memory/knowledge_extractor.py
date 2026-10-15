"""KnowledgeExtractor: structured knowledge extraction from web pages.

Sends page text to a local LLM (Ollama) with an extraction prompt that
returns structured JSON.  Each extracted item becomes a separate fact in
MemoryStore with proper tags, and relations between facts are created in
RelationStore.

Design decisions:

- Universal extraction prompt: the LLM is told the content could be about
  *anything* (games, recipes, tech, collections, etc.).  The prompt asks
  for a schema-flexible JSON response: ``topic``, ``type``, ``items[]``
  where each item has ``name``, ``description``, optional ``sub_items``,
  ``location``, ``requirements``, ``tips``, and ``related_to``.

- Chunking: pages over ``MAX_PAGE_CHARS`` are truncated to the first
  ``TRUNCATED_CHARS`` characters + a "…[truncated]" hint so the LLM knows
  it's incomplete.

- Graceful degradation: LLM returning invalid JSON is retried once.  If
  the retry also fails, the page is saved as a single plain-text fact so
  information is never silently lost.

- All dependencies (ollama_client, memory_store, relation_store) are
  optional and checked at call time.  Tests pass None without side effects.

Usage:
    extractor = KnowledgeExtractor(ollama_client, memory_store, relation_store)
    result = await extractor.extract_and_save(text, url, title)
    # -> {"topic": "...", "type": "...", "items_saved": 5, "relations_created": 8}
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_PAGE_CHARS: int = 10_000
TRUNCATED_CHARS: int = 8_000

EXTRACTION_PROMPT = """\
You are a knowledge extraction AI. Extract structured data from the following web page content.

Rules:
1. Identify the TOPIC (what this page is about)
2. Identify the TYPE (game_guide, collection, recipe, tutorial, reference, list, article)
3. Extract individual ITEMS — each item is a distinct piece of knowledge
4. For each item, preserve:
   - name/title
   - description
   - any sub-items (requirements, ingredients, steps, locations)
   - relationships to other items on the page
5. Keep the structure — don't flatten lists into text
6. Be universal — this could be about games, cooking, science, anything

Respond ONLY with valid JSON:
{{
  "topic": "...",
  "type": "...",
  "source_url": "{source_url}",
  "items": [
    {{
      "name": "...",
      "description": "...",
      "category": "...",
      "sub_items": [],
      "location": "",
      "requirements": [],
      "tips": "",
      "related_to": []
    }}
  ]
}}

Page title: {page_title}
Page content:
{page_text}
"""


# ---------------------------------------------------------------------------
# KnowledgeExtractor
# ---------------------------------------------------------------------------

class KnowledgeExtractor:
    """Structured knowledge extraction from web page text via LLM.

    All dependencies are optional — when None, the corresponding
    operation is skipped and the caller receives a partial result or
    an error dict.
    """

    def __init__(
        self,
        ollama_client: Any = None,
        memory_store: Any = None,
        relation_store: Any = None,
        auto_tagger: Any = None,
    ) -> None:
        self._ollama = ollama_client
        self._memory_store = memory_store
        self._relation_store = relation_store
        self._auto_tagger = auto_tagger

    # -- public API ---------------------------------------------------------

    async def extract_and_save(
        self,
        text: str,
        url: str,
        title: str,
    ) -> dict[str, Any]:
        """Extract structured knowledge from *text* and persist it.

        Returns a summary dict:
            topic, type, items_saved, relations_created, fact_ids
        """
        if not self._ollama:
            return {"error": "No LLM client available", "items_saved": 0}
        if not self._memory_store:
            return {"error": "No memory store available", "items_saved": 0}

        # 1. Truncate if needed
        page_text = self._prepare_text(text)

        # 2. Build prompt
        prompt = EXTRACTION_PROMPT.format(
            source_url=url,
            page_title=title or "Unknown",
            page_text=page_text,
        )

        # 3. Call LLM (with one retry on bad JSON)
        parsed = await self._call_llm(prompt)
        if parsed is None:
            # Retry once
            log.warning("LLM returned invalid JSON, retrying once")
            parsed = await self._call_llm(prompt)

        if parsed is None:
            # Fallback: save as single plain-text fact
            return await self._save_plain_text_fallback(text, url, title)

        # 4. Save structured facts
        return await self._save_structured(parsed, url, title)

    # -- text preparation ---------------------------------------------------

    @staticmethod
    def _prepare_text(text: str) -> str:
        """Truncate long text while preserving structure hints."""
        if len(text) <= MAX_PAGE_CHARS:
            return text
        return text[:TRUNCATED_CHARS] + "\n\n…[content truncated — extract what you can see]"

    # -- LLM interaction ----------------------------------------------------

    async def _call_llm(self, prompt: str) -> dict | None:
        """Send prompt to Ollama and parse the JSON response.

        Returns the parsed dict on success, None on any failure.
        """
        messages = [{"role": "user", "content": prompt}]
        full_response = ""
        try:
            async for chunk in self._ollama.stream_chat(messages):
                full_response += chunk.content
                if chunk.done:
                    break
        except Exception:
            log.exception("LLM call failed during knowledge extraction")
            return None

        return self._parse_json_response(full_response)

    @staticmethod
    def _parse_json_response(text: str) -> dict | None:
        """Extract and parse JSON from LLM response text."""
        text = text.strip()

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON block from markdown fences
        import re
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try to find first { ... } block
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start : brace_end + 1])
            except json.JSONDecodeError:
                pass

        log.warning("Could not parse JSON from LLM response (%d chars)", len(text))
        return None

    # -- fact persistence ---------------------------------------------------

    async def _save_structured(
        self,
        data: dict,
        url: str,
        title: str,
    ) -> dict[str, Any]:
        """Save parsed structured data as facts + relations."""
        topic = data.get("topic", title or "Unknown topic")
        content_type = data.get("type", "article")
        items = data.get("items") or []

        fact_ids: list[str] = []
        item_name_to_id: dict[str, str] = {}

        # Create root/topic fact
        topic_text = f"{topic} (from {url})"
        base_tags = ["source:web", f"url:{url}", f"topic:{topic}", f"type:{content_type}"]

        topic_id = await self._memory_store.add(
            topic_text,
            category=content_type,
            source="import",
            tags=base_tags + ["root"],
        )
        fact_ids.append(topic_id)
        item_name_to_id[topic] = topic_id

        # Create a fact per item
        for item in items:
            name = item.get("name", "")
            if not name:
                continue

            description = item.get("description", "")
            category = item.get("category", content_type)
            sub_items = item.get("sub_items") or []
            location = item.get("location", "")
            requirements = item.get("requirements") or []
            tips = item.get("tips", "")

            # Build rich fact text preserving structure
            parts = [f"{name}"]
            if description:
                parts.append(f"  {description}")
            if location:
                parts.append(f"  Location: {location}")
            if requirements:
                req_str = ", ".join(str(r) for r in requirements)
                parts.append(f"  Requirements: {req_str}")
            if sub_items:
                sub_str = ", ".join(str(s) for s in sub_items)
                parts.append(f"  Sub-items: {sub_str}")
            if tips:
                parts.append(f"  Tips: {tips}")

            fact_text = "\n".join(parts)

            item_tags = list(base_tags) + [f"item:{name}"]
            if category:
                item_tags.append(f"category:{category}")

            item_id = await self._memory_store.add(
                fact_text,
                category=category or content_type,
                source="import",
                tags=item_tags,
            )
            fact_ids.append(item_id)
            item_name_to_id[name] = item_id

        # Create relations
        relations_created = 0

        if self._relation_store:
            # Each item belongs_to the topic
            for name, item_id in item_name_to_id.items():
                if item_id == topic_id:
                    continue
                try:
                    self._relation_store.add_relation(
                        item_id, topic_id, "belongs_to", strength=1.0,
                    )
                    relations_created += 1
                except Exception:
                    log.debug("Failed to add belongs_to relation for %s", name)

            # Cross-relations from related_to fields
            for item in items:
                item_name = item.get("name", "")
                related_names = item.get("related_to") or []
                if not item_name or item_name not in item_name_to_id:
                    continue
                from_id = item_name_to_id[item_name]
                for rel_name in related_names:
                    if rel_name in item_name_to_id:
                        to_id = item_name_to_id[rel_name]
                        if from_id != to_id:
                            try:
                                self._relation_store.add_relation(
                                    from_id, to_id, "related_to", strength=0.8,
                                )
                                relations_created += 1
                            except Exception:
                                log.debug(
                                    "Failed to add related_to: %s -> %s",
                                    item_name, rel_name,
                                )

        return {
            "topic": topic,
            "type": content_type,
            "items_saved": len(fact_ids),
            "relations_created": relations_created,
            "fact_ids": fact_ids,
        }

    async def _save_plain_text_fallback(
        self,
        text: str,
        url: str,
        title: str,
    ) -> dict[str, Any]:
        """Save the entire page as a single fact when LLM extraction fails."""
        # Truncate for storage
        max_fact_len = 5000
        fact_text = f"{title or 'Web page'} (from {url})\n\n{text[:max_fact_len]}"

        fact_id = await self._memory_store.add(
            fact_text,
            category="web_import",
            source="import",
            tags=["source:web", f"url:{url}", "unstructured"],
        )
        return {
            "topic": title or url,
            "type": "unstructured",
            "items_saved": 1,
            "relations_created": 0,
            "fact_ids": [fact_id],
            "fallback": True,
        }
