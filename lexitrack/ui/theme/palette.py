"""Colour and spacing tokens for the two themes.

Light and dark are designed independently rather than derived from one
another. Inverting a light palette produces dark surfaces that glare and muted
text that disappears; instead each theme picks its own surface ramp and then
tunes the accent and semantic hues to keep contrast comparable on both.

The review screen is looked at for hours at a time, so the palettes are
deliberately low-chroma: one accent for chrome and focus, and two semantic hues
for the answer buttons. "I don't know" is amber rather than red because it is
not a failure — it is how a word gets onto the study list.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ThemeName(StrEnum):
    LIGHT = "light"
    DARK = "dark"

    @property
    def label(self) -> str:
        return "Light" if self is ThemeName.LIGHT else "Dark"


@dataclass(frozen=True, slots=True)
class Palette:
    """One theme's colours."""

    name: ThemeName

    # Surfaces, from furthest back to nearest front.
    background: str
    surface: str
    surface_raised: str
    surface_sunken: str

    # Lines.
    border: str
    border_strong: str
    focus_ring: str

    # Type.
    text: str
    text_muted: str
    text_faint: str
    text_on_accent: str

    # Accent, used for chrome, progress and focus.
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str

    # Semantic colours for the two answers.
    known: str
    known_hover: str
    known_soft: str
    known_text: str

    unknown: str
    unknown_hover: str
    unknown_soft: str
    unknown_text: str

    # Feedback.
    danger: str
    overlay: str

    @property
    def is_dark(self) -> bool:
        return self.name is ThemeName.DARK


LIGHT = Palette(
    name=ThemeName.LIGHT,
    background="#F7F8FA",
    surface="#FFFFFF",
    surface_raised="#FFFFFF",
    surface_sunken="#EDEFF3",
    border="#E1E5EA",
    border_strong="#C9D0D8",
    focus_ring="#4A5FD0",
    text="#16191F",
    text_muted="#5B6573",
    text_faint="#8B94A1",
    text_on_accent="#FFFFFF",
    accent="#4A5FD0",
    accent_hover="#3E51B8",
    accent_pressed="#35459E",
    accent_soft="#EAEDFA",
    known="#0E8A62",
    known_hover="#0B7453",
    known_soft="#E4F4EE",
    known_text="#08644A",
    unknown="#B4650A",
    unknown_hover="#985508",
    unknown_soft="#FBF0E1",
    unknown_text="#8A4E07",
    danger="#C03030",
    overlay="rgba(20, 23, 28, 0.45)",
)

DARK = Palette(
    name=ThemeName.DARK,
    background="#14171C",
    surface="#1B1F26",
    surface_raised="#222730",
    surface_sunken="#101317",
    border="#2C323B",
    border_strong="#3B434F",
    focus_ring="#8CA0FF",
    text="#E7ECF3",
    text_muted="#98A3B2",
    text_faint="#6C7686",
    text_on_accent="#10131A",
    accent="#8CA0FF",
    accent_hover="#9FB0FF",
    accent_pressed="#7A8FF0",
    accent_soft="#232941",
    known="#41C79B",
    known_hover="#55D4AA",
    known_soft="#16302A",
    known_text="#7FE0C1",
    unknown="#E2A653",
    unknown_hover="#EDB768",
    unknown_soft="#332818",
    unknown_text="#F0C489",
    danger="#E06767",
    overlay="rgba(0, 0, 0, 0.55)",
)

PALETTES: dict[ThemeName, Palette] = {
    ThemeName.LIGHT: LIGHT,
    ThemeName.DARK: DARK,
}


def get_palette(name: ThemeName | str) -> Palette:
    """Return the palette for ``name``, falling back to light."""
    try:
        return PALETTES[ThemeName(name)]
    except ValueError:
        return LIGHT


# -- metrics ---------------------------------------------------------------
# One spacing scale shared by every widget, so alignment is automatic rather
# than negotiated per screen.


@dataclass(frozen=True, slots=True)
class Metrics:
    space_1: int = 4
    space_2: int = 8
    space_3: int = 12
    space_4: int = 16
    space_5: int = 24
    space_6: int = 32
    space_7: int = 48
    space_8: int = 64

    radius_sm: int = 6
    radius_md: int = 10
    radius_lg: int = 14

    #: Width the review column is clamped to, so the word never drifts far
    #: from the buttons on a wide monitor.
    content_max_width: int = 720


METRICS = Metrics()

#: Font stack: the platform UI face first, then reasonable fallbacks.
UI_FONT_STACK = '"Segoe UI Variable Text", "Segoe UI", Inter, system-ui, Roboto, sans-serif'
#: The word under review gets a display face with tighter tracking.
DISPLAY_FONT_STACK = '"Segoe UI Variable Display", "Segoe UI", Inter, system-ui, sans-serif'
