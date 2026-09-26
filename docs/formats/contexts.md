# Word contexts: the JSON file

A word in LexiTrack is its **word**, **length**, **CEFR level**, **part of
speech**, one **definition** and one or more **contexts**. A context is a
plain sentence in the language being learned that uses the word the way its
definition says. A word with one common use needs one context; a word whose
definition covers several senses needs one for each, so that together they
show the whole definition.

Contexts are added on a word's page, or imported from a JSON file:
**Export and backup → Word Contexts…**. LexiTrack never writes a context or a
definition itself — nothing is generated at review time — so a file can be
written by hand or with any tool, an LLM included.

## The format

A list of words, each with its contexts:

```json
[
  {
    "word": "sleep in",
    "contexts": [
      "I don't have to work tomorrow, so I can sleep in.",
      "I usually sleep in on Sundays."
    ]
  },
  {
    "word": "deliberate",
    "definition": "done on purpose; to think carefully about something before deciding",
    "contexts": [
      "It was a deliberate attempt to mislead the public.",
      "The jury deliberated for two days before reaching a verdict."
    ]
  }
]
```

| Field | Type | Meaning |
| --- | --- | --- |
| `word` | text, **required** | The word as LexiTrack has it. Found by its spelling, case and spacing aside. |
| `contexts` | list of text (or one text) | Sentences to add to the word. |
| `definition` | text, optional | Replaces the word's definition when it is different. |
| `language` | text, optional | The word's language (`en`, `de`), when the same spelling exists in two. |
| `length`, `cefr_level`, `part_of_speech` | — | Written by the export, ignored on import. |

`{"words": [...]}` — the shape of a JSON word list — is read too, and so is a
content file from an earlier LexiTrack (its contexts' `text`, with any
`{{word}}` marker removed).

## What an import does

The file is read first, and the window shows what it would do; nothing is
written until **Import** is pressed, and then all of it is written together.

- Each entry is **found among the words already in LexiTrack**. A word that
  is not there is listed as *not in your vocabulary* and left out: **an
  import never creates a word.** Add it first — by hand or in a word list —
  then import its contexts.
- A sentence the word **already has**, or that the entry repeats, is not
  added again. Importing the same file twice adds nothing the second time.
- A context that **does not seem to contain its word** is imported and
  flagged, so it can be checked: the word is looked for in its common forms
  (*acquire* in "she acquired", *sleep in* in "we slept in", *look up* in
  "look it up").
- An empty context, or one longer than 400 characters, is left out and
  flagged.
- A `definition` different from the word's replaces it.

## The export

The same window exports words — all of them, your study plan, one list or a
selection, optionally only the words without contexts — in this format, with
each word's `length`, `cefr_level`, `part_of_speech` and `definition` beside
its `contexts` (an empty list when it has none). Fill in the contexts and
import the file back.

## Writing good contexts

- **One per sense.** Read the definition: each part of it (often separated by
  a semicolon) should be shown by at least one context. No more than that is
  needed.
- **Natural and clear.** A sentence someone would say or write, short enough
  to read at a glance, where the word's meaning is plain from the rest.
- **The word itself.** Use the word, in any form (*acquired*, *slept in*);
  not a synonym.
- **English only.** No translation, note or explanation in the sentence.
