---
name: harness-init
description: (Repo-pinned copy of the harness plugin's init skill, for sessions without the plugin; prefer harness:init where the plugin is installed.) Set up the harness in a repository by writing its profile (.claude/harness/profile.md) from the closest template (solo, hackathon, clipfarm, skrypt), filled in from the repo itself, then validating it. Use when the user wants to use the harness or build loop in a new repo, asks to set up, configure or adopt the harness, or when /harness-build-loop finds no profile.
argument-hint: "[solo|hackathon|clipfarm|skrypt]"
---

# Set up the harness in this repo

Write `.claude/harness/profile.md` for this repository and validate it. The
profile is the only per-repo part of the harness: everything else lives in the
plugin at `${CLAUDE_PROJECT_DIR}/.claude/harness/plugin`.

`harness` below is `node ${CLAUDE_PROJECT_DIR}/.claude/harness/plugin/bin/harness.mjs`. Its read
commands (`whoami`, `get`) work before a profile exists, and without `gh`.

Arguments: `$ARGUMENTS`

## 1. Pick the template

Templates are in `${CLAUDE_PROJECT_DIR}/.claude/harness/plugin/profiles/`. Read the one you pick in
full.

| the repo | template |
|---|---|
| origin is `skrypt-labs/*`, or it has `scripts/work-session.ts` | `skrypt.md` (only skrypt-os and automations are supported by the work-session CLI; for another Skrypt repo, say so and stop) |
| origin is `ClipFarmVB/ClipFarm` | `clipfarm.md` |
| it has `hackathon/EVENT.md`, or the user says it is for a hackathon | `hackathon.md` |
| anything else | `solo.md` |

**A repo this account does not own is a team repo**: never `merge policy:
auto` there, and ask before anything that writes to GitHub (labels, issues).
Check with `harness get repos/OWNER/REPO` (`owner`, `permissions`).

## 2. Fill it in from the repo, not from guesses

- **`repo`, `base`**: `harness get repos/OWNER/REPO` (`full_name`, `default_branch`).
- **`gate`**: the one command CI runs. Read `.github/workflows/` and the
  package scripts. If CI runs several steps with no single script, write them
  under "The gate" in the prose and set `gate:` to the closest single command;
  say which steps it misses.
- **`branch`, `ticket line`**: from the PR template and recent merged PRs
  (`harness get "repos/OWNER/REPO/pulls?state=closed&per_page=20"`).
- **Labels**: `harness get repos/OWNER/REPO/labels --all`. If the repo's labels already mean
  ready / claimed / hold / settled / unsettled, map them in the profile rather
  than creating new ones. With no permission to create labels, use `labels:
  no` and `pr scope: loop-body`.
- **`high risk`, `harness paths`**: from `CLAUDE.md` and the repo's docs. Every
  path you list must exist (`test -e`).
- **The prose sections**: the product, what is valuable, the gate's services
  and silent skips, and the traps — from `CLAUDE.md`, the README and the docs.
  Short and specific; this is what every subagent is told.

Ask the user only for what the repo cannot tell you: usually the merge policy
for a repo they own, and what is valuable.

## 3. Write, keep it out of the product, validate

1. Write `.claude/harness/profile.md`.
2. **The log and status must be gitignored**: `git check-ignore -q
   .claude/harness/log.md`. If not, add `/.claude/harness/log.md` and
   `/.claude/harness/status.md` to `.gitignore` in a repo the user owns; in a
   team repo, say what to add and stop.
3. **The profile itself**: in a repo the user owns, offer to commit it
   (`git add -f` if `.claude/` is ignored) so every clone and worktree has it —
   it is a harness path, so the loop can never merge a change to it. In a team
   repo, leave it uncommitted.
4. **Permissions, in a repo the user owns.** An unattended run stalls on its
   first prompt. Offer a committed `.claude/settings.json` allowlist for what
   the loop runs — `Bash(git:*)`, `Bash(gh:*)`, `Bash(node:*)`, the gate's
   toolchain (`Bash(npm:*)`, `Bash(npx:*)`, `Bash(pnpm:*)`, `Bash(python3:*)`,
   `Bash(pytest:*)`, `Bash(uv:*)`). In a team repo, never commit settings;
   tell the user what to allow locally. **Never commit a marketplace entry**
   (`extraKnownMarketplaces`) for the harness: a project-level
   `nelson-harness` overrides the user's local directory marketplace of the
   same name, and cloud sessions ignore the key anyway. The harness runs in
   local sessions, from the user-scope install.
5. `node ${CLAUDE_PROJECT_DIR}/.claude/harness/plugin/bin/harness.mjs profile` must print it without
   an error. Fix and re-run until it does.
6. Show the user the settings block and one line on what the loop will and
   will not do under it (what it may merge, which PRs it touches, where its
   status goes).

## 4. Replacing an in-repo harness

If the repo already carries its own copy of the harness (`docs/build/`,
`docs/overnight/`, `docs/ideation/`, `.claude/skills/{build-loop,overnight,
ideate,review-pr,ui-craft,prompt-writing,task-guidance}`, `scripts/toutc.mjs`),
say so. The plugin replaces them, and its skills are namespaced
(`harness-review-pr`), so both copies would trigger. Removing the old copy is
a PR the user reviews: offer to open it, listing every file and anything in
the old copy the profile does not yet cover. Never delete them unasked.
