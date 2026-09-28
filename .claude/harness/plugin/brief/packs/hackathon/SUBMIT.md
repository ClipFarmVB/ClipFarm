# Submit: rehearsals, polish, and the endgame

Read in the **polish and submit phases**, on any lap that runs a rehearsal, and
when filing or dispatching `submission` tickets.

Part of the hackathon pack. See [`README.md`](./README.md).

---

## Rehearsal

A rehearsal is the closest the loop gets to the judging table. Run one:

- at the end of M1, M2 and M4, before the triage each of those triggers
- once on entering polish
- as the last act of the submit phase

Record the time of each in the Now block.

**Setup.** Dispatch one rehearser, with worktree isolation, on `origin/main` at
its current head. Give it:

- the absolute path of the main checkout
- **permission to copy the main checkout's `.env.local` into its worktree with
  `cp`**, never displaying it, so that live mode can run where keys exist
- the output directory: an **absolute path** in the main checkout,
  `hackathon/rehearsals/STAMP/`, where `STAMP` looks like `20261003T221500Z`.
  There are no colons in the stamp because Windows paths cannot hold them.
  The directory is gitignored, and it survives the worktree being cleaned up.

**Its job.** Run the app the way the demo will run: in live mode where keys
exist, and in fake mode otherwise. Walk `SPEC.md`'s demo script beat by beat in
a real browser at 1440×900. For each beat, record:

- whether it works
- a screenshot
- how long it took, noting any wait over about a second with nothing on screen
  to show progress
- console errors
- anything objective the `ui-craft` rubric would flag

**It also checks:**

- **A clean clone.** Install and start in fake mode following only the README,
  because that is what a judge who clones the repo gets.
- **The fallbacks.** For each live dependency, switch it to fake and confirm the
  beat still works. Compare against the live run, where one happened.

It writes `report.md` in the output directory. The orchestrator puts a summary
into the status issue.

**Every broken beat becomes a card**: P0 in polish or submit, P1 before that.
Objective defects on demo screens become `ui` cards.

**At every rehearsal from polish onwards (the M4 rehearsal included), if every
beat works**, tag that head as the demo candidate:

```
git tag demo-STAMP SHA
git push origin demo-STAMP
```

Name the latest tag in the status issue. If late merges break the demo, the
human still has a known-good build to record the video on.

## Polish

Polish is where hackathons are won between projects that both work. On
entering polish:

1. **Triage.**
   - Every open feature ticket with no PR is deferred: closed as not planned,
     or labelled `hold`, with a line saying why. A feature PR already open is
     left to finish its cycle. Give it `hold` only if the triager judges it a
     risk to the demo.
   - `ui` tickets are filed for every demo screen below the rubric, using the
     rehearsal's screenshots as evidence. The order is: missing states (empty,
     loading, error, success) first, then real-looking data, then layout at
     1440×900, then motion and detail.
   - Every missing `submission` ticket below is filed.
2. **Send the human the draft checklist** ([the endgame](#the-endgame)) now,
   not in the final hour. Recording a video and writing a submission take
   longer than an hour of a tired human's time.

## Submission materials

Each item below is a `submission` ticket in M5, and goes through a reviewed PR.
Anything that needs a human becomes a checklist item, not a ticket.

- **`README.md`**, replacing the harness pointer that sits there now:
  - the one-liner and a hero screenshot
  - the problem, told through the story
  - the demo video link, once the human has one
  - the live demo URL, if the human has deployed one
  - how it works, with a Mermaid architecture diagram
  - **the sponsor technology used, and where in the code**. Prize judges look
    for this.
  - how to run it, including fake mode with no keys
  - the stack, and what is next
- **`hackathon/submission/DEMO.md`**:
  - the final demo script, with timings and a talk track
  - the fallback for every beat
  - the command that resets the demo data
  - a pre-demo checklist: keys loaded, the fake-mode switch, browser zoom,
    notifications off
- **`hackathon/submission/devpost.md`**:
  - a write-up in the sections Devpost asks for: Inspiration; What it does;
    How we built it; Challenges we ran into; Accomplishments that we're proud
    of; What we learned; What's next
  - the "built with" tags
  - one line per targeted prize on how its requirement is met
  - disclosure of AI use, if the event requires it
- **`hackathon/submission/video.md`**:
  - a shot list and narration, aligned to the demo beats and within the
    event's length limit
  - optionally, raw Playwright video of the demo path, for the human to cut
- **A gallery.** 16:9 screenshots of the key screens, taken from the final
  build, in `hackathon/submission/media/`.
- **`hackathon/submission/prizes.md`**: for each prize, its requirement, the
  evidence (a file or a screen), and its status.
- **Hygiene.**
  - `.env.example` is complete.
  - The licence the event requires is present.
  - A secret scan of the full history is clean, using `gitleaks` if it is
    available. Otherwise say that no scan ran, rather than calling the history
    clean.

## The endgame

**The checklist** lives in the status issue. It lists every submission
requirement from `EVENT.md`, with a link to what satisfies it. Then it lists
the human's actions:

- a live-mode dry run of the demo, on the demo-candidate tag
- recording the video
- deploying the live demo, if the event wants one
- filling in and submitting the form
- choosing the prizes
- changing repository visibility, if required

It is sent in draft on entering polish, and final in the submit phase.

**In the submit phase:**

1. **Start no new implementers.** Advance and merge only P0 and `submission`
   PRs that are already in flight, and only work that will finish before the
   deadline margin ([the clock](README.md#the-clock)).
2. **Run the final rehearsal** on `origin/main`.
3. **Finalise the checklist**, and notify the human.
4. **At deadline − 10 minutes, the phase is over.** Stop every subagent, then
   [stop](../../loop/RULES.md#stopping). The loop never submits.
