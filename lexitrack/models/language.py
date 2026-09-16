"""Language codes.

Language is part of vocabulary identity: English "gift" and German "Gift" are
different words and must never be merged. Codes are short lowercase ISO 639-1
style tags. ``und`` (the ISO 639 code for "undetermined") marks vocabulary
whose language could not be established, and a list with language ``und`` may
hold words of any language.

This is deliberately a small table rather than a locale library: LexiTrack only
needs to store, compare and display a language, not to process it.
"""

from __future__ import annotations

import re

UNDETERMINED = "und"

#: Languages offered in the UI, in display order. Any other well-formed code is
#: still accepted from a JSON file and shown as the code itself.
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "tr": "Turkish",
    "ru": "Russian",
    "ja": "Japanese",
    "zh": "Chinese",
    UNDETERMINED: "Unspecified",
}

_NAME_TO_CODE = {name.casefold(): code for code, name in LANGUAGE_NAMES.items()}
_CODE_RE = re.compile(r"^[a-z]{2,3}$")


def normalize_language(value: str | None) -> str | None:
    """Return a language code for ``value``, or ``None`` if it is not one.

    Accepts a code in any case (``"DE"``), or an English language name
    (``"German"``). An empty value means undetermined.

    >>> normalize_language("DE")
    'de'
    >>> normalize_language("German")
    'de'
    >>> normalize_language("")
    'und'
    >>> normalize_language("Klingon!") is None
    True
    """
    if value is None:
        return UNDETERMINED
    text = str(value).strip()
    if not text:
        return UNDETERMINED
    folded = text.casefold()
    if folded in _NAME_TO_CODE:
        return _NAME_TO_CODE[folded]
    # Accept regional tags such as "en-GB" by their primary subtag.
    primary = folded.replace("_", "-").split("-", 1)[0]
    if _CODE_RE.match(primary):
        return primary
    return None


def language_name(code: str | None) -> str:
    """Human readable name for ``code``."""
    if not code:
        return LANGUAGE_NAMES[UNDETERMINED]
    return LANGUAGE_NAMES.get(code, code.upper())


def is_determined(code: str | None) -> bool:
    return bool(code) and code != UNDETERMINED
