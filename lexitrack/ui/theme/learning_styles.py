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
/* The four answers, in the colours of the answer buttons. */
#Chip[tone="again"] {{
    background-color: {p.unknown_soft}; color: {p.unknown_text}; border-color: {p.unknown_soft};
}}
#Chip[tone="hard-answer"] {{ color: {p.text}; }}
#Chip[tone="good"] {{
    background-color: {p.known_soft}; color: {p.known_text}; border-color: {p.known_soft};
}}
#Chip[tone="easy"] {{
    background-color: {p.known}; color: {p.text_on_accent}; border-color: {p.known};
}}
#Chip[clickable="true"]:hover {{ border-color: {p.accent}; }}
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
/* An action that lives in the card's footer: footer-sized, so it does not
   compete with the four answers, and clearly a link when pointed at. */
QPushButton#FooterAction {{
    font-size: 12px;
    color: {p.text_muted};
    background: transparent;
    border: none;
    padding: 2px 6px;
}}
QPushButton#FooterAction:hover {{ color: {p.accent}; }}
QPushButton#FooterAction:focus {{ color: {p.accent}; }}
/* A text link in a column of text: no padding, so it starts where the
   text above it starts. */
QPushButton#LinkButton {{
    font-size: 13px;
    color: {p.accent};
    background: transparent;
    border: none;
    padding: 2px 0;
    text-align: left;
}}
QPushButton#LinkButton:hover {{ text-decoration: underline; }}
QPushButton#LinkButton:focus {{ text-decoration: underline; }}

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
#GroupTitle {{
    font-size: 11px;
    font-weight: 600;
    color: {p.text_faint};
    letter-spacing: 0.8px;
    background: transparent;
}}

/* ------------------------------------------------------------ first-run setup */

#SetupIntro {{ font-size: 14px; color: {p.text_muted}; background: transparent; }}
#SetupNumber {{
    font-size: 12px; font-weight: 600;
    color: {p.accent}; background-color: {p.accent_soft};
    border-radius: 12px;
}}
#SetupWarning {{ font-size: 13px; color: {p.unknown_text}; background: transparent; }}

/* ------------------------------------------------------------ word history */

#HistoryFact {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
}}
#HistoryFactCaption {{
    font-size: 10px; font-weight: 600; letter-spacing: 0.6px;
    color: {p.text_faint}; background: transparent;
}}
#HistoryFactValue {{ font-size: 13px; color: {p.text}; background: transparent; }}
#HistoryWhy {{
    font-size: 13px; color: {p.text};
    background-color: {p.accent_soft};
    border-radius: {m.radius_md}px;
    padding: 10px 12px;
}}
#HistorySteps {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
}}
#HistoryStep {{ background: transparent; border-top: 1px solid {p.border}; }}
#HistoryStep[first="true"] {{ border-top: none; }}
#HistoryDay {{ font-size: 12px; color: {p.text_faint}; background: transparent; }}
#HistoryDetail {{ font-size: 13px; color: {p.text}; background: transparent; }}
#HistoryDetail[undone="true"] {{ color: {p.text_faint}; text-decoration: line-through; }}
#HistoryUndone {{ font-size: 11px; color: {p.unknown_text}; background: transparent; }}

/* -------------------------------------------------------------- study plan */

#PlanListRow {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {m.radius_md}px;
}}
#PlanListRow[selected="true"] {{ border-color: {p.accent}; background-color: {p.accent_soft}; }}
#PlanListName {{ font-size: 14px; font-weight: 600; color: {p.text}; background: transparent; }}
#PlanListMeta {{ font-size: 12px; color: {p.text_faint}; background: transparent; }}
/* Rows stand aside while All my lists is ticked. The labels carry the state
   themselves: Qt ignores :disabled on an ancestor in a descendant selector. */
#PlanListName:disabled {{ color: {p.text_faint}; }}
#PlanListMeta:disabled {{ color: {p.text_faint}; }}
#PlanListRow:disabled {{ background-color: {p.surface_sunken}; }}
#SummaryBox {{
    background-color: {p.accent_soft};
    border-radius: {m.radius_md}px;
}}
#SummaryText {{ font-size: 13px; color: {p.text}; background: transparent; }}
/* ------------------------------------------------------- review card, V2 */

#TaskLabel {{
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.7px;
    color: {p.text_faint};
    background: transparent;
}}
/* The question: a meaning, a sentence with a gap, a phrase. Large enough to
   be the thing on screen, smaller than a word on its own. */
#PromptLabel {{
    font-family: {DISPLAY_FONT_STACK};
    font-size: 24px;
    font-weight: 500;
    color: {p.text};
    background: transparent;
}}
#PromptDetail {{ font-size: 14px; color: {p.text_muted}; background: transparent; }}
QLineEdit#AnswerInput {{
    font-size: 22px;
    padding: 10px 14px;
    border-radius: {m.radius_md}px;
    border: 1px solid {p.border_strong};
    min-height: 30px;
}}
QLineEdit#AnswerInput:focus {{ border: 2px solid {p.focus_ring}; }}
QLineEdit#AnswerInput[result="right"], QLineEdit#AnswerInput[result="near"] {{
    border: 2px solid {p.known};
    background-color: {p.known_soft};
    color: {p.known_text};
}}
QLineEdit#AnswerInput[result="wrong"] {{
    border: 2px solid {p.unknown};
    background-color: {p.unknown_soft};
    color: {p.unknown_text};
}}
QLineEdit#SentenceInput {{ font-size: 16px; padding: 10px 12px; }}
#HintLabel {{
    font-family: {DISPLAY_FONT_STACK};
    font-size: 18px;
    letter-spacing: 1px;
    color: {p.text_muted};
    background: transparent;
}}
QPushButton#AnswerButton[result="right"] {{
    background-color: {p.known_soft};
    border: 2px solid {p.known};
}}
QPushButton#AnswerButton[result="wrong"] {{
    background-color: {p.unknown_soft};
    border: 2px solid {p.unknown};
}}
QPushButton#AnswerButton[result="right"] #AnswerTitle {{ color: {p.known_text}; }}
QPushButton#AnswerButton[result="wrong"] #AnswerTitle {{ color: {p.unknown_text}; }}
#FeedbackLabel {{ font-size: 14px; color: {p.text_muted}; background: transparent; }}
#FeedbackLabel[tone="good"] {{ color: {p.known_text}; font-weight: 600; }}
#FeedbackLabel[tone="bad"] {{ color: {p.unknown_text}; }}
#ExamplesLabel, #TeachText {{
    font-size: 14px;
    color: {p.text};
    background-color: {p.surface_sunken};
    border-radius: {m.radius_md}px;
    padding: 12px 14px;
}}

/* ------------------------------------------------------- the day, one card */

#TodayHeadline {{
    font-family: {DISPLAY_FONT_STACK};
    font-size: 22px;
    font-weight: 600;
    color: {p.text};
    background: transparent;
}}
#TodayDetail {{ font-size: 14px; color: {p.text_muted}; background: transparent; }}
"""
