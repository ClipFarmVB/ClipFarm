# Skein install notes

A second draft of ClipFarm's three Skein files, made by the fixed `skein init` on 2026-10-22
from main at 4571f2c, for Saturday's install. Nothing here is decided yet: every entry in
`decisions.md` is `Decided by: draft` with `Confirmed: no`, and every handle in `owners.md` is
blank. On Saturday the team corrects the draft, confirms what is right, deletes what isn't, and
fills in owners.

The record lives on the `skein` branch, never on main: the Skein server commits and pushes
`skein/decisions.md` there after every answer, so branch protection and review never stand
between an answer and the record. This PR targets `skein`; merging it lands the draft there.

## What changed since the first draft

The first draft's review found five problems. This run:

1. Reads only what git tracks (`git ls-files`), so the two `.pytest_cache` READMEs the first
   run cited cannot be read, and never reads any file whose name looks like a secret
   (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `credentials*`, `secrets*`), tracked or not.
   ClipFarm's tracked `.env.docker.example` was not read.
2. Starts the boundary narrow. "Skein may settle" holds only the kinds of convention the draft
   covers (formatting, file layout, API shape here; no naming rule made the cut). "Always a
   person" holds tuning and thresholds, dependencies and licensing, migrations and deploys,
   security, users' data, money and deleting things, whatever the drafts say. The first
   draft had "Pipeline tuning", "Dependencies" and "Migrations" on the safe list; they are
   gone. The team widens the list; init does not.
3. Selects by importance, not read order. Each candidate is scored: firm wording (must,
   never, always, don't), a rule reviewers repeated across PRs, a rule that gates work; a
   PR-template checklist item ranks last; ties go to the newer source. 151 candidates, top 30
   kept. The score and reason for each kept entry is in the run summary below, and each
   entry's `Why` ends with its sources and the date each last changed.
4. Says what may be edited. Drafts may be edited or deleted freely; a confirmed entry is never
   edited or deleted, only overridden by a newer entry that cites it, and Skein stops reading
   the overridden one.
5. Ignores Ollienel777 for owner hints (`--ignore-reviewer Ollienel777`): that account gave 14
   of the 27 approvals on the PRs read, most of them through the agent loop, so it would have
   been the hint on every topic. The remaining hints name OwenChen07 and chessdudeo7.

Also new: a PR that touched only `.claude/` is dropped entirely, review text included, so the
agent loop's own review rules stay out. A few still slipped in through PRs that touched both
loop files and team code (D-0022 and D-0023 look like loop rules); delete them on Saturday.

## What init read, and what it did not

```
excluded, never read: .claude, node_modules, .git, docs/overnight; untracked files and secret-shaped names never read
read: 9 docs, 4 config files, 0 CODEOWNERS rules, 180 merged PRs (98 reviews, 10 review comments)
decisions.md: 30 draft entries (3 from docs, 27 from PR reviews, 0 from config), 121 more found and left out, 0 dropped for no citation
boundary.md: 3 may-settle lines, 7 always-a-person lines
owners.md: 19 topics, 3 with a GitHub hint, every handle blank
approvals by account: Ollienel777 14, OwenChen07 7, Kunyuan1 5, chessdudeo7 1 (27 in all; ignored for hints: Ollienel777)
```

- Scoring favours review-enforced rules, so 27 of 30 entries now cite a PR; the first draft
  had 24 from docs. The CLAUDE.md and ARCHITECTURE.md conventions are mostly in the 121 left
  out; if the team wants them, the call is where to add them, confirmed on the spot.
- There is no CODEOWNERS file, and review text is thin (98 reviews, 10 inline comments).
- Kept, best first (score: reason):
  - Run ml tests in CI (7: firm wording, repeated across 2 PRs, gates work, newest 2026-10-20)
  - Migration chain must have one head (7: firm wording, repeated across 2 PRs, gates work, 2026-08-28)
  - Run pre-commit checks before PR (7: firm wording, repeated across 2 PRs, gates work, 2026-07-10)
  - Rebuild stale PRs without squashing (6: firm wording, repeated across 3 PRs, 2026-08-30)
  - Acquire lock after abandoned check (5), Probe Python by running it (5), Keep shallow
    healthz for Render (5), Re-read visibility section after #481 (5), Name unmeasured risks
    in code (5), Scrub passwords in every rendering (5), Gate debug hooks behind
    settings.debug (5), Rebase onto main before merging (5), Keep CPU fallback for Modal (5)
  - Compare pre-gate and post-gate (4), Do not promise erasure yet (4), Do not add root
    pyproject.toml (4)
  - Fourteen more at 3: each firm or review-enforced, one source, dated 2026-09-29 to 2026-10-20.
- Left out, highest first: Never expose generated usernames publicly (3), Keep group class on
  BrandMark parents (3), Re-derive clip visibility on reads (3), Never return known-stale
  cached data (3), Velocity thresholds in px/second (3), Keep public player names on clips (3),
  Download authorization reuses share check (3), Keep one visibility ranking ladder (3), Never
  force-push PR branches (3), No nit commits after settling (3), and 111 more.

## Patterns init saw but could not cite

Init found 66; the first 15. Each is a question for the team, not a rule.

- Whether pre-commit checks (ruff, mypy, eslint, tsc) must pass before opening a PR is implied by the checklist but not stated outright.
- Whether pipeline changes must be validated against a cached trajectory or test video is offered as a checkbox option, not a clear requirement.
- Whether the inference package pin to 1.3.3 must be kept if ML dependencies are re-added.
- Whether the 1 speed sample per second abstain threshold is a fixed rule or a tunable.
- Whether an agent may run alembic upgrade head against a local database without asking.
- Whether sprint assignment on project cards may ever be automated.
- Whether production CORS origins may ever include a wildcard entry is not stated outright beyond the config check.
- Whether the python3 versus python interpreter rule applies to agent-run commands is not stated clearly.
- Whether the linter and formatter must be run before committing is not stated in this material.
- Whether the Roboflow ball-tracking weights volleyball-ball-tracking-0eo7r/3 are licensed for commercial use; the audit left this open.
- Whether shipping a Modal image containing a GPL ffmpeg counts as conveying, which the audit flagged but did not resolve.
- Whether a new copyleft dependency needs review or sign-off before it is added.
- Whether players and teams should gain a deletion path, and how, is not stated as a rule.
- Whether the Roboflow inference package reports usage to Roboflow is not verified in the material.
- Whether ruff itself must be pinned to a specific version in CI.

## The three questions to settle first

1. **Who approves what?** No reviewer rule is written down and there is no CODEOWNERS file.
   Name an owner for each `owners.md` topic and a default, and say whether migrations,
   pipeline changes and deploys need a specific person.
2. **What may an agent run against shared infrastructure?** Supabase migrations, `modal
   deploy`, the OOM repro and pushing images are described in the docs but never ruled on.
   They sit under "Always a person" now; decide whether any of them may ever be settled from
   the record.
3. **Who owns licensing and user-data calls?** GPL ffmpeg in images, the Ultralytics and
   Roboflow weights, and what happens to a deleted user's data come up repeatedly with no
   owner. Decide whether they gate a deploy and who answers.
