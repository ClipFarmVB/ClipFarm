# Mobile app — decision log

This file records the architectural choices made while building the mobile app
epic (CF-313, #363), so the shape of the thing can be understood without
reverse-engineering it from diffs. **Open** entries are questions waiting on
Owen — work that depends on them is stopped, not guessed at. **Decided**
entries were settled by whoever was implementing, and are here because a future
reader would otherwise have to work out *why* from the code alone. Newest
first in both sections.

Entries are appended, never rewritten. Correcting an earlier one means a new
entry that links back to it, so the reasoning stays legible in order.

The epic body has its own **"Decisions already made (do not relitigate)"**
section — Expo over Capacitor, one repo, both platforms, the `ClipSource`
interface, transactional-vs-engagement push, private by default. Those are
settled and are not repeated here; this file only covers what came up while
building.

**Resolving a conflict here.** This file is meant to live on `main` so that
branches writing entries share a common ancestor for the path. A branch that
added its own copy before it landed there sees an add/add conflict the first
time it merges `main`, and git offers a choice between two whole files.
**If you hit a conflict in this file, keep both sides** — never resolve it by
keeping one. Every entry the other branch carried is a decision that nothing
else in the repository records. See CF-386 (#503).

---

## Open — needs Owen's call

### CF-386 · Whether the log stays one file or becomes one file per entry — 2026-09-12

**The choice.** This file is a single document that entries are added to at the
top, which is the shape the epic's ticket procedure prescribes. CF-386 (#503)
proposes replacing it with `docs/mobile/decisions/CF-NNN-<slug>.md` — one file
per entry, with this file reduced to an index.

**Context.** Landing this file on `main` (the first *Decided* entry below) fixes
the expensive half of the problem: with no common ancestor for the path, git
reported an add/add conflict, and resolving that by taking one side silently
dropped every entry the other branch carried.

What it does not fix is the conflict itself. Once the branches have adopted the
ancestor, two of them adding entries both insert at the top of `## Decided` — the
same region of the same file — so git still stops and asks. It shows both sides
rather than offering a choice between two whole files, which is a large
improvement in the failure mode and no improvement at all in the frequency.

**Options.**

1. **Leave it as one file.** Conflicts stay routine but visible, and the
   resolution is mechanical: keep both entries, order by date. The reading order
   the format is built around — newest first, one scroll, one document you can
   hand someone — survives. The cost is a hand resolution on most mobile PRs, and
   the standing risk that somebody resolves one carelessly.

2. **One file per entry, this file as an index.** No two tickets ever write the
   same entry path, which is exactly the trick CF-315 (#365) uses to stop five
   screen tickets fighting over one route table. The conflict does not vanish,
   though — it moves to the index, where every ticket still appends a line. A
   one-line conflict is better than an entry-sized one, but "no more conflicts"
   would overstate it. The cost is the reading order: every entry becomes its
   own file, plus an index that can drift from them, and reviewing a decision
   alongside its PR means opening a second file.

3. **One file per entry, no index at all.** The directory listing is the index —
   filenames carry the CF number and slug, and Open-vs-Decided becomes a status
   line inside each file. The only option with genuinely no shared file and so
   genuinely no conflicts. It costs the most convenience: there is no single
   document any more, and "what is still open?" becomes a grep rather than a
   section.

**Recommended.** Option 1 for now — a recommendation to *not* spend the change.
The silent-loss risk was the part worth fixing and the entry below fixes it. What
remains is an ordinary merge conflict in a documentation file, which at the
epic's current size is cheaper to pay than to engineer around. Option 2 is the
one to reach for if mobile PR volume rises enough that the resolutions become real toil, and
it stays available: moving markdown is a `git mv` and a link fix, not a
migration.

The reason this is Owen's call rather than mine is narrower than the trade-off.
Options 2 and 3 change the file format the ticket procedure specifies, and
quietly reshaping the thing that procedure tells the next run to write is how a
convention stops being followed.

**Consequences.** Nothing blocks on this; every mobile ticket keeps writing this
file as it stands. If option 2 or 3 is chosen later the entries move wholesale,
and nothing that cites them by content breaks — only the links in this file and
any PR body that deep-linked a heading anchor.

---

## Decided

### CF-386 · The log lands on `main` as a skeleton, and each branch brings its own entries — 2026-11-23

**The choice.** This file is added to `main` with its header, its two section
headings and the two CF-386 entries, and no other entries. Every other entry
reaches `main` with the PR of the branch that wrote it.

**Context.** The log was introduced by the first mobile ticket that needed one
and then, independently, by the next three: `mobile/CF-318`, `mobile/CF-319`,
`mobile/CF-321` and `mobile/CF-322` each *added* `docs/mobile/DECISIONS.md` in
their own first commit. Git had no common ancestor for the path, so it treated
every pair as an add/add conflict — and the natural resolution, keeping one
side, discards every entry the other side wrote. **Merge order silently decided
which decisions survived**, in a file whose entire purpose is that the reasoning
is not lost. CF-386 (#503) is the card.

**Options.** Land the file on `main` once so every branch shares an ancestor —
either carrying a snapshot of every branch's entries, or carrying none of them;
restructure it so no two tickets write the same path (the *Open* entry above,
still Owen's call); or keep resolving by hand.

**Chosen.** Land it, and carry none of the branches' entries.

The first version of this change carried the union of every branch's entries,
on the grounds that they were byte-identical wherever they overlapped, so that a
branch could resolve by taking `main`'s copy wholesale. That held for about six
weeks. Two branches then corrected entries in place, and from that point taking
`main`'s copy would have dropped their corrections — one of them a reversal of
the reasoning behind a push-notification privacy decision. A snapshot of entries
that are still being edited on their branches cannot stay a superset of them,
and re-taking the snapshot only restarts the same clock.

The ancestor only has to exist at the path, and its header is the one region
that is the same on every branch copy. So `main` carries that, and the entries
stay with the branches that own them.

**Consequences.** Landing the file does **not** make the in-flight merges
three-way. A branch that added its own copy before this landed still has no
ancestor for the path at its merge base, and still reports add/add the first
time it merges `main`. The resolution is the one the header gives — keep both
sides: the branch's own entries, plus whatever this file has that the branch's
copy does not (the conflict note above and the two CF-386 entries). Nothing on
`main` competes with a branch's entry, so there is nothing to choose between.
After that one merge the path merges normally. Branches cut from `main` after
this lands never see the add/add at all.

Reconciling the in-flight branches is the second half of CF-386 and is not done
here.

The header carries the convention, including that a conflict here is always
resolved by keeping both sides. It is stated in this file rather than in
`docs/overnight/TICKETS.md` because this is the file a run has open at the very
moment it is about to write an entry, which is where a convention actually gets
read.

**Worth knowing.** This is a general shape rather than a mobile one. Any file
that several branches are each expected to *create* — a changelog, a decision
log, an ADR index, a registry — has to exist on the mainline before the branches
that write it, or every merge after the first is a conflict with no base. Seed
it with what is invariant — a header, the headings — rather than with content
that is still moving on the branches, or the seed goes stale while it waits.
