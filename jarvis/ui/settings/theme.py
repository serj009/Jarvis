"""Visual design tokens and Qt stylesheet for the Settings window.

Palette: near-black background, subtle gray secondary text. Blue accent.
Sidebar navigation with card-based content areas.

Usage
-----
    from jarvis.ui.settings.theme import apply_theme
    apply_theme(window)   # call once in SettingsWindow.__init__
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

# ---------------------------------------------------------------------------
# Color tokens
# ---------------------------------------------------------------------------

BG             = "#0c0c0c"
BG_CARD        = "#151515"
BG_CARD_HOVER  = "#1a1a1a"
BG_INPUT       = "#111111"
TEXT_PRIMARY   = "#e8e8e8"
TEXT_SECONDARY = "#888888"
TEXT_MUTED     = "#555555"
ACCENT         = "#3b82f6"
ACCENT_DIM     = "rgba(59, 130, 246, 0.15)"
SUCCESS        = "#22c55e"
SUCCESS_DIM    = "rgba(34, 197, 94, 0.15)"
WARNING        = "#f59e0b"
WARNING_DIM    = "rgba(245, 158, 11, 0.15)"
ERROR          = "#ef4444"
BORDER         = "#222222"
BORDER_FOCUS   = "#444444"
HOVER_BG       = "#1a1a1a"

# Section card tokens
CARD_RADIUS    = "12px"
CARD_PADDING   = "20px 24px"

# Hint / description tokens
HINT_COLOR     = "#555555"
DESC_COLOR     = "#707070"
DIVIDER_COLOR  = "#1a1a1a"

# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

_QSS = f"""
/* ── base ─────────────────────────────────────────────────────────── */

QMainWindow,
QDialog,
QWidget {{
    background-color: {BG};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", -apple-system, system-ui, sans-serif;
    font-weight: 400;
    font-size: 13px;
    line-height: 1.5;
}}

/* ── sidebar ──────────────────────────────────────────────────────── */

QWidget#settingsSidebar {{
    background-color: {BG};
    border-right: 1px solid {BORDER};
}}

QWidget#sidebarHeader {{
    background-color: transparent;
    border-bottom: 1px solid {BORDER};
}}

QLabel#sidebarTitle {{
    color: {TEXT_PRIMARY};
    font-size: 16px;
    font-weight: 600;
    background-color: transparent;
}}

QLabel#sidebarVersion {{
    color: {TEXT_MUTED};
    font-size: 11px;
    background-color: transparent;
}}

QLabel#navSection {{
    color: {TEXT_MUTED};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.5px;
    background-color: transparent;
}}

QPushButton#navItem {{
    background-color: transparent;
    color: {TEXT_SECONDARY};
    border: none;
    border-left: 3px solid transparent;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 400;
    text-align: left;
}}

QPushButton#navItem:hover {{
    color: {TEXT_PRIMARY};
    background-color: {BG_CARD};
}}

QPushButton#navItemActive {{
    background-color: {BG_CARD};
    color: {TEXT_PRIMARY};
    border: none;
    border-left: 3px solid {ACCENT};
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 400;
    text-align: left;
}}

QWidget#sidebarFooter {{
    background-color: transparent;
    border-top: 1px solid {BORDER};
}}

QLabel#statusLabel {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
    background-color: transparent;
}}

/* ── labels ────────────────────────────────────────────────────────── */

QLabel {{
    color: {TEXT_PRIMARY};
    font-size: 13px;
    font-weight: 400;
    background-color: transparent;
}}

/* ── page title/desc ──────────────────────────────────────────────── */

QLabel#pageTitle {{
    color: {TEXT_PRIMARY};
    font-size: 22px;
    font-weight: 600;
    background-color: transparent;
}}

QLabel#pageDesc {{
    color: {TEXT_SECONDARY};
    font-size: 13px;
    background-color: transparent;
}}

/* ── section cards (objectName=sectionCard) ────────────────────────── */

QWidget#sectionCard {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {CARD_RADIUS};
}}

QWidget#sectionCard:hover {{
    border-color: #333333;
}}

