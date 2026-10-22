# Decisions

The team's record of settled questions. Skein reads this on every call and appends to it.

Format: one `##` heading per entry, `id · date · short title`, then these lines:

- Question: what was asked, in plain words.
- Answer: one or two sentences someone can act on.
- Decided by: a handle like @maya, `skein` when settled from an earlier entry, or `draft`.
- Why: the reason, short, and for a draft the sources with the date each last changed.
- Cites: ids this rests on, comma separated, or `none`. A draft cites what it was drawn from.
- Confirmed: yes or no. A draft with no never settles anything. To confirm one, change the
  field to yes; or leave it, and the first time an agent asks about it the question goes to
  the owner with the draft attached, and their answer becomes the record.

Two kinds of entry, two rules. A draft (`Confirmed: no`) is a guess: edit it, or delete it,
freely. A confirmed entry is the record: never edited or deleted, by people or by Skein. To
change one, append a newer entry that cites it; the newer entry overrides it from then on.

Entries Skein appends also carry `Context:` and `Route:`: settled (from the record), pending
(waiting on a person, with `Routed to:`) or routed (a person's answer, with `Waited:`, citing
the pending entry it closes). Ids are D- plus four digits, in order. Write new entries at
the bottom.

Empty record. The team's draft is in the pull request against this branch; what the team confirms
there becomes the first entries. The Skein server appends below this line and commits here.
