# Content enrichment files

Every word in LexiTrack has a short definition in its own language. **Teaching
content** is extra material that helps the engine teach a word for use, not
only for recognition. It comes in two kinds:

- **Target-language content**, the same for every learner: the grammatical
  pattern, collocations, register, related words, and example contexts. "be
  reluctant to do sth" is true of English whoever learns it.
- **Learner-language content**, one block per language the learner may speak:
  the core meaning in that language, the nuance explained, a usage note, a
  mnemonic, notes, and the translation of each context.

A word is one record with one card and one review history, however many
learner languages explain it. English → German and English → Spanish
learners of *commute* share everything except the explanations. No language
is special: a learner language is a code such as `de`, `es`, `fr` or `tr`, and
a file can carry any number of them.

Content is optional. A word without it is still taught and reviewed, by the
SHORT route. Content is added in batches, from outside the application:

```
export a batch of words that need content      content_batch_001.json
      → fill it with any tool you like, e.g. an LLM, outside LexiTrack
      → import it: LexiTrack validates it and shows a preview
      → confirm: empty fields are filled; nothing is replaced unless you choose
```

LexiTrack never calls an LLM itself and works fully offline. Which learner
language a review uses is chosen in *Settings → Learning → Explain words in*.

**In the app:** *Export and backup → Word Content…* (also in the ⋯ menu and
`Ctrl+K`). It shows how many of your words have content for a language,
exports the next batch — from your study plan, today's new words, a list, or
the words selected in a table (*More → Export for Content…*) — and imports a
filled file after showing what it would change, with each conflicting field
unticked until you tick it.

**Resumable:** a batch exported and not yet imported is listed as *waiting*,
and its words are left out of the next batch; importing it closes it. A batch
that will never come back can be forgotten, and its words are offered again.
To redo one word, select it in a table, export it, edit it, import it, and
tick the fields to replace.

---

## The file (schema 2)

```json
{
  "format": "lexitrack-content",
  "schema_version": 2,
  "app_version": "0.5.0",
  "batch": "batch_001",
  "created_at": "2026-09-25T09:00:00+00:00",
  "learner_languages": ["de", "es"],
  "instructions": "…what to fill and how…",
  "encoding_types": ["IMAGE", "SCENE", "ACTION", "CONTRAST", "RELATION", "SOUND", "NONE"],
  "words": [
    {
      "word_id": 4211,
      "word": "commute",
      "target_language": "en",
      "cefr": "B1",
      "part_of_speech": "verb",
      "source_definition": "to travel regularly between your place of work and your home",
      "needs": {
        "target": ["pattern", "collocations", "contexts"],
        "de": ["core_meaning", "nuance", "usage_note", "encoding_type", "encoding_cue", "notes"],
        "es": ["core_meaning", "nuance", "usage_note", "encoding_type", "encoding_cue", "notes"]
      },
      "target": {
        "pattern": "commute (from A) to B",
        "collocations": ["commute to work", "a long commute"],
        "register": "neutral",
        "related": [{ "word": "travel", "relation": "synonym" }],
        "depth_hint": "light"
      },
      "contexts": [
        {
          "kind": "sentence",
          "text": "She {{commutes}} to London every day.",
          "translations": {
            "de": "Sie pendelt jeden Tag nach London.",
            "es": "Va y vuelve a Londres todos los días."
          }
        },
        {
          "kind": "situation",
          "text": "Two hours on the train, twice a day, five days a week: a long {{commute}}.",
          "translations": {}
        }
      ],
      "localizations": {
        "de": {
          "core_meaning": "pendeln",
          "nuance": "…",
          "usage_note": "…",
          "encoding_type": "ACTION",
          "encoding_cue": "…",
          "notes": null
        },
        "es": {
          "core_meaning": "ir y volver del trabajo",
          "nuance": null,
          "usage_note": null,
          "encoding_type": null,
          "encoding_cue": null,
          "notes": null
        }
      }
    }
  ]
}
```

The exported file already contains `instructions`: a prompt that explains
every field, naming the target language and the learner languages asked for,
so the file can be handed to an LLM as it is. `needs` lists what the word is
still missing, per part. It is for information and is ignored on import.

### Target-language fields (`target`, `contexts`)