/* ── card header (objectName=cardTitle) ─────────────────────────────── */

QLabel#cardTitle {{
    color: {TEXT_PRIMARY};
    font-size: 14px;
    font-weight: 600;
    background-color: transparent;
}}

/* ── card icon (objectName=cardIcon) ───────────────────────────────── */

QWidget#cardIcon {{
    background-color: {ACCENT_DIM};
    border-radius: 8px;
}}

QLabel#cardIconLabel {{
    color: {ACCENT};
    font-size: 15px;
    background-color: transparent;
}}

/* ── badge ─────────────────────────────────────────────────────────── */

QLabel#badgeOk {{
    background-color: {SUCCESS_DIM};
    color: {SUCCESS};
    font-size: 11px;
    font-weight: 500;
    padding: 3px 10px;
    border-radius: 10px;
}}

QLabel#badgeWarn {{
    background-color: {WARNING_DIM};
    color: {WARNING};
    font-size: 11px;
    font-weight: 500;
    padding: 3px 10px;
    border-radius: 10px;
}}

QLabel#badgeInfo {{
    background-color: {ACCENT_DIM};
    color: {ACCENT};
    font-size: 11px;
    font-weight: 500;
    padding: 3px 10px;
    border-radius: 10px;
}}

/* ── section title (objectName=sectionTitle) ───────────────────────── */

QLabel#sectionTitle {{
    color: {TEXT_PRIMARY};
    font-size: 14px;
    font-weight: 600;
    background-color: transparent;
}}

/* ── section description (objectName=sectionDesc) ──────────────────── */

QLabel#sectionDesc {{
    color: {DESC_COLOR};
    font-size: 11px;
    background-color: transparent;
}}

/* ── setting label (objectName=settingLabel) ────────────────────────── */

QLabel#settingLabel {{
    color: {TEXT_PRIMARY};
    font-size: 13px;
    font-weight: 500;
    background-color: transparent;
}}

/* ── setting hint (objectName=settingHint) ──────────────────────────── */

QLabel#settingHint {{
    color: {HINT_COLOR};
    font-size: 11px;
    font-weight: 400;
    background-color: transparent;
}}

/* ── divider (objectName=divider) ──────────────────────────────────── */

QFrame#divider {{
    background-color: {DIVIDER_COLOR};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

/* ── setting row divider ──────────────────────────────────────────── */

QFrame#settingDivider {{
    background-color: #1a1a1a;
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

/* ── text inputs ───────────────────────────────────────────────────── */

QLineEdit,
QPlainTextEdit,
QSpinBox,
QDoubleSpinBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    selection-background-color: {ACCENT};
    selection-color: #000000;
}}

QLineEdit:hover,
QSpinBox:hover,
QDoubleSpinBox:hover {{
    border-color: {BORDER_FOCUS};
}}

QLineEdit:focus,
QPlainTextEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus {{
    border-color: {ACCENT};
}}

QSpinBox::up-button,
QSpinBox::down-button,
QDoubleSpinBox::up-button,
QDoubleSpinBox::down-button {{
    width: 0;
    height: 0;
    border: none;
}}

/* ── combo boxes ───────────────────────────────────────────────────── */

QComboBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 32px 8px 12px;
    font-size: 13px;
    min-width: 180px;
}}

QComboBox:hover {{
    border-color: {BORDER_FOCUS};
}}

QComboBox:focus {{
    border-color: {ACCENT};
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox QAbstractItemView {{
    background-color: {BG};
    color: {TEXT_PRIMARY};
    selection-background-color: {HOVER_BG};
    selection-color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    outline: none;
}}

/* ── checkboxes ────────────────────────────────────────────────────── */

QCheckBox {{
    color: {TEXT_PRIMARY};
    spacing: 10px;
    font-weight: 400;
    background-color: transparent;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid #404040;
    border-radius: 3px;
    background-color: transparent;
}}

QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    image: none;
}}

QCheckBox::indicator:hover {{
    border-color: #808080;
}}

/* ── sliders ───────────────────────────────────────────────────────── */

QSlider::groove:horizontal {{
    height: 4px;
    background-color: #333333;
    border-radius: 2px;
    margin: 0;
}}

