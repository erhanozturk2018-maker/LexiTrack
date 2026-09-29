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

## A word

A word is what you see on its page: the **word**, its **length**, its **CEFR
level**, its **part of speech**, one **definition** — covering every sense
the word is learned in — and its **contexts**, sentences that show the
definition in use. Add a context, delete one or change the definition on the
word's page (select it in any table). A word needs a definition to be asked;
without one it waits.

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
| **Export and backup** | Words as PDF, CSV or JSON; your answers as CSV; backups, one file with everything, and restoring them. |

## Your day

Today has two sessions, each with its own button: **Reviews** first, then
**New words**. A review session never shows a new word.

1. **Reviews.** Words come one at a time, each with one question and four
   options: the word for its definition, or the definition of the word picked
   out in a sentence. Choose with **1**–**4** or **A**–**D**. Right: say how
   it went with **1** Again, **2** Hard, **3** Good or **4** Easy. Wrong: the
   right word and its definition are shown, **Enter** moves on, and the word
   comes back a few cards later, asked the other way. **Ctrl+Z** takes back
   the last word.
2. **New words.** Four at a time: each shown whole, then asked; words with
   contexts are asked a second time a little later. None of it is rated — a
   word's first real question is tomorrow. A word counts as learned only
   once it has been shown and practised; stop halfway and the rest wait.

On your phone the Telegram bot does the same — `/review` and `/learn`, or
the two buttons on its morning message — if you set it up in
*Settings → Telegram*.

## How a word is learned

Every question has four options and one right answer: the word for its
definition (**Definition → Word**), or — for a word with contexts — its
definition for a sentence that uses it (**Context → Definition**). A word due
for review is asked once a day, one of the two, and that answer is the day's
answer for the word:

- **Right:** the word and its definition are shown again, and you say how it
  went — **Again**, **Hard**, **Good** or **Easy**. That is the rating.
- **Wrong:** the right word and its definition are shown, the answer is
  recorded as wrong and rated **Again**, and the word is asked again a few
  cards later, the other way round when it has contexts. That second question
  is practice: it never changes the schedule.

Whether an answer was right and how it went are kept apart: a right answer
you rate Again is recorded as right, rated Again.

The rating does not move a word along a fixed track; it changes one number,
how long LexiTrack expects you to remember the word. Everything else follows
from it. With the default settings:

| Day | You answer | Remembered for | Comes back |
| --- | --- | --- | --- |
| 0 | Studied the new words and confirmed | — | Tomorrow |
| 1 | Good | 2 days | Day 3 |
| 3 | Good | 11 days | Day 14 |
| 14 | Good | 46 days | Day 35: the Known check |

A word is in **long-term memory** when that number passes 21 days — three
Goods, or two Easys, but never on the day you first study it. Answering Easy
every time gets there on day 9; Hard alone never does, because it barely moves
the number. That is a forecast, not yet a reason to call the word Known.
LexiTrack offers to mark it Known when you **answer it right after 21 days or
more without a review**. It never marks a word Known itself, because Known is
your judgement, and until you say yes the word keeps coming back.

**The Known check.** Until a word has passed that test, it is never sent
further away than 21 days. On day 14 above the memory says 46 days, but the
word comes back on day 35: 21 days later, the gap the offer asks for. Answer
it right then and Known is offered; after that the word is spaced out as its
memory says. Without this limit the offer would wait for day 60, or longer.

**Pressing Again is not a reset to the beginning.** It costs you the interval
you had built up, and the word comes back tomorrow as a review — it does not
go back into the new-word list and never uses up one of your 25:

| Day | You answer | Remembered for |
| --- | --- | --- |
| 14 | Again | 11 days → **1.5 days** |
| 15 | Good | 3.6 days |
| 19 | Good | 9.7 days |
| 29 | Good | 22.9 days → **long-term memory** |

Note day 15: Good brings the word back to normal intervals immediately, but
not to where it was. One slip on day 14 moved long-term memory from day 14 to
day 29. Miss the same word again later and it recovers more slowly each time,
because LexiTrack has learned the word is hard for you — after the second slip
it also joins *Words you find hard* and comes first in every session.

These days come from the scheduler with the default settings. Change
*Offer Known after* or, in Developer mode, the target retention, and they
move with it.

## Words you know

**Marking a word Known** takes it out of your new words and your reviews,
whether you knew it before LexiTrack or learned it here. What happens next
depends on *Keep reviewing words learned here* (Settings → Learning):

1. **Off.** Known words are never asked again.
2. **On** (the default). A word you *learned here* keeps coming back now and
   then, so it stays known. A word you knew before and never learned here is
   never scheduled: there is no memory of it to keep.

Known words that keep coming back aim at a lower chance of remembering than
other words: **85%**, against 90%. That is *Memory target for Known words*,
from 75% to 95%. A lower target means longer gaps: a Known word comes back
about half as often as it would otherwise. When you mark a word Known, its
next review moves to the Known target at once (unless it is due today);
when you change the target, every Known word moves with it.

**A Known word you get wrong.** It is still Known; LexiTrack never takes that
back by itself. It asks instead: *You forgot this word. Learn it again?* Say
yes and it becomes Unknown again, recorded as forgotten, and goes back to
the normal target. Say nothing and it waits in *Known words you forgot* on
the Progress page, until you learn it again or answer it right.

**Many Known words at once.** Turning *Keep reviewing* back on, or raising
the target, can bring hundreds of Known words due on the same day. They are
spread out instead: at most *Known words back per day* (25 by default, 5 to
100), and only on days with room left after your reviews and new words, so
those never stop for them. The weakest come first, from tomorrow on.
LexiTrack says how long it will take, for example *800 Known words will come
back over the next 32 days*. Change the number and the words still waiting
are spread again.

## The four ratings

After a right answer you say how it went; that is the answer the schedule
hears.

| You choose | Use it when | What happens |
| --- | --- | --- |
| **Again** | You were not sure — a guess that happened to be right | Back tomorrow; the interval you had built up is lost; asked again later today. |
| **Hard** | It came, with effort | A short interval; the word stays in its current step. |
| **Good** | It came | The normal next interval. |
| **Easy** | It came at once, without thinking | The longest interval. |

Only **Good** and **Easy** move a word forward. Keep **Easy** for words that
come without thinking: it spaces a word out fastest, so an Easy you did not
mean costs the most. A wrong answer is always Again. A word you answer right
after a long gap is offered as Known, never made Known for you.

## What is recorded, and where to see it

Nothing is hidden from you.

- **Every answer** from Today and Telegram, with its time, the question it
  answered, whether it was right, its rating, how long you were expected to
  remember the word afterwards and where you answered. See it in
  *Progress → Answers*, or save it as a spreadsheet from *Export and backup*.
- **Every question**, practice included, with whether it was right.
- **Every change of status** - Known, Unknown, reset - with its cause: the
  schedule, a button, Sort words, Undo, or a Known word you forgot and chose
  to learn again.
- **Answers taken back** stay in the record, marked, and are left out of
  every count.

Select a word in any table, or click it on the Today page, to see its whole
history: when it was introduced, every answer, and why it is due when it is.

Everything is stored in one file on this computer. LexiTrack sends nothing
anywhere unless you connect a Telegram bot yourself.
