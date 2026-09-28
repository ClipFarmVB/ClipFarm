---
name: harness-ideate
description: (Repo-pinned copy of the harness plugin's ideate skill, for sessions without the plugin; prefer harness:ideate where the plugin is installed.) Run the autonomous hackathon ideation loop. It takes the event's track descriptions, generates ideas with independent subagents, scores and red-teams them over several rounds, then stops with a decision packet for the human. Also records the human's choice (decide) or feedback for another round (refine). Use when the user wants hackathon ideas, uploads or pastes track descriptions, asks to start or resume ideation, picks an idea from the packet, or gives feedback on the shortlist.
---

# Ideate

Start, resume or close the ideation loop that `${CLAUDE_PROJECT_DIR}/.claude/harness/plugin/brief/packs/hackathon/ideation/` describes. This
skill prepares the inputs and starts the loop. The brief governs everything
the loop does, so **do not restate its rules here or in the prompt**.

UTC timestamps below mean the output of `node ${CLAUDE_PROJECT_DIR}/.claude/harness/plugin/bin/harness.mjs now`
(any shell). A bare `date` in PowerShell prints local time, and Git Bash's has no
timezone data.

## What the user typed

| the user typed | do |
|---|---|
| `/harness-ideate`, `/harness-ideate 3m` | [start or resume](#start-or-resume), with a `5m` interval unless one is given |
| `/harness-ideate decide R2-03 notes…` | [decide](#decide) |
| `/harness-ideate refine feedback…` | [refine](#refine) |
| `/harness-ideate note feedback…` | append a `note` entry to `FEEDBACK.md`, verbatim. It binds later rounds and starts nothing. |
| `/harness-ideate status` | report the matching row of the phase machine in `${CLAUDE_PROJECT_DIR}/.claude/harness/plugin/brief/packs/hackathon/ideation/RULES.md`, the top of the latest combined board, the budget used, and whether a loop job exists |

Plain language counts:
- "Here are the tracks" means start.
- "Go with the voice one, but no hardware" means decide.
- "None of these, more playful" means refine.
- "By the way, I have no GPU" means note.

## Start or resume

1. **Get the inputs on disk.**
   - Save pasted track text, attached files and URLs into `hackathon/tracks/`
     (URLs go into `links.md`).
   - Fill anything the user said about the event into `hackathon/EVENT.md`.
   - Say what you saved.
2. **Check what intake cannot guess.** There must be readable track text, and
   `EVENT.md` must give a real submission deadline and judging criteria, not
   `TODO`. Ask for anything missing now.
3. **Check for another run.** This session may already have this loop's job
   scheduled. If it does not, and `hackathon/ideation/LOCK` was refreshed in
   the last 15 minutes, another session is probably driving a loop. Ask before
   starting a second one.
4. **Check whether the inputs changed.** If `brief.md` exists and lists inputs
   older than the current files in `hackathon/tracks/` or `EVENT.md`, the brief
   is stale. Offer to archive the run and start fresh:
   1. Move everything under `hackathon/ideation/` except `archive/` into
      `hackathon/ideation/archive/STAMP/`, where `STAMP` is a UTC stamp such
      as `20261001T142000Z`.
   2. Truncate the log.
5. **Is the run already finished?** A finished run has a complete packet, and
   no `refine` entry that a round has not consumed. Starting a loop then would
   stop on its first lap. Instead, point the user at `decide`, `refine`, or
   archiving for a fresh run.
6. **Resuming?** A `run start:` line in `.claude/ideation-log.md` means a run is
   resuming.
   - Append `resumed: <UTC>`.
   - If the budget window since the latest `budget from:` line is already spent
     and the run has no packet yet, ask the user: extend the budget (append a
     new `budget from: <UTC>`) or go straight to the shortlist.
   - If the log is empty, write `run start: <UTC>` and `budget from: <UTC>`.
7. **Tell the user**, in a few lines:
   - **What happens.** Research, then rounds of generation, judging and
     red-teaming, then a packet. At the default settings this takes roughly
     2.5–3.5 hours. For a quicker pass, set `ideation rounds: 2` and
     `ideation budget: 90m` in `EVENT.md`.
   - **What it never does.** Sign-ups, keys, commits or publishing.
   - **What it needs.** This session open, the machine awake, and a permission
     mode that will not prompt for:
     - web search and page fetches
     - subagents
     - file writes and renames under `hackathon/ideation/`
     - the UTC `date` command and scratch scripts
     - the scheduler tools

     The loop stalls on the first prompt nobody answers. Offer to add a project
     allowlist for these, and a denylist for browser tools, `git push` and `gh`
     writes, if the user wants one.
8. **Start the loop.** Invoke the `loop` skill with the interval and this
   prompt:

   ```
   Read ${CLAUDE_PROJECT_DIR}/.claude/harness/plugin/brief/packs/hackathon/ideation/README.md and follow the brief it indexes, per the reading protocol in it. The harness plugin root is ${CLAUDE_PROJECT_DIR}/.claude/harness/plugin. Re-read .claude/ideation-log.md first each iteration so you do not repeat work.
   ```

   **Use a fixed interval, never self-pacing.** A fixed interval re-arms itself,
   fires only between turns, and never stacks missed ticks. If this session
   already has a job for this prompt, do not add a second one.

## Decide

1. **Find the ID** in `hackathon/ideation/PACKET.md`, or in any
   `round-*/PACKET.md`. If it appears only in an older packet, confirm that is
   what the user means. If no packet contains it, show the IDs that exist.
2. **Follow the handoff** in `${CLAUDE_PROJECT_DIR}/.claude/harness/plugin/brief/packs/hackathon/ideation/SHORTLIST.md`, under *The handoff*,
   putting the user's notes verbatim at the top of `hackathon/DECISION.md`.
3. **Confirm no ideation job is still scheduled.**
4. **Tell the user what comes next.**
   - The human actions the decision lists: sign-ups and keys, with links.
     Keys go in `.env.local` and in GitHub Actions secrets.
   - `/harness-build-loop` once hacking starts, in a repo whose profile has
     `pack: hackathon` (`/harness-init` writes one). `/harness-build-loop
     plan-only` before then writes the spec locally, for review.
   - Do not commit `DECISION.md`. The build loop's spec PR does that.

## Refine

1. **If the feedback is ambiguous enough to send a whole round the wrong way**,
   ask one clarifying question before writing anything. "More playful" is
   clear. "Not that" is not.
2. **Append an entry** to `hackathon/ideation/FEEDBACK.md`: a line
   `## <UTC> — refine`, followed by the user's words verbatim. Feedback binds
   later rounds and must not be paraphrased.
3. **If a loop is already running, stop here.** It picks up the entry at its
   next verdict, or right after its packet if it is already shortlisting.
4. **Otherwise, start the loop** as above, skipping the "finished" check. The
   phase machine starts a refine round from any `refine` entry that no round has
   consumed, and refine rounds are exempt from the budget.
