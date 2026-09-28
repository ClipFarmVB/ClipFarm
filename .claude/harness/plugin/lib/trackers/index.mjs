// The tracker interface. The brief calls these five operations whatever the
// tracker is; each adapter maps them onto its own system.
//
//   tickets()              -> { eligible: Ticket[], skipped: Ticket[], resumable: Ticket[] }
//   claim(id)              -> { id, wrote, branch?, worktree?, next? }
//   release(id, outcome)   -> { id, wrote }
//   synced(id, { cwd })    -> { id, wrote | skipped }
//   ticketLine(id)         -> string   (the line a PR body must carry)
//
// Ticket: { id, title, priority, areas, reason?, why?, worktree?, branch? }.
// `eligible` is in the order to take it; the orchestrator never reorders it.
import { githubTracker } from './github.mjs';
import { skryptTracker } from './skrypt.mjs';
import { TrackerError } from './common.mjs';

export { TrackerError, fillTicketLine } from './common.mjs';

export function getTracker(ctx) {
  switch (ctx.profile.tracker) {
    case 'github': return githubTracker(ctx);
    case 'skrypt': return skryptTracker(ctx);
    default: throw new TrackerError(`no adapter for tracker "${ctx.profile.tracker}"`);
  }
}
