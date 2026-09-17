"""Building the Qt stylesheet for a palette.

The whole application is styled from one generated sheet rather than from
per-widget code, which is what keeps the two themes consistent: a button looks
the same everywhere because it is described once.

Widgets opt into a look with ``setObjectName`` or a ``class`` property, e.g.
``button.setProperty("variant", "known")``.
"""

from __future__ import annotations

from .component_styles import component_rules
from .palette import DISPLAY_FONT_STACK, METRICS, UI_FONT_STACK, Palette


def build_stylesheet(palette: Palette) -> str:
    """Return the full Qt stylesheet for ``palette``."""
    p = palette
    m = METRICS
    return _base_rules(p, m) + component_rules(p)


def _base_rules(p: Palette, m) -> str:
    return f"""
/* ---------------------------------------------------------------- base */

QWidget {{
    background-color: {p.background};
    color: {p.text};
    font-family: {UI_FONT_STACK};
    font-size: 14px;
}}

QMainWindow, QDialog {{
    background-color: {p.background};
}}

/* Labels inherit the window colour by default, which paints a visible chip
   behind every piece of text sitting on a different surface. */
QLabel {{
    background: transparent;
}}

QToolTip {{
    background-color: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: {m.radius_sm}px;
    padding: 6px 8px;
}}

/* ------------------------------------------------------------ app bar */

#AppBar {{
    background-color: {p.surface};
    border-bottom: 1px solid {p.border};
}}

#AppTitle {{
    font-family: {DISPLAY_FONT_STACK};
    font-size: 15px;
    font-weight: 600;
    color: {p.text};
    letter-spacing: 0.2px;
}}

#AppBarStatus {{
    font-size: 13px;
    color: {p.text_muted};
}}

/* ------------------------------------------------------------ surfaces */

#ReviewCard {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_lg}px;
}}

#StatsBar {{
    background-color: {p.surface};
    border-top: 1px solid {p.border};
}}

/* -------------------------------------------------------- review screen */

#SourceChip {{
    background-color: {p.surface_sunken};
    color: {p.text_muted};
    border-radius: {m.radius_sm}px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.4px;
}}

#WordLabel {{
    font-family: {DISPLAY_FONT_STACK};
    font-size: 58px;
    font-weight: 600;
    color: {p.text};
    letter-spacing: -0.5px;
    background: transparent;
}}

#MetaLabel {{
    font-size: 15px;
    color: {p.text_muted};
    background: transparent;
}}

#SenseLabel {{
    font-size: 13px;
    color: {p.text_faint};
    font-style: italic;
    background: transparent;
}}

#DefinitionLabel {{
    font-size: 14px;
    color: {p.text_muted};
    background: transparent;
}}

#PositionLabel {{
    font-size: 13px;
    color: {p.text_faint};
    background: transparent;
}}

/* --------------------------------------------------------- empty states */

#EmptyTitle {{
    font-family: {DISPLAY_FONT_STACK};
    font-size: 26px;
    font-weight: 600;
    color: {p.text};
    background: transparent;
}}

#EmptyBody {{
    font-size: 15px;
    color: {p.text_muted};
    background: transparent;
}}

#EmptyGlyph {{
    font-size: 44px;
    color: {p.text_faint};
    background: transparent;
}}

/* --------------------------------------------------------------- buttons */

QPushButton {{
    background-color: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {m.radius_md}px;
    padding: 9px 16px;
    font-size: 14px;
    font-weight: 500;
    min-height: 20px;
}}

QPushButton:hover {{
    background-color: {p.surface_sunken};
    border-color: {p.border_strong};
}}

QPushButton:pressed {{
    background-color: {p.surface_sunken};
}}

QPushButton:focus {{
    border: 1px solid {p.focus_ring};
    outline: none;
}}

QPushButton:disabled {{
    color: {p.text_faint};
    background-color: {p.surface_sunken};
    border-color: {p.border};
}}

/* Primary action, e.g. Import in the empty state. */
QPushButton[variant="primary"] {{
    background-color: {p.accent};
    color: {p.text_on_accent};
    border: 1px solid {p.accent};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{ background-color: {p.accent_hover};
                                        border-color: {p.accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background-color: {p.accent_pressed}; }}
QPushButton[variant="primary"]:disabled {{
    background-color: {p.surface_sunken};
    border-color: {p.border};
    color: {p.text_faint};
}}

/* Quiet action, e.g. Undo or Skip. */
QPushButton[variant="ghost"] {{
    background: transparent;
    border: 1px solid transparent;
    color: {p.text_muted};
    font-weight: 500;
    padding: 6px 10px;
}}
QPushButton[variant="ghost"]:hover {{
    background-color: {p.surface_sunken};
    color: {p.text};
}}
QPushButton[variant="ghost"]:disabled {{
    color: {p.text_faint};
    background: transparent;
}}

/* The two answer buttons. Equal weight, distinguished by hue, not size. */
QPushButton[variant="known"], QPushButton[variant="unknown"] {{
    font-size: 15px;
    font-weight: 600;
    padding: 15px 20px;
    border-radius: {m.radius_md}px;
    min-height: 26px;
    letter-spacing: 0.2px;
}}

QPushButton[variant="known"] {{
    background-color: {p.known_soft};
    color: {p.known_text};
    border: 1px solid {p.known};
}}
QPushButton[variant="known"]:hover {{
    background-color: {p.known};
    color: {p.text_on_accent};
    border-color: {p.known};
}}
QPushButton[variant="known"]:pressed {{ background-color: {p.known_hover};
                                        border-color: {p.known_hover}; }}
QPushButton[variant="known"]:focus {{ border: 2px solid {p.known}; }}

QPushButton[variant="unknown"] {{
    background-color: {p.unknown_soft};
    color: {p.unknown_text};
    border: 1px solid {p.unknown};
}}
QPushButton[variant="unknown"]:hover {{
    background-color: {p.unknown};
    color: {p.text_on_accent};
    border-color: {p.unknown};
}}
QPushButton[variant="unknown"]:pressed {{ background-color: {p.unknown_hover};
                                          border-color: {p.unknown_hover}; }}
QPushButton[variant="unknown"]:focus {{ border: 2px solid {p.unknown}; }}

/* Keyboard hint printed inside the answer buttons. */
#KeyHint {{
    color: {p.text_faint};
    font-size: 11px;
    font-weight: 600;
    background: transparent;
}}

/* --------------------------------------------------------- progress bar */

QProgressBar {{
    background-color: {p.surface_sunken};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}}

QProgressBar::chunk {{
    background-color: {p.accent};
    border-radius: 3px;
}}

/* ------------------------------------------------------------- statistics */

#StatValue {{
    font-size: 17px;
    font-weight: 600;
    color: {p.text};
    background: transparent;
}}

#StatValue[tone="known"] {{ color: {p.known}; }}
#StatValue[tone="unknown"] {{ color: {p.unknown}; }}

#StatLabel {{
    font-size: 11px;
    font-weight: 600;
    color: {p.text_faint};
    letter-spacing: 0.7px;
    background: transparent;
}}

#StatDivider {{
    background-color: {p.border};
}}

/* ------------------------------------------------------------------ menus */

QMenuBar {{
    background-color: {p.surface};
    color: {p.text};
    border: none;
    padding: 2px 4px;
}}
QMenuBar::item {{ background: transparent; padding: 6px 10px; border-radius: {m.radius_sm}px; }}
QMenuBar::item:selected {{ background-color: {p.surface_sunken}; }}

QMenu {{
    background-color: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 28px 7px 14px; border-radius: {m.radius_sm}px; }}
QMenu::item:selected {{ background-color: {p.accent_soft}; color: {p.text}; }}
QMenu::item:disabled {{ color: {p.text_faint}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 5px 8px; }}

/* ---------------------------------------------------------------- dialogs */

#DialogTitle {{
    font-family: {DISPLAY_FONT_STACK};
    font-size: 19px;
    font-weight: 600;
    color: {p.text};
    background: transparent;
}}

#DialogHint {{
    font-size: 13px;
    color: {p.text_muted};
    background: transparent;
}}

#FilePathLabel {{
    background-color: {p.surface_sunken};
    border: 1px solid {p.border};
    border-radius: {m.radius_sm}px;
    padding: 9px 12px;
    color: {p.text};
    font-size: 13px;
}}

#FilePathLabel[state="empty"] {{
    color: {p.text_faint};
}}

QRadioButton {{
    color: {p.text};
    font-size: 14px;
    spacing: 9px;
    padding: 5px 2px;
    background: transparent;
}}
/* A border thick enough to read as a dot defeats border-radius and Qt draws
   a square, so the checked state is a filled circle instead of a ring. */
QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 8px;
    border: 1px solid {p.border_strong};
    background-color: {p.surface};
}}
QRadioButton::indicator:hover {{
    border-color: {p.accent};
}}
QRadioButton::indicator:checked {{
    border: 1px solid {p.accent};
    background-color: {p.accent};
}}
QRadioButton:disabled {{ color: {p.text_faint}; }}

#RadioHint {{
    color: {p.text_muted};
    font-size: 12px;
    background: transparent;
}}

QGroupBox {{
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
    margin-top: 10px;
    padding: 14px 12px 10px 12px;
    font-weight: 600;
    color: {p.text_muted};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
    color: {p.text_muted};
    font-size: 12px;
    letter-spacing: 0.4px;
}}

/* -------------------------------------------------------------- scrollbar */

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p.border_strong};
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.text_faint}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

/* ------------------------------------------------------------- separators */

QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {p.border};
    background-color: {p.border};
    border: none;
    max-height: 1px;
}}
"""
