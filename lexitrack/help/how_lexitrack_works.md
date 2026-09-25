# How LexiTrack works

LexiTrack does two different things, and most confusion comes from mixing
them up:

1. **Sorting.** On **Sort words** you go through a list and say what you
   already know: *I Know* or *I Don't Know*. Nothing is scheduled there. It
   only decides which words are **Unknown**.
2. **Learning.** Your **study plan** takes the Unknown words from the lists
   you choose and teaches them on **Today**: a few new words a day,
   then each one again on the day you are most likely to forget it.

Words you already know are never offered and never scheduled.

## The pages

The sidebar on the left reaches every page. On a narrow window it folds to
its icons; hover over one for its name.

| Page | What it is for |
| --- | --- |
| **Today** | The new words to learn, then the words due for review. The number beside it is what is waiting. |
| **Progress** | Everything learned here, every answer you have given, and whether the schedule fits you. |
| **Lists** | Your lists and how far each one is sorted. |
| **Sort words** | Sorting a list into Known and Unknown, as flashcards or as a table. |
| **Unknown** | Every word you marked Unknown, across all lists. |
| **Export and backup** | Words as PDF, CSV or JSON; your answers and attempts as CSV; backups, one file with everything, and restoring them. |

## Your day

**Start session** on Today does the day's reviews first, then its new words.

1. **Review.** Words come one at a time, and you **type** them from their
   meaning, or later from a sentence with a gap. **Enter** checks; **Ctrl+H**
   shows the first letter; **Enter** with nothing typed means you don't know.
   Missed it? The answer stays hidden and an easier question follows, down to
   picking the word among four (**1**–**4**). A word you forgot is shown again
   and asked once more later, which never changes its schedule. A word with no
   meaning stored is reviewed as before: **Space**, then **1**–**4**.
   **Ctrl+Z** takes back the last word, with every question about it.
2. **Learn the new words.** Four at a time: shown, then asked from their
   meaning; some are asked again later. None of it is scored — a word's first
   real question is tomorrow. **Mark as studied** skips the practice if you
   studied them another way.

On your phone the Telegram bot does the same, if you set it up in
*Settings → Telegram*.

## How a word is learned

The four answers do not move a word along a fixed track; they change one
number, how long LexiTrack expects you to remember the word. Everything else
follows from it. You say how each retrieval went — **Instant**, **Remembered**,
**Effortful** or **Forgot** — after typing the word right, after writing a
sentence, or for a word shown without a meaning, and that becomes the answer:
**Easy**, **Good**, **Hard** or **Again**. A wrong answer is followed by an
easier question; picking the word out among four only after missing it is
**Hard**, and missing that too is **Again**. With the default settings:

| Day | You answer | Remembered for | Comes back |
| --- | --- | --- | --- |
| 0 | Studied the new words and confirmed | — | Tomorrow |
| 1 | Good | 2 days | Day 3 |
| 3 | Good | 11 days | Day 14 |
| 14 | Good | 46 days | Day 60, in long-term memory |

A word is in **long-term memory** when that number passes 21 days — three
Goods, or two Easys, but never on the day you first study it. Answering Easy
every time gets there on day 9; Hard alone never does, because it barely moves
the number. That is a forecast, not yet a reason to call the word Known.
LexiTrack offers to mark it Known when the record makes the case: you have
**used it well in two different ways** — a collocation, a sentence of your own
— and **recalled it after 21 days or more without a review**. It never marks a
word Known itself, because Known is your judgement, and until you say yes the
word keeps coming back.

**Pressing Again is not a reset to the beginning.** It costs you the interval
you had built up, and the word comes back tomorrow as a review — it does not
go back into the new-word list and never uses up one of your 25:

| Day | You answer | Remembered for |
| --- | --- | --- |
| 14 | Again | 11 days → **1.5 days** |
| 15 | Good | 3.6 days |
| 19 | Good | 9.7 days |
| 29 | Good | 22.9 days → **Known** |

Note day 15: Good brings the word back to normal intervals immediately, but
not to where it was. One slip on day 14 moved Known from day 14 to day 29.
Miss the same word again later and it recovers more slowly each time, because
LexiTrack has learned the word is hard for you — after the second slip it also
joins *Words you find hard* and comes first in every session.

These days come from the scheduler with the default settings. Change
*Count as known after* or, in Developer mode, the target retention, and they
move with it.

## The four answers

You say how the retrieval went; LexiTrack records the answer it means.

| You say | Use it when | Answer | What happens |
| --- | --- | --- | --- |
| **Forgot** | It did not come | **Again** | Back tomorrow; the interval you had built up is lost. |
| **Effortful** | It came, with effort | **Hard** | A short interval; the word stays in its current step. |
| **Remembered** | It came | **Good** | The normal next interval. |
| **Instant** | It came at once, without thinking | **Easy** | The longest interval. |

Only **Remembered** and **Instant** move a word forward. Keep **Instant** for
words that come without thinking: it spaces a word out fastest, so an Instant
you did not mean costs the most. Time, a hint or a slip never change what you
said; they are kept beside it. A word you have used well and recalled after a
long gap is offered as Known, never made Known for you.

## What is recorded, and where to see it

Nothing is hidden from you.

- **Every answer** from Today and Telegram, with its time, the answer, how
  long you were expected to remember the word afterwards and where you
  answered. See it in *Progress → Answers*, or save it as a spreadsheet
  from *Settings → Data → Export history*.
- **Every change of status** - Known, Unknown, reset - with its cause: the
  schedule, a button, Sort words or Undo.
- **Answers taken back** stay in the record, marked, and are left out of
  every count.

Select a word in any table, or click it on the Today page, to see its whole
history: when it was introduced, every answer, and why it is due when it is.

Everything is stored in one file on this computer. LexiTrack sends nothing
anywhere unless you connect a Telegram bot yourself.
