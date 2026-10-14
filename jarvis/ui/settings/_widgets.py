"""Reusable helper widgets for the redesigned Settings UI.

Provides visual building blocks — section headers with icons, setting
cards with label + hint + control, and divider lines — so each tab can
compose a clean, card-based layout without duplicating style logic.

All styling is done via objectName selectors defined in theme.py (QSS),
not via CSS class syntax (which Qt does not support).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class SectionHeader(QWidget):
    """Section heading with emoji icon, uppercase title, and description.

    Parameters
    ----------
    icon:
        Emoji string shown before the title (e.g. "🎤").
    title:
        Section name rendered as uppercase text with letter-spacing.
    description:
        Gray subtitle explaining what the section controls.
    """

    def __init__(
        self,
        icon: str,
        title: str,
        description: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        # Title row: emoji + text
        title_label = QLabel(f"{icon}  {title.upper()}")
        title_label.setObjectName("sectionTitle")

        # Description
        desc_label = QLabel(description)
        desc_label.setObjectName("sectionDesc")
        desc_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(title_label)
        layout.addWidget(desc_label)


class SettingCard(QWidget):
    """A single setting: human-readable label, the actual control widget,
    and a small hint explaining what it does.

    Layout (vertical):
        [Label]          (11pt, white)
        [Control]        (combo/slider/etc. — full width)
        [Hint]           (8pt, gray)

    Parameters
    ----------
    title:
        Friendly setting name (e.g. "Recognition Model").
    hint:
        One-liner explanation (e.g. "Larger models are more accurate").
    control:
        The interactive widget (QComboBox, QSlider, QWidget with
        sub-layout, etc.).
    """

    def __init__(
        self,
        title: str,
        hint: str,
        control: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        title_label = QLabel(title)
        title_label.setObjectName("settingLabel")

        hint_label = QLabel(hint)
        hint_label.setObjectName("settingHint")
        hint_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)
        layout.addWidget(title_label)
        layout.addWidget(control)
        layout.addWidget(hint_label)


class SectionCard(QWidget):
    """A card container wrapping a SectionHeader and several SettingCards
    with a slightly lighter background (#111111), rounded corners, and
    internal padding.

    Usage::

        card = SectionCard()
        card.add_header("🎤", "Speech Recognition", "How Jarvis hears you")
        card.add_setting("Model", "Larger = better", my_combo)
        card.add_setting("Language", "auto recommended", my_line)
        parent_layout.addWidget(card)
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")
        # Inline style reinforces the QSS (objectName selector sometimes
        # needs the widget to have an explicit background role).
        self.setStyleSheet(
            """
            QWidget#sectionCard {
                background-color: #111111;
                border-radius: 8px;
            }
            """
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(12)

    # Convenience methods --------------------------------------------------

    def add_header(
        self, icon: str, title: str, description: str
    ) -> SectionHeader:
        header = SectionHeader(icon, title, description)
        self._layout.addWidget(header)
        return header

    def add_setting(
        self, title: str, hint: str, control: QWidget
    ) -> SettingCard:
        card = SettingCard(title, hint, control)
        self._layout.addWidget(card)
        return card

    def add_widget(self, widget: QWidget) -> None:
        """Add an arbitrary widget (e.g. a button row)."""
        self._layout.addWidget(widget)

    def add_divider(self) -> QFrame:
        div = make_divider()
        self._layout.addWidget(div)
        return div


def make_divider(parent: QWidget | None = None) -> QFrame:
    """Create a thin horizontal divider line (#1a1a1a)."""
    line = QFrame(parent)
    line.setObjectName("divider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setFixedHeight(1)
    return line


def slider_with_label(
    slider: "QWidget",  # QSlider
    label: "QWidget",   # QLabel
) -> QWidget:
    """Wrap a slider + its value label in a horizontal row widget."""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(slider, 1)
    h.addWidget(label)
    return row
