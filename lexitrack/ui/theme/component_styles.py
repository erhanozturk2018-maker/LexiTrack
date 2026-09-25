"""Stylesheet rules for the list, table and navigation components.

Kept apart from :mod:`.stylesheet` so each file stays readable. Both are
generated from the same palette, so the two themes stay consistent by
construction.
"""

from __future__ import annotations

from pathlib import Path

from .palette import DISPLAY_FONT_STACK, METRICS, Palette

_ICONS = Path(__file__).with_name("icons")


def component_rules(p: Palette) -> str:
    m = METRICS
    chevron = (_ICONS / f"chevron-down-{p.name.value}.svg").as_posix()
    check = (_ICONS / f"check-{p.name.value}.svg").as_posix()
    return f"""
/* ------------------------------------------------------------ page chrome */

#PageTitle {{
    font-family: {DISPLAY_FONT_STACK};
    font-size: 22px;
    font-weight: 600;
    color: {p.text};
}}

#PageSubtitle {{
    font-size: 13px;
    color: {p.text_muted};
}}

#SectionTitle {{
    font-size: 11px;
    font-weight: 600;
    color: {p.text_faint};
    letter-spacing: 0.8px;
}}

#ContextLabel {{
    font-size: 12px;
    font-weight: 600;
    color: {p.text_faint};
    letter-spacing: 0.6px;
}}

#ContextName {{
    font-family: {DISPLAY_FONT_STACK};
    font-size: 17px;
    font-weight: 600;
    color: {p.text};
}}

/* The current list's name, which is also the button that switches list. */
QPushButton#ContextListButton {{
    background: transparent;
    border: none;
    color: {p.text};
    font-family: {DISPLAY_FONT_STACK};
    font-size: 18px;
    font-weight: 600;
    padding: 1px 6px 1px 0;
    text-align: left;
    min-height: 0;
}}
QPushButton#ContextListButton:hover {{ color: {p.accent}; }}
QPushButton#ContextListButton::menu-indicator {{ image: none; width: 0; }}

#Muted {{ color: {p.text_muted}; font-size: 13px; }}
#ErrorText {{ color: {p.danger}; font-size: 13px; }}
#WarningText {{ color: {p.unknown_text}; font-size: 13px; }}
#Faint {{ color: {p.text_faint}; font-size: 12px; }}

/* Plain containers inside a panel show the panel through, instead of painting
   the window colour as a grey block. */
#PanelBody {{ background: transparent; }}
QCheckBox#FileCheck {{ font-size: 15px; font-weight: 600; }}

/* A raised panel. Used sparingly: most structure is whitespace. */
#Panel {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_lg}px;
}}

/* ------------------------------------------------------------- navigation */

/* The sidebar: items are painted (components/sidebar.py); these rules give
   the column its surface and style the group headings between them. */
#Sidebar {{
    background-color: {p.surface};
    border-right: 1px solid {p.border};
}}
QLabel#SidebarSection {{
    color: {p.text_faint};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.7px;
}}
#SidebarFoot {{ background: transparent; }}
#SidebarRule {{
    background-color: {p.border};
    margin: 8px 6px;
}}

/* Segmented control: Flashcard | List. */
#ModeSwitch {{
    background-color: {p.surface_sunken};
    border-radius: {m.radius_md}px;
}}
QPushButton#ModeButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    color: {p.text_muted};
    padding: 5px 16px;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton#ModeButton:hover {{ color: {p.text}; }}
QPushButton#ModeButton:checked {{
    background-color: {p.surface_raised};
    border: 1px solid {p.border};
    color: {p.text};
}}

/* ------------------------------------------------------------ list cards */

#ListCard {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
}}
#ListCard:hover {{ border-color: {p.border_strong}; }}
#ListCard[current="true"] {{ border: 1px solid {p.accent}; }}
/* Keyboard focus is always visible and distinct from "current": a thicker
   border and a tinted background, not just the accent outline. */
#ListCard:focus {{
    border: 2px solid {p.focus_ring};
    background-color: {p.accent_soft};
}}


/* Transfer result with Undo: an inverted chip that floats over the page. */
#Toast {{
    background-color: {p.inverse};
    border-radius: {m.radius_md}px;
}}
#ToastText {{ color: {p.inverse_text}; font-size: 13px; }}
QPushButton#ToastAction {{
    background: transparent;
    border: none;
    color: {p.accent_soft};
    font-weight: 600;
    padding: 4px 8px;
    min-height: 0;
}}
QPushButton#ToastAction:hover {{ text-decoration: underline; }}

/* Export: settings on the left, the file as it will be on the right. */
#ExportSettings {{
    background-color: {p.surface};
    border-right: 1px solid {p.border};
}}
#ExportPreview {{ background-color: {p.surface_sunken}; }}
QScrollArea#PreviewScroll, QScrollArea#PreviewScroll > QWidget > QWidget {{
    background: transparent;
}}
#PreviewPage {{ background: transparent; }}
QPlainTextEdit#PreviewText {{
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 12px;
    background-color: {p.surface};
}}

#ListPicker {{
    background-color: {p.surface_raised};
    border: 1px solid {p.border_strong};
    border-radius: {m.radius_md}px;
}}

#ListCardName {{
    font-size: 15px;
    font-weight: 600;
    color: {p.text};
}}
#ListCardMeta {{ font-size: 12px; color: {p.text_faint}; }}
#ListCardPercent {{ font-size: 13px; font-weight: 600; color: {p.text_muted}; }}

#LanguageTag {{
    background-color: {p.surface_sunken};
    color: {p.text_muted};
    border-radius: 4px;
    padding: 1px 6px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
}}

/* ---------------------------------------------------------- icon buttons */

QPushButton#IconButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {m.radius_sm}px;
    padding: 7px;
    min-height: 0;
}}
QPushButton#IconButton:hover {{ background-color: {p.surface_sunken}; }}
QPushButton#IconButton::menu-indicator {{ image: none; width: 0; }}

#CommandPalette {{
    background-color: {p.surface_raised};
    border: 1px solid {p.border_strong};
    border-radius: {m.radius_lg}px;
}}
QLineEdit#PaletteSearch {{
    font-size: 16px;
    padding: 10px 12px;
    border: none;
    border-bottom: 1px solid {p.border};
    border-radius: 0;
    background: transparent;
}}
QListWidget#PaletteResults {{ border: none; background: transparent; padding: 0; }}

#KeyCap {{
    background-color: {p.surface_sunken};
    border: 1px solid {p.border_strong};
    border-bottom-width: 2px;
    border-radius: 5px;
    color: {p.text};
    padding: 1px 7px;
    font-size: 12px;
    font-weight: 600;
}}

/* ------------------------------------------------------------ stat tiles */

#StatTile {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
}}
#StatTileValue {{
    font-family: {DISPLAY_FONT_STACK};
    font-size: 24px;
    font-weight: 600;
    color: {p.text};
}}
#StatTileValue[tone="known"] {{ color: {p.known}; }}
#StatTileValue[tone="unknown"] {{ color: {p.unknown}; }}
#StatTileLabel {{ font-size: 12px; color: {p.text_muted}; }}
#StatTile[clickable="true"]:hover {{ border-color: {p.unknown}; }}
#StatTile[clickable="true"]:focus {{ border: 2px solid {p.focus_ring}; }}

/* --------------------------------------------------------- status badges */

/* Status is never colour alone: every badge carries a symbol and a word. */
#StatusBadge {{
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 12px;
    font-weight: 600;
}}
#StatusBadge[status="known"] {{
    background-color: {p.known_soft};
    color: {p.known_text};
}}
#StatusBadge[status="unknown"] {{
    background-color: {p.unknown_soft};
    color: {p.unknown_text};
}}
#StatusBadge[status="not_reviewed"] {{
    background-color: {p.surface_sunken};
    color: {p.text_muted};
}}

#HistoryBanner {{
    background-color: {p.accent_soft};
    color: {p.text};
    border-radius: {m.radius_sm}px;
    padding: 4px 10px;
    font-size: 12px;
}}

/* ----------------------------------------------------------------- inputs */

QLineEdit, QPlainTextEdit {{
    background-color: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {m.radius_sm}px;
    padding: 7px 10px;
    selection-background-color: {p.accent};
    selection-color: {p.text_on_accent};
}}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {p.focus_ring}; }}
QLineEdit:disabled {{ color: {p.text_faint}; background-color: {p.surface_sunken}; }}

QComboBox {{
    background-color: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {m.radius_sm}px;
    padding: 6px 30px 6px 10px;
    min-height: 20px;
}}
/* Styling the drop-down removes Fusion's own arrow, so it draws its own. */
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url("{chevron}");
    width: 12px;
    height: 12px;
}}
QComboBox:hover {{ border-color: {p.text_faint}; }}
QComboBox:focus {{ border: 1px solid {p.focus_ring}; }}
QComboBox QAbstractItemView {{
    background-color: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border};
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
    outline: none;
}}

QCheckBox {{ color: {p.text}; spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {p.border_strong};
    border-radius: 4px;
    background-color: {p.surface};
}}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{
    background-color: {p.accent};
    border-color: {p.accent};
    image: url("{check}");
}}

QListWidget {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_sm}px;
    outline: none;
    padding: 4px;
}}
QListWidget::item {{ padding: 6px 8px; border-radius: 4px; color: {p.text}; }}
QListWidget::item:hover {{ background-color: {p.surface_sunken}; }}
QListWidget::item:selected {{ background-color: {p.accent_soft}; color: {p.text}; }}

/* Settings navigation: a sidebar, not a boxed list. */
QListWidget#SettingsNav {{
    background-color: {p.surface};
    border: none;
    border-right: 1px solid {p.border};
    border-radius: 0;
    padding: {m.space_3}px {m.space_2}px;
}}
QListWidget#SettingsNav::item {{ padding: 8px 12px; margin-bottom: 2px; }}
QListWidget#SettingsNav::item:selected {{ color: {p.accent}; font-weight: 600; }}

/* Number fields match the text fields; Fusion's own frame ignores the theme. */
QSpinBox, QDoubleSpinBox {{
    background-color: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {m.radius_sm}px;
    padding: 6px 8px;
    min-height: 20px;
    selection-background-color: {p.accent};
    selection-color: {p.text_on_accent};
}}
QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {p.focus_ring}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {p.text_faint};
    background-color: {p.surface_sunken};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 18px;
    border: none;
    background: transparent;
}}

/* ----------------------------------------------------------------- tables */

QTableView {{
    background-color: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
    gridline-color: transparent;
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
    outline: none;
}}
QTableView::item {{
    padding: 0 10px;
    border-bottom: 1px solid {p.border};
}}
QTableView::item:selected {{ background-color: {p.accent_soft}; color: {p.text}; }}
QHeaderView {{ background-color: {p.surface}; border: none; }}
QHeaderView::section {{
    background-color: {p.surface};
    color: {p.text_faint};
    border: none;
    border-bottom: 1px solid {p.border_strong};
    padding: 9px 10px;
    font-size: 11px;
    font-weight: 600;
}}
QHeaderView::section:hover {{ color: {p.text}; }}
QTableCornerButton::section {{ background-color: {p.surface}; border: none; }}

/* The selection bar floats over the table: an inverted pill, so it reads as
   a temporary tool rather than part of the page. */
#SelectionBar {{
    background-color: {p.inverse};
    border-radius: 21px;
}}
#SelectionCount {{ font-weight: 600; color: {p.inverse_text}; background: transparent; }}
#BarDivider {{ background-color: {p.inverse_muted}; margin: 0 6px; }}
QPushButton#BarButton {{
    background: transparent;
    border: none;
    border-radius: 15px;
    color: {p.inverse_text};
    padding: 6px 12px;
    font-size: 13px;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton#BarButton:hover, QPushButton#BarButton:focus {{
    background-color: {p.inverse_hover};
    outline: none;
}}
QPushButton#BarButton::menu-indicator {{ image: none; width: 0; }}

/* CEFR filter chips beside the search. */
QPushButton#LevelChip {{
    background: transparent;
    border: 1px solid {p.border_strong};
    border-radius: 13px;
    color: {p.text_muted};
    padding: 3px 10px;
    font-size: 12px;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton#LevelChip:hover {{ color: {p.text}; border-color: {p.text_faint}; }}
QPushButton#LevelChip:checked {{
    background-color: {p.accent_soft};
    border-color: {p.accent};
    color: {p.accent};
}}
QPushButton#DetailsToggle:checked {{
    background-color: {p.accent_soft};
    border-color: {p.accent};
    color: {p.text};
}}

/* The table's frame; the view inside draws no border of its own. */
#TableFrame {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
}}
#TableFrame QTableView {{ border: none; border-radius: 0; }}
QPushButton#PanelStatusButton {{
    padding: 6px 4px;
    font-size: 13px;
    min-height: 16px;
}}

/* Word details beside the table. */
#WordPanel {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
}}
QScrollArea#PanelScroll, QScrollArea#PanelScroll > QWidget > QWidget {{
    background: transparent;
}}
#PanelWord {{
    font-family: {DISPLAY_FONT_STACK};
    font-size: 26px;
    font-weight: 600;
    color: {p.text};
}}
#PanelExample {{ color: {p.text_muted}; font-style: italic; }}
#PanelNote {{ color: {p.text_muted}; }}
#NoteLabel {{ color: {p.text_faint}; font-size: 13px; }}

QPushButton[variant="danger"] {{
    color: {p.danger};
    border: 1px solid {p.border_strong};
}}
QPushButton[variant="danger"]:hover {{
    background-color: {p.danger};
    border-color: {p.danger};
    color: {p.text_on_accent};
}}

QPushButton[size="small"] {{
    padding: 5px 12px;
    font-size: 13px;
    min-height: 16px;
}}

/* "size" is a property every QWidget already has (its width and height), so
   the rule above never matches; "compact" is free. */
QPushButton[compact="true"] {{
    padding: 4px 12px;
    font-size: 13px;
    min-height: 16px;
}}
"""
