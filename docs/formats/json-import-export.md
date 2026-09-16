# JSON word lists

LexiTrack reads and writes a small JSON format for word lists. It is designed
to be written by hand, edited in any text editor, and imported again — so it
asks for as little as possible and never contains anything a person would not
write themselves.

Examples you can import straight away live in [`examples/`](../../examples).

---

## The smallest valid file

```json
{ "words": ["Haus", "gehen", "kommen"] }
```

A bare array is accepted too, as shorthand for the same thing:

```json
["Haus", "gehen", "kommen"]
```

## Everything the format supports

```json
{
  "name": "German A1",
  "language": "de",
  "description": "Everyday German vocabulary at A1 level.",
  "source": "Goethe-Institut A1 word list",
  "words": [
    "Haus",
    {
      "word": "gehen",
      "part_of_speech": "verb",
      "cefr_level": "A1",
      "definition": "to go",
      "example": "Wir gehen nach Hause."
    },
    { "word": "gift", "language": "en" }
  ]
}
```

Strings and objects can be mixed freely in `words`.

### List fields

All optional except `words`.

| Field | Type | Meaning |
| --- | --- | --- |
| `words` | list | **Required.** The vocabulary, as strings or word objects. |
| `name` | text | Suggested name for the list. Defaults to the file name (`german_a1.json` → "German A1"). |
| `language` | text | Language of the words: a code (`en`, `de`, `en-GB`) or an English name (`German`). |
| `description` | text | Shown on the list card. |
| `source` | text | Where the words came from. Recorded as provenance; see below. |

### Word fields

All optional except `word`.

| Field | Also accepted as | Type |
| --- | --- | --- |
| `word` | — | text, **required** |
| `part_of_speech` | `pos` | text |
| `cefr_level` | `cefr`, `level` | text (`A1`…`C2` are normalised to upper case) |
| `definition` | `meaning`, `translation` | text |
| `example` | — | text |
| `language` | — | text; overrides the list's `language` for this word |

Unknown fields are ignored, so a file produced by another tool with extra
fields still imports.

---

## How words are identified

A word's identity is its **language** plus its spelling with case and
punctuation variants removed. Within one language `Haus`, `haus` and `HAUS`
are the same word. Across languages, English `gift` and German `Gift` are
different words and never merge.

Importing a word that already exists in that language does not create a copy
and never changes whether you know it. It only adds the word to the lists you
chose, and fills in details (part of speech, definition…) the word did not
have yet.

## Language

The language words are stored in is decided in this order:

1. A `language` on the word itself.
2. The file's `language`.
3. The language you choose in the import preview.
4. The language of the list you import into.
5. Otherwise *unspecified*.

A file that states a language cannot be imported into a list of a different
language — the preview explains the clash and disables Import. A list whose
language is *Unspecified* accepts words in any language.

## Source

`source` is **provenance**: where the words came from, shown on flashcards as
"Source: …". It is not the list. A file without `source` is recorded as coming
from the file itself (for example `german_a1.json`). LexiTrack never invents a
source: a file called `oxford_3000.json` is not labelled "Oxford 3000" unless
it says so.

---

## Validation

Two kinds of problem are handled differently.

**The file has the wrong shape** — the import stops, nothing is written, and
the message says where. Up to three problems are listed at once.

| Problem | Message |
| --- | --- |
| Not JSON | `words.json is not valid JSON: Expecting value (line 3, column 12).` |
| Empty file | `words.json is empty.` |
| No `words` | `words.json has no "words" list, so there is nothing to import.` |
| `words` not a list | `In words.json, "words" must be a list, not text.` |
| Empty `words` | `words.json contains no words.` |
| Item neither text nor object | `Word 4 must be text or an object with a "word" field, not a number.` |
| Object without `word` | `Word 2 has no "word" field.` |
| Field of the wrong type | `Word 7 ("Haus"): "definition" must be text, not a list.` |
| Metadata of the wrong type | `In words.json, "name" must be text, not a number.` |
| Unrecognised language | `In words.json, "Klingon!" is not a language LexiTrack recognises.` |

**An item is well-formed but is not a usable word** — for example `""` or
`"42"`. It is skipped, the rest of the file imports, and the preview shows a
warning naming what was skipped. A file where *every* item is unusable is
rejected.

Duplicates inside one file are merged, and the preview says how many.

---

## Export

**File → Export…** writes this same format when you choose *JSON word list*.
You can export a whole list, its unknown words, all unknown words, or a
selection from a table.

Exports follow three rules:

- **Only what a person would write.** No database ids, no timestamps and no
  learning status. What you know belongs to your copy of LexiTrack, not to the
  word list — and importing never changes it anyway.
- **Nothing empty.** Missing fields are left out rather than written as
  `null`; a word with no details is written as a plain string.
- **Round-trips.** Importing an exported file reproduces the same words, in
  the same order, with the same details. A test enforces this.

In a list with an unspecified language that mixes languages, each word keeps
its own `language` field, so the round trip does not lose it.

Files are UTF-8 and written unescaped, so `Straße` appears as `Straße`.
