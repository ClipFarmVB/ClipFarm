// GitHub-issue tickets: eligibility, the issues open PRs already close, and
// claims. All REST (no GraphQL: cloud sessions refuse it at the proxy).
import { firstLine } from './pr.mjs';

const CLOSES_RE = /\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b/gi;
const DEPENDS_RE = /\b(?:depends on|after|blocked by|needs)\s+((?:#\d+[\s,and]*)+)/gi;
const PRIORITY = ['P0', 'P1', 'P2', 'P3'];

// Issue numbers an open PR's body says it closes. The body, not commit
// messages: a squash merge carries every commit body onto the base.
export function linkedIssues(openPrs) {
  const map = new Map();
  for (const pr of openPrs) {
    for (const m of (pr.body ?? '').matchAll(CLOSES_RE)) {
      const n = Number(m[1]);
      if (!map.has(n)) map.set(n, []);
      if (!map.get(n).includes(pr.number)) map.get(n).push(pr.number);
    }
  }
  return map;
}

export function dependencies(issue) {
  const notes = (issue.body ?? '').split(/^##\s+Notes\b/im)[1] ?? issue.body ?? '';
  const deps = new Set();
  for (const m of notes.matchAll(DEPENDS_RE)) for (const d of m[1].matchAll(/#(\d+)/g)) deps.add(Number(d[1]));
  deps.delete(issue.number);
  return [...deps];
}

const priorityOf = (labels) => {
  const p = PRIORITY.findIndex((x) => labels.includes(x));
  return p < 0 ? PRIORITY.length : p;
};

// Tickets the loop may start, best first. Areas are checked by the
// orchestrator against its registry, since in-flight work is only known there.
export function eligibleTickets({ issues, openPrs, profile }) {
  const byNum = new Map(issues.map((i) => [i.number, i]));
  const linked = linkedIssues(openPrs);
  const blockers = [profile['claim label'], profile['decision label'], profile['hold label']];
  const out = [];
  const skipped = [];
  for (const i of issues) {
    if (i.pull_request || i.state !== 'open') continue;
    const labels = i.labels.map((l) => (typeof l === 'string' ? l : l.name));
    if (!labels.includes(profile['ready label'])) continue;
    const why = [];
    const blocked = blockers.filter((b) => labels.includes(b));
    if (blocked.length) why.push(`carries ${blocked.join(', ')}`);
    if (linked.has(i.number)) why.push(`already closed by open PR #${linked.get(i.number).join(', #')}`);
    const deps = dependencies(i).filter((d) => {
      const dep = byNum.get(d);
      return !dep || dep.state !== 'closed' || dep.state_reason !== 'completed';
    });
    if (deps.length) why.push(`waiting on #${deps.join(', #')}`);
    const areas = labels.filter((l) => l.startsWith(profile['area prefix']));
    const row = { number: i.number, title: i.title, priority: PRIORITY[priorityOf(labels)] ?? '-', areas, labels };
    if (why.length) skipped.push({ ...row, why }); else out.push(row);
  }
  out.sort((a, b) => priorityOf(a.labels) - priorityOf(b.labels) || a.number - b.number);
  return { eligible: out, skipped };
}

// Claims: the account's latest `claimed:` comment on a target. Its
// updated_at is when work was last handed out (claims are re-stamped by
// editing the comment on every dispatch).
export function latestClaim(comments, me) {
  const own = comments.filter((c) => c.user?.login?.toLowerCase() === me.toLowerCase());
  const claims = own.filter((c) => /^claimed:/i.test(firstLine(c.body)));
  const releases = own.filter((c) => /^released:/i.test(firstLine(c.body)));
  const claim = claims.sort((a, b) => (a.created_at < b.created_at ? -1 : 1)).pop();
  const release = releases.sort((a, b) => (a.created_at < b.created_at ? -1 : 1)).pop();
  if (!claim || (release && release.created_at > claim.created_at)) return null;
  return { id: claim.id, created_at: claim.created_at, updated_at: claim.updated_at ?? claim.created_at, url: claim.html_url };
}

// An in-progress target is orphaned by an earlier run when its newest own
// claim predates this run AND is older than the longest stale limit.
export function classifyClaim(claim, { runStart, now, staleMinutes }) {
  if (!claim) return 'not ours';
  const age = (Date.parse(now) - Date.parse(claim.updated_at)) / 60000;
  if (claim.updated_at < runStart && age > staleMinutes) return 'orphaned';
  if (claim.updated_at < runStart) return 'too recent';
  return 'this run';
}
