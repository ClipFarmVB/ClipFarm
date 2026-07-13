# GitHub Issues — backlog setup + workflow

The backlog lives in **GitHub Issues + Projects** (not the old Google Doc). This
folder sets it up and seeds the first cards. Card IDs stay `CF-##` in issue titles
so existing branches/PRs keep referring to them; GitHub's own `#N` is just the URL.

## One-time setup (any maintainer, once)

```bash
# 1. Auth the CLI (interactive browser flow — do this once per machine)
gh auth login

# 2. Confirm Issues is enabled: repo → Settings → General → Features → Issues.
#    (On by default; only needed if it was turned off.)

# 3. Create the label taxonomy (idempotent)
bash scripts/github/setup-labels.sh

# 4. Seed the backlog cards (skips titles that already exist — safe to re-run)
bash scripts/github/seed-backlog.sh
```

Then create a **Project board** for the kanban view: repo → Projects → New project →
Board template → add the "Status" field (Backlog / In progress / In review / Done) and
pull in the issues. (The board is easiest to configure once in the UI; issues are the
source of truth, the board is just a view.)

## Adding a card later

Drop a file in `backlog/` and re-run the seed script — or just
`gh issue create` directly. A backlog file is:

```markdown
<!-- title: CF-59 · Short imperative title -->
<!-- labels: devops, feat, P1 -->
Body markdown here. What / why / acceptance / depends.
```

`labels` are comma-separated and must already exist (see `setup-labels.sh`). The seed
script reads the two header lines, uses the rest as the issue body, and skips the card
if an open issue with that title already exists.

## Day-to-day, from Claude Code

Once `gh auth login` is done on an engineer's machine, their Claude Code can manage the
board directly in chat via the `gh` CLI — e.g. create, label, comment, close:

```bash
gh issue list --repo ClipFarmVB/ClipFarm --label devops
gh issue create --repo ClipFarmVB/ClipFarm --title "CF-59 · …" --body "…" --label web,feat,P2
gh issue comment 42 --body "picked this up on branch web/CF-59-…"
gh issue close 42 --comment "done in #57"
```

PRs auto-close their issue when the description says `Closes #<n>` (or `Closes CF-59`
if you keep the number aligned). Keep `CF-##` in the branch name per
[CONTRIBUTING.md](../../CONTRIBUTING.md).

## What's seeded here

The nine forward-looking cards specced in July 2026: the virtual-clip / mezzanine-proxy
epic (CF-48–54) and the model-evaluation harness (CF-55–56). Older/parked cards from the
Google Doc backlog still need porting — do that by adding `backlog/*.md` files (or ask an
agent to convert them).
