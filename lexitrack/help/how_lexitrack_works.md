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

**Start session** on Today does the day's reviews first, then its new words.

1. **Review.** Words come one at a time, each with one question and four
   options: the word for its definition, or the definition of the word picked
   out in a sentence. Choose with **1**–**4** or **A**–**D**. Right: say how
   it went with **1** Again, **2** Hard, **3** Good or **4** Easy. Wrong: the
   right word and its definition are shown, **Enter** moves on, and the word
   comes back a few cards later, asked the other way. **Ctrl+Z** takes back
   the last word.
2. **Learn the new words.** Four at a time: each shown whole, then asked;
   words with contexts are asked a second time a little later. None of it is
   rated — a word's first real question is tomorrow. **Mark as studied**
   skips the practice if you studied them another way.

On your phone the Telegram bot does the same, if you set it up in
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
| 14 | Good | 46 days | Day 60, in long-term memory |

A word is in **long-term memory** when that number passes 21 days — three
Goods, or two Easys, but never on the day you first study it. Answering Easy
every time gets there on day 9; Hard alone never does, because it barely moves
the number. That is a forecast, not yet a reason to call the word Known.
LexiTrack offers to mark it Known when you **answer it right after 21 days or
more without a review**. It never marks a word Known itself, because Known is
your judgement, and until you say yes the word keeps coming back.

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
*Long-term evidence after* or, in Developer mode, the target retention, and
they move with it.

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
  schedule, a button, Sort words or Undo.
- **Answers taken back** stay in the record, marked, and are left out of
  every count.

Select a word in any table, or click it on the Today page, to see its whole
history: when it was introduced, every answer, and why it is due when it is.

Everything is stored in one file on this computer. LexiTrack sends nothing
anywhere unless you connect a Telegram bot yourself.
