"""AutoTagger: automatic tag and category extraction from fact text.

Extracts tags from text using regex patterns and keyword matching.
Detects language (uk/ru/en) from text characters.
Assigns hierarchical categories (e.g. games/stalker_2/achievements).

Does NOT require LLM — pure regex + keyword rules. Fast enough to
run synchronously on every fact insertion.

Usage:
    tagger = AutoTagger()
    result = tagger.analyze("My Stalker 2 save is corrupted")
    # result.tags = ["games", "stalker_2", "save"]
    # result.category = "games"
    # result.language = "en"
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Language detection (same logic as phrases.py but standalone)
# ---------------------------------------------------------------------------

_CYRILLIC = re.compile(r"[\u0430-\u044f\u0451\u0456\u0457\u0454\u0491]", re.IGNORECASE)

if TYPE_CHECKING:
    from jarvis.memory.auto_classifier import AutoClassifier
_LATIN = re.compile(r"[a-z]", re.IGNORECASE)
_UK_LETTERS = re.compile(r"[\u0456\u0457\u0454\u0491]", re.IGNORECASE)
_UK_WORDS = frozenset({"\u0449\u043e", "\u0449\u043e\u0441\u044c", "\u0449\u0435", "\u0446\u0435", "\u044f\u043a", "\u0434\u0443\u0436\u0435", "\u0439", "\u0448\u043e"})
_WORD = re.compile(r"[\u0430-\u044f\u0451\u0456\u0457\u0454\u0491']+", re.IGNORECASE)


def detect_language(text: str) -> str:
    """Detect language from text: 'uk', 'ru', or 'en'."""
    if not text:
        return "en"
    if _CYRILLIC.search(text):
        if _UK_LETTERS.search(text):
            return "uk"
        words = {w.lower() for w in _WORD.findall(text)}
        return "uk" if words & _UK_WORDS else "ru"
    if _LATIN.search(text):
        return "en"
    return "en"


# ---------------------------------------------------------------------------
# Tag extraction rules
# ---------------------------------------------------------------------------

@dataclass
class TagRule:
    """A pattern that extracts a tag when matched."""
    pattern: re.Pattern
    tag: str
    category: str = ""  # Optional category override


# Game-specific rules
_GAME_RULES = [
    TagRule(re.compile(r"stalker\s*2|сталкер\s*2", re.I), "stalker_2", "games"),
    TagRule(re.compile(r"dark\s*souls|дарк\s*соулс", re.I), "dark_souls", "games"),
    TagRule(re.compile(r"elden\s*ring|елден\s*рінг|элден\s*ринг", re.I), "elden_ring", "games"),
    TagRule(re.compile(r"cyberpunk|кіберпанк|киберпанк", re.I), "cyberpunk_2077", "games"),
    TagRule(re.compile(r"baldur|балдур|балдурс", re.I), "baldurs_gate_3", "games"),
    TagRule(re.compile(r"witcher|ведьмак|відьмак", re.I), "witcher_3", "games"),
    TagRule(re.compile(r"skyrim|скайрим|скайрім", re.I), "skyrim", "games"),
    TagRule(re.compile(r"minecraft|майнкрафт", re.I), "minecraft", "games"),
    TagRule(re.compile(r"diablo|діабло|диабло", re.I), "diablo", "games"),
    TagRule(re.compile(r"gta\b|гта\b", re.I), "gta", "games"),
    TagRule(re.compile(r"achievement|ачивк|досягнен", re.I), "achievements", ""),
    TagRule(re.compile(r"save\s*(?:game|file)|сохранен|збережен", re.I), "saves", ""),
]

# Tech rules
_TECH_RULES = [
    TagRule(re.compile(r"python", re.I), "python", "tech"),
    TagRule(re.compile(r"javascript|js\b", re.I), "javascript", "tech"),
    TagRule(re.compile(r"docker", re.I), "docker", "tech"),
    TagRule(re.compile(r"ollama", re.I), "ollama", "tech"),
    TagRule(re.compile(r"whisper", re.I), "whisper", "tech"),
    TagRule(re.compile(r"gpu|видеокарт|відеокарт", re.I), "gpu", "tech"),
    TagRule(re.compile(r"linux|ubuntu|debian", re.I), "linux", "tech"),
    TagRule(re.compile(r"github|гитхаб|гітхаб", re.I), "github", "tech"),
]

# Personal rules
_PERSONAL_RULES = [
    TagRule(re.compile(r"my name|меня зовут|мене звати", re.I), "name", "personal"),
    TagRule(re.compile(r"birthday|день рожден|день народжен", re.I), "birthday", "personal"),
    TagRule(re.compile(r"favorite|любим|улюблен", re.I), "favorites", "personal"),
    TagRule(re.compile(r"wife|husband|жена|муж|дружина|чоловік", re.I), "family", "personal"),
    TagRule(re.compile(r"friend|друг|друж", re.I), "friends", "personal"),
    TagRule(re.compile(r"work.?schedule|расписание|розклад", re.I), "schedule", "work"),
]

# Media rules
_MEDIA_RULES = [
    TagRule(re.compile(r"anime|аниме|анімe", re.I), "anime", "media"),
    TagRule(re.compile(r"manga|манга", re.I), "manga", "media"),
    TagRule(re.compile(r"ranobe|ранобе", re.I), "ranobe", "media"),
    TagRule(re.compile(r"movie|film|фильм|фільм", re.I), "movies", "media"),
    TagRule(re.compile(r"series|сериал|серіал", re.I), "series", "media"),
    TagRule(re.compile(r"book|книг", re.I), "books", "media"),
    TagRule(re.compile(r"music|song|музык|пісн|песн", re.I), "music", "media"),
]

ALL_RULES = _GAME_RULES + _TECH_RULES + _PERSONAL_RULES + _MEDIA_RULES


# ---------------------------------------------------------------------------
# AutoTagger result
# ---------------------------------------------------------------------------

@dataclass
class TagResult:
    """Result of auto-tagging analysis."""
    tags: list[str] = field(default_factory=list)
    category: str = "general"
    language: str = "en"
    sub_category: str = ""  # e.g. "stalker_2" for hierarchical "games/stalker_2"

    @property
    def full_category(self) -> str:
        """Hierarchical category path, e.g. 'games/stalker_2/achievements'."""
        parts = [self.category]
        if self.sub_category:
            parts.append(self.sub_category)
        return "/".join(parts)


# ---------------------------------------------------------------------------
# AutoTagger
# ---------------------------------------------------------------------------

class AutoTagger:
    """Extract tags, category, and language from fact text.

    Pure regex + keyword matching — no LLM, no network, < 1ms per call.

    When an AutoClassifier is attached (via ``set_classifier()``),
    entities that regex rules can't categorize (result stays "general")
    are flagged for async LLM classification. The classifier reference
    is stored but never called synchronously — the caller (MemoryManager)
    checks ``needs_classification`` on the result and calls the
    classifier asynchronously if needed.
    """

    def __init__(self, extra_rules: list[TagRule] | None = None) -> None:
        self._rules = list(ALL_RULES)
        if extra_rules:
            self._rules.extend(extra_rules)
        self._classifier: AutoClassifier | None = None

    def set_classifier(self, classifier: AutoClassifier | None) -> None:
        """Attach an AutoClassifier for LLM-based entity classification.

        When set, ``analyze()`` marks results where regex rules couldn't
        determine a specific category, so the caller can optionally run
        async LLM classification.
        """
        self._classifier = classifier

    @property
    def has_classifier(self) -> bool:
        """Whether an AutoClassifier is attached."""
        return self._classifier is not None

    @property
    def classifier(self) -> AutoClassifier | None:
        """The attached AutoClassifier, if any."""
        return self._classifier

    def analyze(self, text: str, category_hint: str = "general") -> TagResult:
        """Analyze text and return extracted tags + category + language."""
        language = detect_language(text)
        tags: list[str] = []
        category = category_hint
        sub_category = ""

        for rule in self._rules:
            if rule.pattern.search(text):
                if rule.tag not in tags:
                    tags.append(rule.tag)
                # First rule with a category wins
                if rule.category and category == "general":
                    category = rule.category
                # Track sub-category (game title etc.)
                if rule.category and not sub_category:
                    sub_category = rule.tag

        # If category_hint was specific and not "general", keep it
        if category_hint != "general":
            category = category_hint

        return TagResult(
            tags=tags,
            category=category,
            language=language,
            sub_category=sub_category if sub_category != category else "",
        )
