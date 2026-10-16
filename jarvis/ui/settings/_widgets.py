"""Reusable helper widgets for the redesigned Settings UI.

Provides visual building blocks — section cards with icon headers and
badges, setting rows with label + hint + control, toggle switches,
badges, and divider lines. All tabs compose from these primitives.

Styling is done via objectName selectors defined in theme.py (QSS).
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


# ---------------------------------------------------------------------------
# Badge — colored pill label (e.g. "Connected", "GPU", "~4.7 GB")
# ---------------------------------------------------------------------------

class Badge(QLabel):
    """Small pill-shaped label with colored background.

    Variants:
        "ok"   → green (success/connected)
        "warn" → yellow (warning)
        "info" → blue (informational)
    """

    _OBJECT_NAMES = {"ok": "badgeOk", "warn": "badgeWarn", "info": "badgeInfo"}

    def __init__(
        self, text: str, variant: str = "info", parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        obj_name = self._OBJECT_NAMES.get(variant, "badgeInfo")
        self.setObjectName(obj_name)


# ---------------------------------------------------------------------------
# SectionHeader — icon + title (used inside SectionCard)
# ---------------------------------------------------------------------------

class SectionHeader(QWidget):
    """Card header with icon badge, title, and optional right-side badge."""

    def __init__(
        self,
        icon: str,
        title: str,
        description: str = "",
        badge: Badge | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        # Icon container (32×32 rounded square with accent background)
        icon_container = QWidget()
        icon_container.setObjectName("cardIcon")
        icon_container.setFixedSize(32, 32)
        icon_label = QLabel(icon)
        icon_label.setObjectName("cardIconLabel")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_layout = QVBoxLayout(icon_container)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.addWidget(icon_label)

        # Title
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")

        # Left side: icon + title
        left = QHBoxLayout()
        left.setSpacing(10)
        left.addWidget(icon_container)
        left.addWidget(title_label)
        left.addStretch()

        # Main row
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addLayout(left, 1)
        if badge is not None:
            row.addWidget(badge)


# ---------------------------------------------------------------------------
# SettingRow — label+hint left, control right, with bottom divider
# ---------------------------------------------------------------------------

class SettingRow(QWidget):
    """A single setting row: label + hint on the left, control on the right.

    Includes a bottom divider line. Use `last=True` on the final row in
    a card to suppress the divider.
    """

    def __init__(
        self,
        label: str,
        hint: str,
        control: QWidget,
        *,
        last: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        # Left: label + hint
        lbl = QLabel(label)
        lbl.setObjectName("settingLabel")

        ht = QLabel(hint)
        ht.setObjectName("settingHint")
        ht.setWordWrap(True)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        left.addWidget(lbl)
        left.addWidget(ht)

        # Row layout
        row = QHBoxLayout()
        row.setContentsMargins(0, 12, 0, 12)
        row.addLayout(left, 1)
        row.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(row)
        if not last:
            outer.addWidget(make_setting_divider())


# ---------------------------------------------------------------------------
# SettingCard (legacy compatibility name)
# ---------------------------------------------------------------------------

class SettingCard(QWidget):
    """A single setting: label, control, hint. Legacy layout (vertical).

    For new code prefer SettingRow (horizontal label+control layout).
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


# ---------------------------------------------------------------------------
# SectionCard — container card with rounded borders
# ---------------------------------------------------------------------------

class SectionCard(QWidget):
    """A card container with dark background, rounded corners, and
    1px border. Contains a header and setting rows.

    Usage::

        card = SectionCard()
        card.add_header("🎤", "Speech Recognition", badge=Badge("GPU", "info"))
        card.add_row("Model", "Larger = better", my_combo)
        card.add_row("Language", "auto recommended", my_line, last=True)
        parent_layout.addWidget(card)
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 20, 24, 20)
        self._layout.setSpacing(0)

    # -- Convenience methods ------------------------------------------------

    def add_header(
        self,
        icon: str,
        title: str,
        description: str = "",
        badge: Badge | None = None,
    ) -> SectionHeader:
        header = SectionHeader(icon, title, description, badge)
        self._layout.addWidget(header)
        # Spacing after header
        self._layout.addSpacing(16)
        return header

    def add_row(
        self,
        label: str,
        hint: str,
        control: QWidget,
        *,
        last: bool = False,
    ) -> SettingRow:
        """Add a horizontal setting row (label+hint left, control right)."""
        row = SettingRow(label, hint, control, last=last)
        self._layout.addWidget(row)
        return row

    def add_setting(
        self, title: str, hint: str, control: QWidget
    ) -> SettingCard:
        """Legacy vertical setting card. Prefer add_row() for new code."""
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


# ---------------------------------------------------------------------------
# Divider helpers
# ---------------------------------------------------------------------------

def make_divider(parent: QWidget | None = None) -> QFrame:
    """Create a thin horizontal divider line."""
    line = QFrame(parent)
    line.setObjectName("divider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setFixedHeight(1)
    return line


def make_setting_divider(parent: QWidget | None = None) -> QFrame:
    """Lighter divider for between setting rows."""
    line = QFrame(parent)
    line.setObjectName("settingDivider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setFixedHeight(1)
    return line


# ---------------------------------------------------------------------------
# slider_with_label — slider + value display
# ---------------------------------------------------------------------------

def slider_with_label(
    slider: "QWidget",  # QSlider
    label: "QWidget",   # QLabel
) -> QWidget:
    """Wrap a slider + its value label in a horizontal row widget."""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(12)
    h.addWidget(slider, 1)
    h.addWidget(label)
    return row
