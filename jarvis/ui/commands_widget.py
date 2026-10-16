"""Reusable commands reference widget with search + language filter.

Used by *both* the Settings → Commands tab and the standalone Help panel
so that the two surfaces stay visually identical and code-deduplicated.

Data comes from ``jarvis.ui.capabilities.CAPABILITY_CATEGORIES`` plus a
small set of "extra" commands (language switching, quiet mode, etc.) that
don't live in capabilities.py because they are pipeline behaviours rather
than registered tools.

Styling relies on objectName-based QSS selectors already defined in
``jarvis.ui.settings.theme`` (``sectionCard``, ``cardTitle``, ``cardIcon``,
``settingLabel``, ``settingHint``, ``cmdTagEN/RU/UK``, ``langBtn``,
``pageTitle``, ``pageDesc``).
"""

from __future__ import annotations

import logging

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

from jarvis.ui.capabilities import CAPABILITY_CATEGORIES
from jarvis.ui.settings._widgets import SectionCard

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extra commands (not in capabilities.py — pipeline / state-machine behaviours)
# ---------------------------------------------------------------------------

_EXTRA_COMMANDS: tuple[tuple[str, str, tuple[str, str, str]], ...] = (
    (
        "\U0001f310 Switch language",
        "Change JARVIS response language",
        ("Speak English", "Говори по-русски", "Говори українською"),
    ),
    (
        "\U0001f92b Quiet mode",
        "Stop talking without going to sleep",
        ("Be quiet / Stop", "Замолчи / Хватит", "Замовчи / Досить"),
    ),
    (
        "\U0001f550 Time & Date",
        "Ask for current time",
        ("What time is it?", "Который час?", "Котра година?"),
    ),
    (
        "\U0001f3ae Game mode status",
        "Check or toggle game mode",
        ("Game mode", "Игровой режим", "Ігровий режим"),
    ),
    (
        "\u25b6\ufe0f Activate game mode",
        "Manually enable game mode",
        ("Enable game mode", "Включи игровой режим", "Увімкни ігровий режим"),
    ),
    (
        "\u23f9\ufe0f Deactivate game mode",
        "Manually disable game mode",
        (
            "Disable game mode",
            "Выключи игровой режим",
            "Вимкни ігровий режим",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Small internal widgets
# ---------------------------------------------------------------------------


class _LangButton(QPushButton):
    """Toggle button for the language filter bar (All / EN / RU / UK)."""

    def __init__(self, text: str, lang: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.lang = lang
        self.setCheckable(True)
        self.setObjectName("langBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(50)


class _CommandRow(QWidget):
    """A single command row: name + description + coloured language tags."""

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

        # Example tags (coloured per language)
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

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _make_tag(text: str, obj_name: str) -> QLabel:
        tag = QLabel(f'"{text}"')
        tag.setObjectName(obj_name)
        return tag

    def matches_search(self, query: str) -> bool:
        return query in self._text_lower

    def set_lang_filter(self, lang: str) -> None:
        """Show/hide language tags.  ``'all'`` shows everything."""
        if self._en_tag:
            self._en_tag.setVisible(lang in ("all", "en"))
        if self._ru_tag:
            self._ru_tag.setVisible(lang in ("all", "ru"))
        if self._uk_tag:
            self._uk_tag.setVisible(lang in ("all", "uk"))


# ---------------------------------------------------------------------------
# CommandsWidget — the shared, reusable piece
# ---------------------------------------------------------------------------


class CommandsWidget(QWidget):
    """Searchable, filterable catalogue of every JARVIS command.

    Embeddable in any parent layout.  Provides:

    * A header row with a search box and All / EN / RU / UK toggle buttons.
    * A scrollable area of ``SectionCard`` groups populated from
      ``CAPABILITY_CATEGORIES`` + ``_EXTRA_COMMANDS``.
    * Real-time search filtering and per-language tag visibility.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        # -- Search + language filter row ----------------------------------
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("\U0001f50d Search commands\u2026")
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

        # -- Scrollable card area ------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(16)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # -- Build rows from data sources ----------------------------------
        self._all_rows: list[_CommandRow] = []
        self._build_from_capabilities()
        self._build_extras()
        self._content_layout.addStretch()

        # -- Wiring --------------------------------------------------------
        self._search.textChanged.connect(self._on_search)

    # -- data builders -----------------------------------------------------

    def _build_from_capabilities(self) -> None:
        """Populate cards from ``CAPABILITY_CATEGORIES``."""
        for cat in CAPABILITY_CATEGORIES:
            card = SectionCard()
            card.add_header(cat.icon_glyph, cat.name)
            for cap in cat.capabilities:
                examples = list(cap.examples)
                en = examples[0] if examples else ""
                ru = ""
                uk = ""
                for ex in examples[1:]:
                    if any("\u0400" <= c <= "\u04ff" for c in ex):
                        if not ru:
                            ru = ex
                        elif not uk:
                            uk = ex
                    elif not en:
                        en = ex
                row = _CommandRow(cap.name, cap.description, en, ru, uk)
                card.add_widget(row)
                self._all_rows.append(row)
            self._content_layout.addWidget(card)

    def _build_extras(self) -> None:
        """Populate the 'Special Commands' card (not in capabilities.py)."""
        card = SectionCard()
        card.add_header("\U0001f3ae", "Special Commands")
        for name, desc, (en, ru, uk) in _EXTRA_COMMANDS:
            row = _CommandRow(name, desc, en, ru, uk)
            card.add_widget(row)
            self._all_rows.append(row)
        self._content_layout.addWidget(card)

    # -- slots -------------------------------------------------------------

    def _on_search(self, text: str) -> None:
        query = text.strip().lower()
        for row in self._all_rows:
            row.setVisible(row.matches_search(query) if query else True)

    def _on_lang(self, clicked_btn: _LangButton) -> None:
        for btn in self._lang_buttons:
            btn.setChecked(btn is clicked_btn)
        lang = clicked_btn.lang
        for row in self._all_rows:
            row.set_lang_filter(lang)

    # -- public API --------------------------------------------------------

    def focus_search(self) -> None:
        """Put keyboard focus on the search box (handy after panel open)."""
        self._search.setFocus()
