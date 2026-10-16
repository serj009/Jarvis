"""Commands reference tab: searchable catalogue of all JARVIS capabilities.

Pulls data from ``jarvis.ui.capabilities.CAPABILITY_CATEGORIES`` and
renders each capability as a row with example phrases. The search box
filters in real time. A language toggle (All / EN / RU / UK) hides
examples in other languages.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from jarvis.core.config import JarvisConfig
from jarvis.ui.capabilities import CAPABILITY_CATEGORIES
from jarvis.ui.settings._widgets import SectionCard

log = logging.getLogger(__name__)


# -- Built-in command reference (beyond what capabilities.py covers) --
# These are commands that capabilities.py doesn't document because they
# are not tools — they're pipeline / state-machine behaviors.
_EXTRA_COMMANDS: tuple[tuple[str, str, tuple[str, str, str]], ...] = (
    ("🌐 Switch language",
     "Change JARVIS response language",
     ("Speak English", "Говори по-русски", "Говори українською")),
    ("🤫 Quiet mode",
     "Stop talking without going to sleep",
     ("Be quiet / Stop", "Замолчи / Хватит", "Замовчи / Досить")),
    ("🕐 Time & Date",
     "Ask for current time",
     ("What time is it?", "Который час?", "Котра година?")),
    ("🎮 Game mode status",
     "Check or toggle game mode",
     ("Game mode", "Игровой режим", "Ігровий режим")),
    ("▶️ Activate game mode",
     "Manually enable game mode",
     ("Enable game mode", "Включи игровой режим", "Увімкни ігровий режим")),
    ("⏹️ Deactivate game mode",
     "Manually disable game mode",
     ("Disable game mode", "Выключи игровой режим", "Вимкни ігровий режим")),
)


class _LangButton(QPushButton):
    """Toggle button for language filter."""

    def __init__(self, text: str, lang: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.lang = lang
        self.setCheckable(True)
        self.setObjectName("langBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(50)


class _CommandRow(QWidget):
    """A single command row: name + example phrases."""

    def __init__(
        self,
        name: str,
        description: str,
        examples_en: str,
        examples_ru: str = "",
        examples_uk: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._text_lower = (
            f"{name} {description} {examples_en} {examples_ru} {examples_uk}"
        ).lower()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(2)

        # Name
        name_label = QLabel(name)
        name_label.setObjectName("settingLabel")

        # Description
        desc_label = QLabel(description)
        desc_label.setObjectName("settingHint")
        desc_label.setWordWrap(True)

        # Example tags
        self._en_tag = self._make_tag(examples_en, "cmdTagEN") if examples_en else None
        self._ru_tag = self._make_tag(examples_ru, "cmdTagRU") if examples_ru else None
        self._uk_tag = self._make_tag(examples_uk, "cmdTagUK") if examples_uk else None

        tags_layout = QHBoxLayout()
        tags_layout.setContentsMargins(0, 2, 0, 0)
        tags_layout.setSpacing(6)
        for tag in (self._en_tag, self._ru_tag, self._uk_tag):
            if tag is not None:
                tags_layout.addWidget(tag)
        tags_layout.addStretch()

        layout.addWidget(name_label)
        layout.addWidget(desc_label)
        layout.addLayout(tags_layout)

    def _make_tag(self, text: str, obj_name: str) -> QLabel:
        tag = QLabel(f'"{text}"')
        tag.setObjectName(obj_name)
        return tag

    def matches_search(self, query: str) -> bool:
        return query in self._text_lower

    def set_lang_filter(self, lang: str) -> None:
        """Show/hide language tags. 'all' shows everything."""
        if self._en_tag:
            self._en_tag.setVisible(lang in ("all", "en"))
        if self._ru_tag:
            self._ru_tag.setVisible(lang in ("all", "ru"))
        if self._uk_tag:
            self._uk_tag.setVisible(lang in ("all", "uk"))


class CommandsTab(QWidget):
    def __init__(
        self,
        *,
        config: JarvisConfig,
        on_change: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.setSpacing(16)

        # -- Header --
        title = QLabel("Commands")
        title.setObjectName("pageTitle")
        desc = QLabel("Everything JARVIS can do — say these after the wake word")
        desc.setObjectName("pageDesc")
        root.addWidget(title)
        root.addWidget(desc)

        # -- Search + lang filter --
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("\U0001f50d Search commands...")
        filter_row.addWidget(self._search, 1)

        self._lang_buttons: list[_LangButton] = []
        for text, lang in (("All", "all"), ("EN", "en"), ("RU", "ru"), ("UK", "uk")):
            btn = _LangButton(text, lang)
            if lang == "all":
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, b=btn: self._on_lang(b))
            self._lang_buttons.append(btn)
            filter_row.addWidget(btn)

        root.addLayout(filter_row)

        # -- Scrollable content --
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(16)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # -- Build command rows --
        self._all_rows: list[_CommandRow] = []
        self._build_from_capabilities()
        self._build_extras()
        self._content_layout.addStretch()

        # -- Connections --
        self._search.textChanged.connect(self._on_search)

    def _build_from_capabilities(self) -> None:
        """Build cards from CAPABILITY_CATEGORIES."""
        for cat in CAPABILITY_CATEGORIES:
            card = SectionCard()
            card.add_header(cat.icon_glyph, cat.name)
            for cap in cat.capabilities:
                # Split examples into EN / RU / UK by heuristic:
                # first example = EN, look for Cyrillic for RU/UK
                examples = list(cap.examples)
                en = examples[0] if examples else ""
                ru = ""
                uk = ""
                # Simple heuristic: if there are multiple examples,
                # try to detect Cyrillic
                for ex in examples[1:]:
                    if any("\u0400" <= c <= "\u04ff" for c in ex):
                        if not ru:
                            ru = ex
                        elif not uk:
                            uk = ex
                    elif not en:
                        en = ex

                row = _CommandRow(
                    f"{cap.name}",
                    cap.description,
                    en, ru, uk,
                )
                card.add_widget(row)
                self._all_rows.append(row)

            self._content_layout.addWidget(card)

    def _build_extras(self) -> None:
        """Build the 'extra' commands not in capabilities.py."""
        card = SectionCard()
        card.add_header("\U0001f3ae", "Special Commands")
        for name, desc, (en, ru, uk) in _EXTRA_COMMANDS:
            row = _CommandRow(name, desc, en, ru, uk)
            card.add_widget(row)
            self._all_rows.append(row)
        self._content_layout.addWidget(card)

    def _on_search(self, text: str) -> None:
        query = text.strip().lower()
        for row in self._all_rows:
            row.setVisible(row.matches_search(query) if query else True)

    def _on_lang(self, clicked_btn: _LangButton) -> None:
        # Radio-like behavior: uncheck others, check clicked
        for btn in self._lang_buttons:
            btn.setChecked(btn is clicked_btn)
        lang = clicked_btn.lang
        for row in self._all_rows:
            row.set_lang_filter(lang)
