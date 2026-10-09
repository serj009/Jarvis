"""Voice tools for memory: remember, recall, forget, memory status (T4.1/T4.3).

These tools are registered in the ToolRegistry so the LLM can invoke them
via voice commands like:
    "Remember that my favorite color is blue"
    "What do you know about my work schedule?"
    "Forget the fact about my old address"
    "How many things do you remember?"

Each tool receives the MemoryStore instance at construction time
(wired in tools/__init__.py via setup_local_tools).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from jarvis.core.phrases import reply_language
from jarvis.tools.registry import ToolResult, VoicePattern
from jarvis.memory.smart_search import SmartSearch

if TYPE_CHECKING:
    from jarvis.memory.store import MemoryStore


# ---------------------------------------------------------------------------
# Tool: Remember a fact
# ---------------------------------------------------------------------------

class RememberArgs(BaseModel):
    fact: str = Field(description="The fact or information to remember")
    category: str = Field(default="general", description="Category: personal, games, tech, work, general")


class RememberTool:
    name: str = "remember_fact"
    description: str = (
        "Store a piece of information in long-term memory. Use when the user "
        "says 'remember that...', 'save this...', 'note that...', or "
        "'keep in mind that...'. The fact parameter is the information to store."
    )
    args_schema = RememberArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:remember|save|store|запомни|збережи|запам.ятай)\b",
            priority=200,
        ),
    )

    def __init__(self, *, memory_store: MemoryStore | None = None) -> None:
        self._store = memory_store

    async def execute(self, args: RememberArgs) -> ToolResult:
        if self._store is None or not self._store.is_open:
            return ToolResult(success=False, error="Memory is not available.")

        lang = reply_language()
        fact_id = await self._store.add(
            args.fact,
            category=args.category,
            source="user",
            language=lang,
        )

        responses = {
            "en": f"Got it, sir. I'll remember that.",
            "ru": f"Понял, сэр. Запомнил.",
            "uk": f"Зрозумів, сер. Запам'ятав.",
        }
        return ToolResult(success=True, output=responses.get(lang, responses["en"]))


# ---------------------------------------------------------------------------
# Tool: Search memory
# ---------------------------------------------------------------------------

class RecallArgs(BaseModel):
    query: str = Field(description="What to search for in memory")


class RecallTool:
    name: str = "search_memory"
    description: str = (
        "Search long-term memory for stored facts. Use when the user asks "
        "'what do you know about...', 'do you remember...', 'what did I tell "
        "you about...'. Returns matching facts from memory."
    )
    args_schema = RecallArgs
    requires_confirmation: bool = False
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:what do you (?:know|remember)|do you remember|что (?:ты )?знаешь|що (?:ти )?знаєш)\b",
            priority=210,
        ),
    )

    def __init__(self, *, memory_store: MemoryStore | None = None) -> None:
        self._store = memory_store
        self._searcher = SmartSearch()

    async def execute(self, args: RecallArgs) -> ToolResult:
        if self._store is None or not self._store.is_open:
            return ToolResult(success=False, error="Memory is not available.")

        lang = reply_language()
        raw_facts = await self._store.search(args.query, limit=20)

        # Re-rank with SmartSearch (time decay, frequency, language boost)
        scored = self._searcher.rank(raw_facts, args.query, language=lang, limit=5)
        facts = [s.fact for s in scored]

        if not facts:
            responses = {
                "en": f"I don't have anything stored about that, sir.",
                "ru": f"У меня нет информации об этом, сэр.",
                "uk": f"У мене немає інформації про це, сер.",
            }
            return ToolResult(success=True, output=responses.get(lang, responses["en"]))

        # Build a spoken summary
        if len(facts) == 1:
            return ToolResult(success=True, output=facts[0].text)

        summaries = {
            "en": f"I found {len(facts)} things: ",
            "ru": f"Нашёл {len(facts)} записей: ",
            "uk": f"Знайшов {len(facts)} записів: ",
        }
        prefix = summaries.get(lang, summaries["en"])
        items = ". ".join(f.text for f in facts[:3])
        if len(facts) > 3:
            more = {
                "en": f" ...and {len(facts) - 3} more.",
                "ru": f" ...и ещё {len(facts) - 3}.",
                "uk": f" ...і ще {len(facts) - 3}.",
            }
            items += more.get(lang, more["en"])
        return ToolResult(success=True, output=prefix + items)


# ---------------------------------------------------------------------------
# Tool: Forget a fact
# ---------------------------------------------------------------------------

class ForgetArgs(BaseModel):
    query: str = Field(description="What fact to forget / delete from memory")


class ForgetTool:
    name: str = "forget_fact"
    description: str = (
        "Delete a fact from long-term memory. Use when the user says "
        "'forget about...', 'delete that...', 'remove from memory...'. "
        "Searches for the fact first, then deletes the best match."
    )
    args_schema = ForgetArgs
    requires_confirmation: bool = True  # Destructive — ask before deleting
    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        VoicePattern(
            regex=r"^(?:forget|delete|remove|забудь|удали|видали|забудь)\b.*(?:memory|памят|пам.ят)",
            priority=220,
        ),
    )

    def __init__(self, *, memory_store: MemoryStore | None = None) -> None:
        self._store = memory_store

    async def execute(self, args: ForgetArgs) -> ToolResult:
        if self._store is None or not self._store.is_open:
            return ToolResult(success=False, error="Memory is not available.")

        lang = reply_language()
        facts = await self._store.search(args.query, limit=1)

        if not facts:
            responses = {
                "en": "I don't have anything matching that to forget, sir.",
                "ru": "У меня нет ничего подходящего для удаления, сэр.",
                "uk": "У мене немає нічого відповідного для видалення, сер.",
            }
            return ToolResult(success=True, output=responses.get(lang, responses["en"]))

        fact = facts[0]
        if fact.is_permanent:
            responses = {
                "en": "That fact is marked as permanent and cannot be deleted, sir.",
                "ru": "Этот факт помечен как постоянный и не может быть удалён, сэр.",
                "uk": "Цей факт позначений як постійний і не може бути видалений, сер.",
            }
            return ToolResult(success=True, output=responses.get(lang, responses["en"]))

        await self._store.delete(fact.id)
        responses = {
            "en": f"Done, sir. I've forgotten: {fact.text[:80]}",
            "ru": f"Готово, сэр. Удалил: {fact.text[:80]}",
            "uk": f"Готово, сер. Видалив: {fact.text[:80]}",
        }
        return ToolResult(success=True, output=responses.get(lang, responses["en"]))


# ---------------------------------------------------------------------------
# Tool: Memory status
# ---------------------------------------------------------------------------

class MemoryStatusTool:
    name: str = "memory_status"
    description: str = (
        "Report how many facts are stored in memory and their categories. "
        "Use when the user asks 'how much do you remember', 'memory status', "
        "'how many facts do you know'."
    )
    args_schema = type("EmptyArgs", (BaseModel,), {})
    requires_confirmation: bool = False

    def __init__(self, *, memory_store: MemoryStore | None = None) -> None:
        self._store = memory_store

    async def execute(self, args: BaseModel) -> ToolResult:
        if self._store is None or not self._store.is_open:
            return ToolResult(success=False, error="Memory is not available.")

        lang = reply_language()
        total = await self._store.count()
        categories = await self._store.categories()

        if total == 0:
            responses = {
                "en": "My memory is empty, sir. Tell me things to remember!",
                "ru": "Моя память пуста, сэр. Расскажите мне что-нибудь!",
                "uk": "Моя пам'ять порожня, сер. Розкажіть мені щось!",
            }
            return ToolResult(success=True, output=responses.get(lang, responses["en"]))

        cat_text = ", ".join(f"{name}: {count}" for name, count in categories[:5])
        responses = {
            "en": f"I have {total} facts in memory, sir. Categories: {cat_text}.",
            "ru": f"В моей памяти {total} фактов, сэр. Категории: {cat_text}.",
            "uk": f"У моїй пам'яті {total} фактів, сер. Категорії: {cat_text}.",
        }
        return ToolResult(success=True, output=responses.get(lang, responses["en"]))
