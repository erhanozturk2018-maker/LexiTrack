"""A word's contexts: plain sentences that show its definition in use.

A context is English text and nothing else — no translation, no note, no
marker around the word. A word has one context when it has one common use,
more when its definition covers several, each showing one of them.

Where the word stands in a sentence is found here rather than stored: the
Context → Definition question shows the word picked out, and a context that
does not seem to contain its word is flagged when it is added or imported.
The word is found in its common forms too: *acquire* in "she acquired",
*sleep in* in "we slept in", *look up* in "look it up".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: The longest context accepted, in characters. A context is a sentence or
#: two, not a paragraph.
MAX_CONTEXT_LENGTH = 400


@dataclass(frozen=True, slots=True)
class WordContext:
    """One sentence using the word."""

    word_id: int
    text: str
    id: int | None = None


def clean_context(text: str | None) -> str:
    """A context as stored: spacing made single, ends trimmed, and any
    ``{{word}}`` marker from older files removed."""
    value = unicodedata.normalize("NFC", text or "")
    value = re.sub(r"\{\{\s*([^{}]*?)\s*\}\}", r"\1", value)
    return " ".join(value.split())


def same_context(a: str, b: str) -> bool:
    """Whether two contexts are the same sentence, ignoring case and spacing."""
    return clean_context(a).casefold() == clean_context(b).casefold()


# -- finding the word in a sentence ------------------------------------------------

#: Irregular forms, by base form. Enough of English's irregular verbs,
#: plurals and comparisons to find a word in an ordinary sentence.
IRREGULAR: dict[str, tuple[str, ...]] = {
    "arise": ("arose", "arisen"), "awake": ("awoke", "awoken"),
    "be": ("am", "is", "are", "was", "were", "been", "being"),
    "bear": ("bore", "borne", "born"), "beat": ("beaten",), "become": ("became",),
    "begin": ("began", "begun"), "bend": ("bent",), "bet": (), "bid": (),
    "bind": ("bound",), "bite": ("bit", "bitten"), "bleed": ("bled",),
    "blow": ("blew", "blown"), "break": ("broke", "broken"), "breed": ("bred",),
    "bring": ("brought",), "broadcast": (), "build": ("built",),
    "burn": ("burnt",), "burst": (), "buy": ("bought",), "cast": (),
    "catch": ("caught",), "choose": ("chose", "chosen"), "cling": ("clung",),
    "come": ("came",), "cost": (), "creep": ("crept",), "cut": (),
    "deal": ("dealt",), "dig": ("dug",), "do": ("did", "done", "does"),
    "draw": ("drew", "drawn"), "dream": ("dreamt",), "drink": ("drank", "drunk"),
    "drive": ("drove", "driven"), "dwell": ("dwelt",), "eat": ("ate", "eaten"),
    "fall": ("fell", "fallen"), "feed": ("fed",), "feel": ("felt",),
    "fight": ("fought",), "find": ("found",), "flee": ("fled",),
    "fling": ("flung",), "fly": ("flew", "flown", "flies"), "forbid": ("forbade", "forbidden"),
    "forecast": (), "foresee": ("foresaw", "foreseen"), "forget": ("forgot", "forgotten"),
    "forgive": ("forgave", "forgiven"), "freeze": ("froze", "frozen"),
    "get": ("got", "gotten"), "give": ("gave", "given"), "go": ("went", "gone", "goes"),
    "grind": ("ground",), "grow": ("grew", "grown"), "hang": ("hung",),
    "have": ("has", "had", "having"), "hear": ("heard",), "hide": ("hid", "hidden"),
    "hit": (), "hold": ("held",), "hurt": (), "keep": ("kept",),
    "kneel": ("knelt",), "know": ("knew", "known"), "lay": ("laid",),
    "lead": ("led",), "lean": ("leant",), "leap": ("leapt",), "learn": ("learnt",),
    "leave": ("left",), "lend": ("lent",), "let": (), "lie": ("lay", "lain", "lying", "lied"),
    "light": ("lit",), "lose": ("lost",), "make": ("made",), "mean": ("meant",),
    "meet": ("met",), "mislead": ("misled",), "mistake": ("mistook", "mistaken"),
    "misunderstand": ("misunderstood",), "overcome": ("overcame",),
    "overtake": ("overtook", "overtaken"), "overthrow": ("overthrew", "overthrown"),
    "pay": ("paid",), "prove": ("proven",), "put": (), "quit": (), "read": (),
    "rid": (), "ride": ("rode", "ridden"), "ring": ("rang", "rung"),
    "rise": ("rose", "risen"), "run": ("ran",), "saw": ("sawn",), "say": ("said",),
    "see": ("saw", "seen"), "seek": ("sought",), "sell": ("sold",),
    "send": ("sent",), "set": (), "sew": ("sewn",), "shake": ("shook", "shaken"),
    "shed": (), "shine": ("shone",), "shoot": ("shot",), "show": ("shown",),
    "shrink": ("shrank", "shrunk"), "shut": (), "sing": ("sang", "sung"),
    "sink": ("sank", "sunk"), "sit": ("sat",), "sleep": ("slept",),
    "slide": ("slid",), "sling": ("slung",), "slit": (), "smell": ("smelt",),
    "sow": ("sown",), "speak": ("spoke", "spoken"), "speed": ("sped",),
    "spell": ("spelt",), "spend": ("spent",), "spill": ("spilt",),
    "spin": ("spun",), "spit": ("spat",), "split": (), "spoil": ("spoilt",),
    "spread": (), "spring": ("sprang", "sprung"), "stand": ("stood",),
    "steal": ("stole", "stolen"), "stick": ("stuck",), "sting": ("stung",),
    "stink": ("stank", "stunk"), "stride": ("strode",), "strike": ("struck",),
    "string": ("strung",), "strive": ("strove", "striven"),
    "swear": ("swore", "sworn"), "sweep": ("swept",), "swell": ("swollen",),
    "swim": ("swam", "swum"), "swing": ("swung",), "take": ("took", "taken"),
    "teach": ("taught",), "tear": ("tore", "torn"), "tell": ("told",),
    "think": ("thought",), "throw": ("threw", "thrown"), "thrust": (),
    "tread": ("trod", "trodden"), "undergo": ("underwent", "undergone"),
    "understand": ("understood",), "undertake": ("undertook", "undertaken"),
    "undo": ("undid", "undone"), "upset": (), "wake": ("woke", "woken"),
    "wear": ("wore", "worn"), "weave": ("wove", "woven"), "weep": ("wept",),
    "win": ("won",), "wind": ("wound",), "withdraw": ("withdrew", "withdrawn"),
    "withhold": ("withheld",), "withstand": ("withstood",), "wring": ("wrung",),
    "write": ("wrote", "written"), "can": ("could", "cannot", "can't"),
    "will": ("would", "won't"), "shall": ("should",), "may": ("might",),
    "must": (), "i": ("me", "my", "mine", "myself"),
    # plurals
    "child": ("children",), "man": ("men",), "woman": ("women",),
    "person": ("people",), "foot": ("feet",), "tooth": ("teeth",),
    "goose": ("geese",), "mouse": ("mice",), "ox": ("oxen",),
    "life": ("lives",), "knife": ("knives",), "wife": ("wives",),
    "leaf": ("leaves",), "half": ("halves",), "shelf": ("shelves",),
    "thief": ("thieves",), "wolf": ("wolves",), "loaf": ("loaves",),
    "calf": ("calves",), "self": ("selves",), "analysis": ("analyses",),
    "crisis": ("crises",), "thesis": ("theses",), "hypothesis": ("hypotheses",),
    "basis": ("bases",), "diagnosis": ("diagnoses",), "phenomenon": ("phenomena",),
    "criterion": ("criteria",), "medium": ("media",), "datum": ("data",),
    "bacterium": ("bacteria",), "curriculum": ("curricula",),
    "stimulus": ("stimuli",), "nucleus": ("nuclei",), "fungus": ("fungi",),
    "cactus": ("cacti",), "appendix": ("appendices",), "index": ("indices",),
    # comparisons
    "good": ("better", "best"), "well": ("better", "best"), "bad": ("worse", "worst"),
    "badly": ("worse", "worst"), "far": ("further", "farther", "furthest", "farthest"),
    "little": ("less", "least"), "many": ("more", "most"), "much": ("more", "most"),
    # pronouns and determiners that change shape
    "one's": ("my", "your", "his", "her", "its", "our", "their"),
    "oneself": ("myself", "yourself", "himself", "herself", "itself", "ourselves",
                "yourselves", "themselves"),
    "somebody": ("someone", "him", "her", "them", "me", "you", "us"),
    "someone": ("somebody", "him", "her", "them", "me", "you", "us"),
    "something": ("it", "this", "that", "them"),
    "sb": ("someone", "somebody", "him", "her", "them", "me", "you", "us"),
    "sth": ("something", "it", "this", "that", "them"),
    "my": ("your", "his", "her", "its", "our", "their"),
    "a": ("an",), "an": ("a",),
}

#: Words in an entry that stand for anything: "take sth off", "one's best".
_PLACEHOLDERS = {"sb", "sth", "somebody", "something", "someone", "one's", "oneself"}

_VOWELS = "aeiou"


def _forms(token: str) -> set[str]:
    """The spellings one word of an entry can take in a sentence."""
    base = token.casefold()
    forms = {base, *IRREGULAR.get(base, ())}
    if len(base) < 2 or not base.isalpha():
        return forms
    # -s, -es, -ies; -ed, -ied; -ing; -er, -est; -ly
    forms |= {base + "s", base + "es", base + "ed", base + "ing", base + "er", base + "est",
              base + "d", base + "r", base + "st", base + "'s", base + "ly", base + "ness"}
    if base.endswith("y") and len(base) > 2 and base[-2] not in _VOWELS:
        stem = base[:-1]
        forms |= {stem + "ies", stem + "ied", stem + "ier", stem + "iest", stem + "ily"}
    if base.endswith("e"):
        stem = base[:-1]
        forms |= {stem + "ing", stem + "ed", stem + "er", stem + "est", stem + "ion"}
    if base.endswith("ie"):
        forms.add(base[:-2] + "ying")
    # one consonant after one vowel, doubled: stop -> stopped, big -> bigger
    if (
        len(base) >= 3
        and base[-1] not in _VOWELS + "wxy"
        and base[-2] in _VOWELS
        and base[-3] not in _VOWELS
    ):
        double = base + base[-1]
        forms |= {double + "ed", double + "ing", double + "er", double + "est"}
    if base.endswith("c"):
        forms |= {base + "ked", base + "king"}
    if base.endswith("le"):
        forms.add(base[:-1] + "y")
    if base.endswith("ic"):
        forms.add(base + "ally")
    if base.endswith(("s", "x", "z", "ch", "sh")):
        forms.add(base + "es")
    return forms


def _entry_tokens(word: str) -> list[str]:
    """The words of an entry to look for: "(be) able to" -> ["able", "to"]."""
    text = re.sub(r"\(.*?\)", " ", word or "")
    text = text.replace("’", "'").replace("/", " ")
    tokens = [t for t in re.split(r"[\s\-]+", text.strip()) if t]
    # A leading "to" (a verb's infinitive marker) is not part of the word.
    if len(tokens) > 1 and tokens[0].casefold() == "to":
        tokens = tokens[1:]
    return tokens


def _pattern(token: str) -> str:
    if token.casefold() in _PLACEHOLDERS:
        # "sb" and "sth" stand for words of any kind: one to four of them.
        return r"[\w'’-]+(?:\s+[\w'’-]+){0,3}"
    forms = sorted(_forms(token), key=len, reverse=True)
    return "(?:" + "|".join(re.escape(form) for form in forms) + ")"


def find_word(text: str, word: str) -> tuple[int, int] | None:
    """Where ``word`` stands in ``text``, as ``(start, end)``, or None.

    Each word of the entry is matched in its common forms; the words of a
    phrase may have up to three words between them ("look it up"), and a
    one-word entry may also stand inside a hyphenated compound.
    """
    tokens = _entry_tokens(word)
    if not tokens or not text:
        return None
    gap = r"(?:[\s,'’-]+[\w'’-]+){0,3}?[\s'’-]+"
    body = gap.join(_pattern(token) for token in tokens)
    match = re.search(rf"(?<![\w]){body}(?![\w])", text, flags=re.IGNORECASE)
    if match:
        return match.start(), match.end()
    return None


def contains_word(text: str, word: str) -> bool:
    """Whether a context seems to contain its word, in any of its common forms."""
    return find_word(text, word) is not None


def word_length(word: str) -> int:
    """How many letters a word has: spaces, hyphens and apostrophes not counted."""
    return sum(1 for ch in word or "" if ch.isalpha())
