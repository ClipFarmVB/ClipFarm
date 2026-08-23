# Unattended run brief

Instructions for an agent running `/loop` with nobody watching — overnight, or
any stretch where questions cannot be answered.

Start one with:

```
/loop Read OVERNIGHT_RUN.md and follow it exactly. Re-read .claude/overnight-log.md first each iteration so you do not repeat work.
```

The log stays gitignored on purpose: it is scratch memory for one run. The report
that has to survive is posted as an issue — see [Reporting](#reporting).

> **Update "This run" before starting.** Everything under
> [Standing policy](#standing-policy) holds every time. The section immediately
> below does not, and an agent given a stale scope will work confidently on the
> wrong things. This repository has been bitten by exactly that: CF-192's
> worst-case reasoning was invalidated by CF-224 without the text changing, and
> CF-224 read as "fixed" while production was still failing.

---

## This run

**Last updated: 2026-08-22.** If that date is not recent, stop and ask before
running.

### In scope, in this order

| Card | Issue | |
| --- | --- | --- |
| CF-235 | #235 | Reject wildcard `CORS_ORIGINS` in production |
| CF-236 | #236 | Validate avatar magic bytes |
| CF-186 | #189 | Public profile enumeration — rate limit or accept |

Once those are done or blocked, use your judgement on other `Todo` items in the
current sprint. A draft PR that turns out to be wrong costs review time, not
damage — so prefer well-specified, independently testable tickets, and prefer
finishing one to starting three.

### Out of scope, and why

Each of these fails for a specific reason, not merely for being large. If you
think one is worth doing, write the argument in the log instead of starting it.

- **CF-223 (#223)** — profiling needs `STAGE_TIMING` from a production run.
  Render is suspended, so that data does not exist yet.
- **CF-215 (#215)** — is the deployment itself.
- **CF-75 (#88)** — Terms of Service and Privacy Policy. Generated legal text
  that ships to real users is a real liability, and this card gates public
  signups, so there is pressure to merge it as-is. Draft *notes on what the
  documents must cover* into the log if useful; do not open a PR containing the
  documents.
- **CF-64 (#72)** — Stripe payments. Unattended changes to a payment flow are a
  bad trade even with review.
- **CF-73 (#85)** — UI rebuild, explicitly design-led.
- **CF-77 (#90)** — production secret management. You may not read credentials,
  so you cannot finish it. Structure and documentation are fine.
- **CF-112 (#142)** — PR #214 is already open for it.
- **#216, #220** — titled "placeholder". Flag them in the log.

### Environment notes for this run

- Render is **suspended** at $0/month. Starting it costs money.
- `claude-review.yml` is **disabled**, so nothing reviews PRs automatically.

---

## Standing policy

### Hard rules

- **Never** push to `main`, merge a PR, or force-push anything.
- **Never** deploy, unsuspend a hosting service, or touch production
  infrastructure.
- **Never** run the local stack against a `DATABASE_URL` pointing at Supabase.
  Confirm `.env.docker` names the local `db` container first.
- **Never** read, echo, or commit `.env.docker` or any credential.
- Every PR opens as a **draft**. You never merge and never deploy.
- **Maximum 5 new PRs** and **6 new cards** per run.
- If a command fails because of usage limits, **stop the loop** — do not retry.
- If nothing in scope is actionable, **stop the loop**. A run that reviews two
  PRs and opens nothing is a fine outcome.

### Log before you finish each iteration

Append a dated section to `.claude/overnight-log.md`: what you did, what you
decided and why, and anything needing a human call. **Read it at the start of
every iteration.** Context may be compacted between iterations; the log is the
only thing that survives.

### Priority order

Finish work already in flight before starting anything new.

**1 — Review open PRs that changed since their last review.**

Compare each PR's head commit against the commit of its most recent review. If
there are commits since, run `/code-review` and post the findings as one review
comment, in tiers:

- **Critical** — correctness, security, data loss
- **Medium** — should fix before merge
- **Nit** — style, naming, comments

Challenge the design where warranted, not only the code; say so when a premise
looks wrong. **Verify claims against the repository** rather than trusting the PR
description — that has caught real errors here more than once. Never mark a
finding confirmed without checking it.

Do not use `/code-review ultra`; it is billed separately and user-triggered.

**2 — Address review findings on PRs owned by whoever started this run**
(`gh api user -q .login`). If a fix needs no human decision, implement it, push
to that PR's branch, and reply on the thread saying what changed. If it needs a
judgement call, log it and leave it.

**3 — Only when 1 and 2 are clear**, take one ticket from "This run".

### Working a ticket

1. **Plan first.** Read the card and the code it touches. Write the plan into the
   log: approach, files, migration if any, tests, and what could go wrong.
2. **Cross-check the plan before implementing.** Spawn a subagent to review it
   against the actual repository, looking for stale assumptions about repo state,
   a migration number that collides, tests or CI steps that already exist, and
   anything the plan asserts without verifying. Record what it said — including
   when it disagreed and you proceeded anyway, with your reasoning.
3. **Implement** on a branch named for the card.
4. **Run the gate**: `ruff check api/`, `mypy api/app --ignore-missing-imports`,
   `cd api && python -m pytest tests/`, `python -m pytest ml/tests/`, and for web
   changes `npm run lint --workspace=web && npm run typecheck --workspace=web`.
   Do not open a PR if any fail — log it and move on.
5. **Open a draft PR** following `.github/pull_request_template.md`, including the
   bare `Closes #<issue>` line `CLAUDE.md` requires.

**Size discipline.** If a ticket would produce a diff too large to review in one
sitting, do not implement it. Write the plan into the log instead — a good plan
beats a half-finished 2000-line PR.

### Filing cards for out-of-scope findings

You will notice real problems that do not belong in the work at hand. File those
rather than fixing them inline or letting them evaporate.

**File** for: a bug, a security issue, a stale comment that would mislead the next
reader, missing coverage on something that matters, a premise no longer true, or
work a review surfaced that is bigger than that PR.

**Do not file** for: vague code smells, style preferences, or anything fixable
inline in under a minute.

- Title `CF-<n> · <what it is>`. Get `<n>` from the **highest existing CF number**
  across all issues — it has drifted from the issue numbers, so do not infer it.
- Match the house style of existing cards: what, why it matters, evidence with
  file and line references, options where there is a real choice, acceptance.
  CF-224 (#224) and CF-239 (#242) are good models.
- Labels from the existing set, including a priority.
- Add to the `ClipFarm Backlog` project with the **Sprint field left unset** —
  these are for triage, not for silently joining the current sprint.
- Open the body with: `Filed unattended during an overnight run — needs triage.`

### When a command is not available

You may be running in a sandbox rather than on a maintainer's machine. Docker in
particular may be absent, so anything needing `docker compose` — the local stack,
the eval harness — simply cannot run.

**Log that it could not run, and move on. Never report a gate as passing when you
could not execute it.** If a ticket's verification is impossible in the
environment you are in, write the plan and the reason into the log instead of
opening a PR.

### Repo traps that have already cost time

- Migration numbers collide. Check `api/alembic/versions/` for the current head;
  never assume.
- `api/tests/` exists and CI runs it. Test-only dependencies go in
  `api/requirements-dev.txt`, never `requirements.txt` — that file builds the
  production image.
- CF numbers have drifted from issue numbers. Check the highest existing `CF-`
  number; do not infer it from the issue count.
- A closed issue may be `COMPLETED` or `NOT_PLANNED` — opposite facts behind the
  same `state`. Always read `stateReason`.

### Reporting

Post the run report as a GitHub issue titled `Overnight run — <YYYY-MM-DD>`,
labelled `chore`, added to the project with the Sprint field unset. A log file in
a sandbox is a report nobody reads; the issue is the copy that arrives.

Put the same summary at the top of `.claude/overnight-log.md`.

The report contains:

- PRs reviewed, and findings by tier
- PRs opened, with card and branch — and **what to test to verify each one**
- Cards filed, one sentence each on why
- Tickets attempted but abandoned, and why
- Every decision needing a human call
- Anything that failed, **verbatim** — do not summarise errors away

Be honest. A report that overstates what landed is worse than a short one.
