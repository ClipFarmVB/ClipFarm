# Skein install notes

A first draft of ClipFarm's three Skein files, made by `skein init` on 2026-10-22 from main at
4571f2c, for Saturday's install. Nothing here is decided yet: every entry in `decisions.md` is
`Decided by: draft` with `Confirmed: no`, and every handle in `owners.md` is blank. On Saturday
the team corrects the draft, confirms what is right, deletes what isn't, and fills in owners.

## What init read, and what it did not

```
excluded, never read: .claude, node_modules, .git, docs/overnight
read: 11 docs, 4 config files, 0 CODEOWNERS rules, 180 merged PRs (101 reviews, 10 review comments)
decisions.md: 30 draft entries (24 from docs, 6 from PR reviews, 0 from config), 5 dropped for no citation
boundary.md: 12 may-settle topics, 9 always-a-person lines
owners.md: 16 topics, 3 with a GitHub hint, every handle blank
```

- `.claude/` was never read. Neither was `docs/overnight/`: it is the agent loop's operating
  manual, not the team's conventions, and in an earlier run it filled the open questions
  below with the loop's own rules.
- All 180 merged PRs were read. Review text is thin (101 reviews, 10 inline comments), so most
  entries still come from `CLAUDE.md`, `ARCHITECTURE.md`, the PR template and the README; six
  come from PR reviews and cite the PR.
- There is no CODEOWNERS file. The three GitHub hints in `owners.md` name who approved the
  PRs those entries cite; treat them as suggestions.
- Init found 161 citable decisions and kept the first 30. Some overlap (pre-commit checks,
  migrations); merge them on the call. ClipFarm has far more than ten citable decisions; the
  work is pruning, not finding.

## Patterns init saw but could not cite

Init found 83; it prints the first 15. Each is a question for the team, not a rule.

- Whether migrations need a separate reviewer or approval step beyond flagging them in the PR.
- Which pre-commit checks apply to which files when only some tools are relevant.
- Whether the deterministic `condensed/{game_id}.mp4` R2 key may ever change, since retries
  rely on overwriting it.
- Whether the claude-review check should become a required status check, since skipped runs
  report success.
- Whether detection or pipeline changes need an explicit reviewer sign-off.
- Whether agents may run `modal deploy` themselves; the README shows the commands but not who
  runs them.
- Whether the CF-224 OOM repro overlay should be run by agents or only by people on
  production-sized machines.
- Whether the per-file lint ignores apply to tests and scripts outside `app/` and `alembic/`.
- Whether the 120-character limit applies to non-Python files in the api project.
- Whether the Ultralytics pretrained weights' AGPL terms bind a hosted deployment.
- Whether pushing an image to Modal's infrastructure counts as conveying a GPL ffmpeg.
- Whether the volleyball ball-tracking weights permit commercial use (Roboflow license lookup).
- Whether players and teams rows should get a deletion path; they hold names tied to footage.
- Whether a generated handle should be deleted or kept when a user leaves.
- Whether the email column should update when the Supabase email changes.

## The three questions to settle first

1. **Who approves what?** No reviewer rule is written down and there is no CODEOWNERS file.
   Name an owner for each `owners.md` topic and a default, and say whether migrations,
   pipeline changes and deploys need a specific person.
2. **What may an agent run against shared infrastructure?** Supabase migrations, `modal
   deploy`, the OOM repro and pushing images are described in the docs but never ruled on.
   Decide which are always a person, and put them in `boundary.md`.
3. **Who owns licensing and user-data calls?** GPL ffmpeg in images, the Ultralytics and
   Roboflow weights, and what happens to a deleted user's data come up repeatedly with no
   owner. Decide whether they gate a deploy and who answers.