| Field | Meaning |
| --- | --- |
| `pattern` | Grammatical pattern(s), e.g. `be reluctant to do sth`. |
| `collocations` | Common combinations, a list. |
| `register` | `formal`, `informal`, `neutral`, `technical`… |
| `related` | Up to 3 `{word, relation}` of the same language; relation is e.g. `synonym`, `antonym`, `contrast`, `family`, `confusable`. |
| `depth_hint` | `light` or `deep`: how much teaching the word probably needs. |
| `contexts[].kind` | `sentence` (a sentence using the word) or `situation` (a moment the word fits). |
| `contexts[].text` | In the target language. Must mark the word as `{{word}}`, inflected as it appears (`{{commutes}}`). Reviews hide the marked word to ask for it. |
| `contexts[].translations` | The text in each learner language, keyed by its code. Optional. |

### Learner-language fields (`localizations.<code>`)

| Field | Meaning |
| --- | --- |
| `core_meaning` | The core idea in that language, one line. |
| `nuance` | When to use this word rather than a near synonym, and its tone, explained in that language. |
| `usage_note` | How the word is used, explained for a speaker of that language. |
| `encoding_type` | One of `IMAGE`, `SCENE`, `ACTION`, `CONTRAST`, `RELATION`, `SOUND`, `NONE`. Concrete words suit IMAGE, SCENE or ACTION; abstract words suit CONTRAST or RELATION. |
| `encoding_cue` | One sentence in that language that gives the word an extra route into memory. |
| `notes` | Anything else for a speaker of that language: a false friend, a typical confusion. |

Any field may be `null` or an empty list. A field left empty is simply not
filled. Language codes are ISO 639 codes such as `de`, `es`, `fr`, `tr`;
language names (`German`) and regional tags (`es-MX`) are read as their
code.

## What importing does

1. **Every entry is matched by `word_id` and by spelling.** If id 4211 is not
   “commute” in your vocabulary, the entry is rejected. So is an entry whose
   `target_language` is not the word's language. A file can never write one
   word's content onto another. Unknown ids and entries that appear twice
   are rejected too. The other entries are still imported.
2. **Invalid values become warnings, not data.** An encoding type outside the
   list, a depth other than light/deep, a context without a `{{…}}` marker,
   or a block under something that is not a language code is skipped and
   reported. Over-long text is cut at 600 characters.
3. **The preview shows what would change** before anything is written: fields
   to fill and fields that already have a *different* value (conflicts), for
   the target part and each learner language; new contexts; contexts already
   present (compared ignoring case, spacing and the marker), and new
   translations of them.
4. **Nothing is overwritten silently.** Empty fields are filled. A
   conflicting field keeps its current value unless you choose to replace it
   (`target.pattern`, `de.core_meaning`, …), one at a time or all at once.
5. **The whole import is one transaction.** It either happens completely or
   not at all. The batch name is recorded as the content's `source`, and each
   localization counts its versions.

Import the same file twice and nothing changes the second time. Adding a
learner language later is a batch with only that language's blocks: nothing
already stored changes. To regenerate one word, export a batch with just
that word, edit it, import it, and choose to replace the fields you want.

A content file is not a word list. Opening one with *Import words* is
refused with a message that says so.

**Quality warnings.** Beyond what can be read, the preview warns — without
rejecting anything — about what an entry says: a collocation that does not
contain the word, fewer than 2 or more than 5 collocations, more than 3
contexts, and, for each learner language the file asks for
(`learner_languages`), a missing meaning or contexts left untranslated.

## The whole vocabulary

- **All your words** is a scope of *Word Content*: batches are taken from
  every word, so the full vocabulary (thousands of words) is enriched batch
  by batch — at most 500 words each — resuming where the last one stopped,
  since a word with content or out in a batch is not offered again.
- **Send again** writes a waiting batch's file anew — the same words, name
  and languages — when the first file was lost or came back unusable. The
  batch stays open until its filled file is imported, or it is forgotten.
- **Export all** writes every enriched word's content, in every learner
  language stored, to one file: this format with `"kind": "dataset"` and a
  `dataset_version` (the time it was taken, `YYYYMMDD-HHMMSS`). It is not a
  batch — nothing waits for it — and it imports back through the same
  preview, so a dataset can be kept, compared, edited and restored. Each
  localization still counts its own `content_version`.

## Schema 1 files

Schema 1 had a single learner language, written for Turkish-speaking
learners, inside the shared block (`core_meaning_tr`, `translation_tr`, and
`nuance` and the mnemonic next to the pattern). Such a file is still read:
those fields become the `tr` localization and the rest the target part.

## Content status

For a language pair, each word's content is **none**, **partial** or
**complete**. Complete means a pattern or collocations, at least two
contexts — the second is what lets a review test whether the word transfers
to a sentence it has not seen — and, when you have chosen a language to be
taught in, a core meaning in that language. Anything less, but not nothing,
is partial.
