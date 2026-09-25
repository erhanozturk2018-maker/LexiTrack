# Content enrichment files

Every word in LexiTrack has a short English definition. **Teaching content**
is extra material that helps the engine teach a word for use, not only for
recognition: a Turkish core meaning, nuance, a grammatical pattern,
collocations, an encoding cue, related words, and example contexts.

Content is optional. A word without it is still taught and reviewed, by the
SHORT route. Content is added in batches, from outside the application:

```
export a batch of words that need content      content_batch_001.json
      → fill it with any tool you like, e.g. an LLM, outside LexiTrack
      → import it: LexiTrack validates it and shows a preview
      → confirm: empty fields are filled; nothing is replaced unless you choose
```

LexiTrack never calls an LLM itself and works fully offline.

---

## The file

```json
{
  "format": "lexitrack-content",
  "schema_version": 1,
  "app_version": "0.5.0",
  "batch": "batch_001",
  "created_at": "2026-09-25T09:00:00+00:00",
  "instructions": "…what to fill and how…",
  "encoding_types": ["IMAGE", "SCENE", "ACTION", "CONTRAST", "RELATION", "SOUND", "NONE"],
  "words": [
    {
      "word_id": 4211,
      "word": "reluctant",
      "language": "en",
      "cefr": "B2",
      "part_of_speech": "adjective",
      "definition": "hesitating before doing something because you do not want to do it",
      "needs": ["core_meaning_tr", "pattern", "collocations", "contexts"],
      "content": {
        "core_meaning_tr": "<the core meaning, in the learner's language>",
        "nuance": "Stronger than unwilling to admit, weaker than refusing.",
        "pattern": "be reluctant to do sth",
        "collocations": ["reluctant to admit", "a reluctant hero"],
        "register": "neutral",
        "encoding_type": "CONTRAST",
        "encoding_cue": "Not 'no' — a 'yes' that drags its feet.",
        "related": [{ "word": "unwilling", "relation": "synonym" }],
        "depth_hint": "deep"
      },
      "contexts": [
        {
          "kind": "sentence",
          "text": "She was {{reluctant}} to leave the party so early.",
          "translation_tr": "<the sentence, in the learner's language>"
        },
        {
          "kind": "situation",
          "text": "Your friend agrees to help you move, but slowly, sighing: a {{reluctant}} yes."
        }
      ]
    }
  ]
}
```

The exported file already contains `instructions`: a prompt that explains
every field, so the file can be handed to an LLM as it is. `needs` lists
what the word is still missing. It is for information and is ignored on
import.

### Fields

| Field | Meaning |
|---|---|
| `core_meaning_tr` | The core idea in Turkish, one line. |
| `nuance` | When to use this word rather than a near synonym, and its tone. |
| `pattern` | Grammatical pattern(s), e.g. `be reluctant to do sth`. |
| `collocations` | Common combinations, a list. |
| `register` | `formal`, `informal`, `neutral`, `technical`… |
| `encoding_type` | One of `IMAGE`, `SCENE`, `ACTION`, `CONTRAST`, `RELATION`, `SOUND`, `NONE`. Concrete words suit IMAGE, SCENE or ACTION; abstract words suit CONTRAST or RELATION. |
| `encoding_cue` | One sentence that gives the word an extra route into memory. |
| `related` | Up to 3 `{word, relation}`; relation is e.g. `synonym`, `antonym`, `contrast`, `family`, `confusable`. |
| `depth_hint` | `light` or `deep`: how much teaching the word probably needs. |
| `contexts[].kind` | `sentence` (a sentence using the word) or `situation` (a moment the word fits). |
| `contexts[].text` | Must mark the word as `{{word}}`, inflected as it appears (`{{reluctantly}}`). Reviews hide the marked word to ask for it. |
| `contexts[].translation_tr` | Optional Turkish translation. |

Any field may be `null` or an empty list. A field left empty is simply not
filled.

## What importing does

1. **Every entry is matched by `word_id` and by spelling.** If id 4211 is not
   “reluctant” in your vocabulary, the entry is rejected. A file can never
   write one word's content onto another. Unknown ids and entries that appear
   twice are rejected too. The other entries are still imported.
2. **Invalid values become warnings, not data.** An encoding type outside the
   list, a depth other than light/deep, or a context without a `{{…}}` marker
   is skipped and reported. Over-long text is cut at 600 characters.
3. **The preview shows what would change** before anything is written: fields
   to fill, fields that already have a *different* value (conflicts), new
   contexts, and contexts already present (compared ignoring case, spacing and
   the marker).
4. **Nothing is overwritten silently.** Empty fields are filled. A conflicting
   field keeps its current value unless you choose to replace it, one field at
   a time or all at once.
5. **The whole import is one transaction.** It either happens completely or
   not at all. The batch name is recorded as the content's `source`.

Import the same file twice and nothing changes the second time. To regenerate
one word, export a batch with just that word, edit it, import it, and choose
to replace the fields you want.

A content file is not a word list. Opening one with *Import words* is
refused with a message that says so.

## Content status

Each word's content is **none**, **partial** or **complete**. Complete means
a Turkish core meaning, a pattern or collocations, and at least two contexts:
the second context is what lets a review test whether the word transfers to
a sentence it has not seen. Anything less, but not nothing, is partial.
