# Skein install notes

A first draft of ClipFarm's three Skein files, made by `skein init` on 2026-10-22 from main at
4571f2c, for Saturday's install. Nothing here is decided yet: every entry in `decisions.md` is
`Decided by: draft` with `Confirmed: no`, and every handle in `owners.md` is blank. On Saturday
the team corrects the draft, confirms what is right, deletes what isn't, and fills in owners.

## What init read, and what it did not

```
excluded, never read: .claude, node_modules, .git, docs/overnight
read: 9 docs, 4 config files, 0 CODEOWNERS rules, 0 merged PRs (0 reviews, 0 review comments)
note: gh pr list failed
decisions.md: 30 draft entries (30 from docs, 0 from PR reviews, 0 from config), 0 dropped for no citation
boundary.md: 15 may-settle topics, 7 always-a-person lines
owners.md: 17 topics, 0 with a GitHub hint, every handle blank
```

- `.claude/` was never read. Neither was `docs/overnight/`: it is the agent loop's operating
  manual, not the team's conventions, and in an earlier run it filled the open questions
  below with the loop's own rules.
- Merged PR reviews are missing from this draft. Reading them through `gh` failed in the last two
  runs (an earlier run read all 180 merged PRs: 101 reviews, 10 review comments, and kept one
  entry from them). Rerun with `--exclude docs/overnight --force` before Saturday to add them.
- There is no CODEOWNERS file, so `owners.md` has no hints at all.
- Init found 75 citable decisions in the docs and kept the first 30. Most cite `CLAUDE.md` and
  `ARCHITECTURE.md`. Some overlap (migrations, the tracking cache version); merge them on the
  call. So ClipFarm has well over ten citable decisions; the work is pruning, not finding.

## Patterns init saw but could not cite

Init found 30; it prints the first 15. Each is a question for the team, not a rule.

- Whether migration or env var changes need a named reviewer or separate sign-off.
- Who approves production migrations and deploys.
- Whether the 100 MiB multipart threshold is a fixed rule or a tunable.
- Whether polling may be replaced by WebSockets or SSE later.
- Whether an agent may run `alembic upgrade head` against Supabase when a person has not asked.
- Whether `highlight_score_threshold` changes need an eval run before merging.
- Whether a new api test may skip when its optional import is missing.
- Whether the E402 exception should extend to other worker modules.
- Whether to push Docker images to a registry, given a GPL ffmpeg in a distributed image.
- Whether to check the Roboflow ball-tracking weights' license before relying on that model.
- Whether the Modal pose image, which bundles GPL ffmpeg, needs a license review before deploys.
- Whether a deleted user's `upload_events` should cascade or be kept.
- Whether clips inside other owners' collections survive a user's deletion.
- Whether the inference package reports usage to Roboflow (not verified).
- Whether the live R2 bucket has the 7-day multipart abort rule (not verified).

## The three questions to settle first

1. **Who approves what?** No reviewer rule is written down, and there is no CODEOWNERS file.
   Name an owner for each `owners.md` topic and a default, and say whether migrations, env vars
   and deploys need a specific person.
2. **What may an agent run against shared infrastructure?** Running migrations on Supabase,
   `modal deploy`, and pushing images are described in the docs but never ruled on. Decide
   which are always a person, and put them in `boundary.md`.
3. **Who owns licensing calls?** GPL ffmpeg in the Modal image, the Roboflow weights and the
   ultralytics terms come up repeatedly with no owner. Decide whether they block a deploy and
   who answers.
