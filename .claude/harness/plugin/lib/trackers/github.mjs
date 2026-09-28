// Tracker: GitHub issues. Claims are a `claimed:` comment plus the claim
// label (comment first, so a cut-off run never leaves a label nothing can
// release); releases remove the label, then comment.
import { apiAll } from '../gh.mjs';
import { comment, editComment, addLabels, removeLabel } from '../writes.mjs';
import { eligibleTickets, latestClaim } from '../tickets.mjs';
import { nowUtc } from '../time.mjs';
import { fillTicketLine } from './common.mjs';

const issueNumber = (id) => {
  const m = /^#?(\d+)$/.exec(String(id).trim());
  if (!m) throw new Error(`a GitHub ticket id is an issue number, got "${id}"`);
  return Number(m[1]);
};

export function githubTracker({ repo, profile, login }) {
  const label = profile['claim label'];
  const comments = (n) => apiAll(`repos/${repo}/issues/${n}/comments?per_page=100`);

  return {
    name: 'github',

    tickets() {
      const issues = apiAll(`repos/${repo}/issues?state=all&per_page=100`);
      const openPrs = apiAll(`repos/${repo}/pulls?state=open&per_page=100`);
      const { eligible, skipped } = eligibleTickets({ issues, openPrs, profile });
      const shape = (t) => ({ id: `#${t.number}`, number: t.number, title: t.title, priority: t.priority, areas: t.areas, ...(t.why ? { why: t.why } : {}) });
      return { eligible: eligible.map(shape), skipped: skipped.map(shape), resumable: [] };
    },

    // A second claim by this account re-stamps the existing comment, so its
    // updated_at is when work was last handed out.
    claim(id) {
      const n = issueNumber(id);
      const now = nowUtc();
      const mine = latestClaim(comments(n), login);
      if (mine) editComment(repo, mine.id, `claimed: ${now}`);
      else comment(repo, n, `claimed: ${now}`);
      addLabels(repo, n, [label]);
      return { id: `#${n}`, wrote: mine ? `re-stamped claim comment ${mine.id}; label ${label}` : `claimed: comment; label ${label}`, claimedAt: now };
    },

    release(id, outcome) {
      const n = issueNumber(id);
      if (!outcome) throw new Error('release needs an outcome (for example "PR #14", "no PR", "orphaned by an earlier run")');
      removeLabel(repo, n, label);
      comment(repo, n, `released: ${nowUtc()} — ${outcome}`);
      return { id: `#${n}`, wrote: `removed ${label}; released: comment` };
    },

    synced(id) {
      return { id: `#${issueNumber(id)}`, skipped: 'nothing to sync for GitHub issues' };
    },

    ticketLine(id) {
      return fillTicketLine(profile['ticket line'], issueNumber(id));
    },
  };
}