QSlider::sub-page:horizontal {{
    background-color: {ACCENT};
    height: 4px;
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background-color: {ACCENT};
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
}}

QSlider::handle:horizontal:hover {{
    background-color: #5b9cf7;
}}

/* ── push buttons ──────────────────────────────────────────────────── */

QPushButton {{
    background-color: transparent;
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 12px;
    font-weight: 500;
}}

QPushButton:hover {{
    border-color: {BORDER_FOCUS};
    background-color: {HOVER_BG};
}}

QPushButton:pressed {{
    background-color: {BORDER};
}}

QPushButton:disabled {{
    color: #404040;
    border-color: #282828;
}}

/* ── small button (objectName=btnSmall) ────────────────────────────── */

QPushButton#btnSmall {{
    padding: 5px 10px;
    font-size: 11px;
}}

/* ── hotkey capture button ─────────────────────────────────────────── */

QPushButton#hotkeyCapture {{
    font-family: Consolas, "SF Mono", monospace;
    font-size: 12px;
    padding: 6px 16px;
    min-width: 120px;
    letter-spacing: 0.5px;
}}

/* ── scroll area (transparent background for card containers) ──────── */

QScrollArea {{
    background-color: {BG};
    border: none;
}}

QScrollArea > QWidget > QWidget {{
    background-color: {BG};
}}

/* ── scrollbars ────────────────────────────────────────────────────── */

QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: #333333;
    border-radius: 3px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background: #555555;
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: #333333;
    border-radius: 3px;
    min-width: 20px;
}}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── status bar ────────────────────────────────────────────────────── */

QStatusBar {{
    color: {TEXT_SECONDARY};
    background-color: {BG};
    border-top: 1px solid {BORDER};
    font-size: 11px;
}}

QStatusBar::item {{
    border: none;
}}

/* ── tooltips ──────────────────────────────────────────────────────── */

QToolTip {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 12px;
}}

/* ── message boxes ─────────────────────────────────────────────────── */

QMessageBox {{
    background-color: {BG};
    color: {TEXT_PRIMARY};
}}

QMessageBox QPushButton {{
    min-width: 80px;
}}

/* ── group boxes (legacy compatibility) ────────────────────────────── */

QGroupBox {{
    color: {ACCENT};
    border: none;
    border-radius: 0;
    margin-top: 24px;
    padding-top: 24px;
    font-size: 12px;
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 4px;
    padding: 0 4px;
    color: {ACCENT};
}}
/* -- command tag colors (Commands tab) -------------------------------- */

QLabel#cmdTagEN {{
    background-color: rgba(59, 130, 246, 0.1);
    color: #7cb3f4;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
}}

QLabel#cmdTagRU {{
    background-color: rgba(239, 68, 68, 0.1);
    color: #f87171;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
}}

QLabel#cmdTagUK {{
    background-color: rgba(34, 197, 94, 0.1);
    color: #4ade80;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
}}

QPushButton#langBtn {{
    border: none;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 500;
    color: {TEXT_SECONDARY};
    background-color: transparent;
}}

QPushButton#langBtn:checked {{
    color: {ACCENT};
    background-color: {ACCENT_DIM};
}}
"""


def apply_theme(window: QWidget) -> None:
    """Apply the sidebar dark theme to *window* and all children.
/* ── command tag colors (Commands tab) ─────────────────────────── */

QLabel#cmdTagEN {{
    background-color: rgba(59, 130, 246, 0.1);
    color: #7cb3f4;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
}}

QLabel#cmdTagRU {{
    background-color: rgba(239, 68, 68, 0.1);
    color: #f87171;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
}}

QLabel#cmdTagUK {{
    background-color: rgba(34, 197, 94, 0.1);
    color: #4ade80;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
}}

QPushButton#langBtn {{
    border: none;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 500;
    color: {TEXT_SECONDARY};
    background-color: transparent;
}}

QPushButton#langBtn:checked {{
    color: {ACCENT};
    background-color: {ACCENT_DIM};
}}
"""
    window.setStyleSheet(_QSS)

