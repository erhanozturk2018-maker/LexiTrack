"""Stylesheet rules for the learning screens: Study, Study Plan, Settings.

Built from the same palette and metrics as every other screen, so the new
pages cannot drift from Home and Review: a chip is a pill in the list tag's
colours, a day tile is a stat tile, a settings group is a panel.
"""

from __future__ import annotations

from .palette import DISPLAY_FONT_STACK, METRICS, Palette


def learning_rules(p: Palette) -> str:
    m = METRICS
    return f"""
/* ------------------------------------------------------------- study page */

#TodayTitle {{
    font-family: {DISPLAY_FONT_STACK};
    font-size: 17px;
    font-weight: 600;
    color: {p.text};
    background: transparent;
}}
#StepGlyph {{ font-size: 15px; background: transparent; color: {p.text_faint}; }}
#StepGlyph[state="done"] {{ color: {p.known}; }}
#StepGlyph[state="active"] {{ color: {p.accent}; }}
#StepText {{ font-size: 15px; color: {p.text}; background: transparent; }}
#StepText[state="done"] {{ color: {p.text_muted}; }}
#StepText[state="active"] {{ font-weight: 600; }}
#StepMeta {{ font-size: 12px; color: {p.text_faint}; background: transparent; }}

#Chip {{
    background-color: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 13px;
    padding: 3px 11px;
    font-size: 13px;
}}
#Chip:hover {{ border-color: {p.border_strong}; }}
#Chip[tone="hard"] {{
    background-color: {p.unknown_soft};
    color: {p.unknown_text};
    border-color: {p.unknown_soft};
}}
#Chip[tone="done"] {{ color: {p.text_muted}; background-color: {p.surface_sunken}; }}
#LevelLabel {{
    color: {p.text_faint};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    background: transparent;
}}

#DayTile {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
}}
#DayTile[today="true"] {{ border: 1px solid {p.accent}; background-color: {p.accent_soft}; }}
#DayTile[over="true"] {{ border: 1px solid {p.unknown}; }}
#DayName {{ font-size: 11px; font-weight: 600; color: {p.text_faint}; background: transparent; }}
#DayTile[today="true"] #DayName {{ color: {p.accent}; }}
#DayCount {{ font-size: 18px; font-weight: 600; color: {p.text}; background: transparent; }}
#DayCount[empty="true"] {{ color: {p.text_faint}; }}

/* ---------------------------------------------------------- review session */

#SessionCard {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_lg}px;
}}
#CardFooter {{ font-size: 12px; color: {p.text_faint}; background: transparent; }}

QPushButton#AnswerButton {{
    border-radius: {m.radius_md}px;
    padding: 0;
    min-height: 58px;
}}
QPushButton#AnswerButton[variant="known-solid"] {{
    background-color: {p.known};
    border: 1px solid {p.known};
}}
QPushButton#AnswerButton[variant="known-solid"]:hover {{
    background-color: {p.known_hover};
    border-color: {p.known_hover};
}}
#AnswerTitle {{ font-size: 15px; font-weight: 600; background: transparent; color: {p.text}; }}
#AnswerSub {{ font-size: 11px; background: transparent; color: {p.text_muted}; }}
QPushButton[variant="known"] #AnswerTitle, QPushButton[variant="known"] #AnswerSub {{
    color: {p.known_text};
}}
QPushButton[variant="unknown"] #AnswerTitle, QPushButton[variant="unknown"] #AnswerSub {{
    color: {p.unknown_text};
}}
QPushButton[variant="known-solid"] #AnswerTitle,
QPushButton[variant="known-solid"] #AnswerSub {{
    color: {p.text_on_accent};
}}
/* Qt ignores pseudo-states on an ancestor in a descendant selector, so a
   ":hover #AnswerTitle" rule would apply all the time. Instead the answer
   buttons keep their soft fill on hover and only the border strengthens,
   which keeps the label colours right in both states. */
QPushButton#AnswerButton[variant="known"]:hover {{
    background-color: {p.known_soft};
    border: 2px solid {p.known};
}}
QPushButton#AnswerButton[variant="unknown"]:hover {{
    background-color: {p.unknown_soft};
    border: 2px solid {p.unknown};
}}
QPushButton#AnswerButton:!pressed:hover {{ border-width: 2px; }}
#AnswerKey {{
    background: transparent;
    border: 1px solid {p.border_strong};
    border-radius: 4px;
    padding: 0 5px;
    font-size: 11px;
    font-weight: 600;
    color: {p.text_muted};
}}
QPushButton[variant="known"] #AnswerKey {{ border-color: {p.known}; color: {p.known_text}; }}
QPushButton[variant="unknown"] #AnswerKey {{ border-color: {p.unknown}; color: {p.unknown_text}; }}
QPushButton[variant="known-solid"] #AnswerKey {{
    border-color: {p.text_on_accent};
    color: {p.text_on_accent};
}}

/* ---------------------------------------------------------------- settings */

#SettingsGroup {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
}}
#SettingRow {{ background: transparent; }}
#RowDivider {{ background-color: {p.border}; border: none; }}
#SettingTitle {{ font-size: 14px; color: {p.text}; background: transparent; }}
#SettingHint {{ font-size: 12px; color: {p.text_muted}; background: transparent; }}
/* A row whose right-hand side is an answer rather than a control. It is
   quieter than a title so the row still reads name first, answer second. */
#SettingValue {{ font-size: 14px; color: {p.text_muted}; background: transparent; }}
#GroupTitle {{
    font-size: 11px;
    font-weight: 600;
    color: {p.text_faint};
    letter-spacing: 0.8px;
    background: transparent;
}}

/* -------------------------------------------------------------- study plan */

#PlanListRow {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
}}
#PlanListRow[selected="true"] {{ border-color: {p.accent}; background-color: {p.accent_soft}; }}
#PlanListName {{ font-size: 14px; font-weight: 600; color: {p.text}; background: transparent; }}
#PlanListMeta {{ font-size: 12px; color: {p.text_faint}; background: transparent; }}
#SummaryBox {{
    background-color: {p.accent_soft};
    border-radius: {m.radius_md}px;
}}
#SummaryText {{ font-size: 13px; color: {p.text}; background: transparent; }}
"""
