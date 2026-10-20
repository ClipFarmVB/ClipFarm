// A PR's review state, derived only from what is on GitHub, and the routing
// table that turns that state into the next action. Pure functions: raw
// GitHub JSON in, plain objects out. brief/loop/REVIEW.md is the prose
// definition; this file is its executable form, and the two must agree.
import { matchesAny } from './profile.mjs';

export const ROUND_RE = /^(cold: (findings|clean)|semi-cold: (closes|does not close)) @ ?([0-9a-f]{7})\b/i;
const FIX_RE = /^fix: (pushed|no push|hold requested) @ ?([0-9a-f]{7})\b/i;
const DISPATCH_RE = /^loop: fixer dispatched @ ?([0-9a-f]{7})\s*[—–-]+\s*(findings|human|check|conflict)\b/i;
const REOPENED_RE = /^reopened: ?([0-9a-f]{7})\b(?:\s*[—–-]+\s*(.*))?$/i;
// Finding IDs from before R<k>- numbering ("**M1.**", "- C2:"). Their
// verdicts were free prose, so they cannot be tracked, only re-baselined.
const LEGACY_ID_RE = /^\s*(?:[-*#]+\s*)?\*{0,2}([CM])\d+\b[.:,)]/m;
const SETTLED_RE = /^settled: @ ?([0-9a-f]{7})\b/i;
const UNSETTLED_RE = /^unsettled: (.+?) @ ?([0-9a-f]{7})\b/i;
const ANSWERED_RE = /^(fix:|loop: answered\b)/i;
const ID_RE = /\bR(\d+)-([CMN])(\d+)\b/g;
const VERDICT_RE = /^\s*(?:[-*]\s*)?`?(R\d+-[CMN]\d+)`?\s*:\s*(closed|open, needs a decision|open|withdrawn)\b/i;
const HOLD_ON_RE = /^hold\b/i;
const HOLD_OFF_RE = /^(unhold|release hold|resume)\b/i;
const PASSING = ['success', 'neutral', 'skipped', 'stale'];

export const MACHINE_PREFIXES = [
  'cold:', 'semi-cold:', 'fix:', 'checks:', 'settled:', 'unsettled:', 'reopened:',
  'claimed:', 'released:', 'loop:', 'ship:',
];
// Precedence when more than one applies: the first wins.
export const UNSETTLED_REASONS = ['needs a decision', 'latched', 'head moved', 'ran out of rounds'];

export const firstLine = (body) => (body ?? '').replace(/\r/g, '').split('\n')[0].trim();
export const isMachine = (line) => MACHINE_PREFIXES.some((p) => line.toLowerCase().startsWith(p));
const at = (x) => x.created_at ?? x.submitted_at ?? '';
const byTime = (a, b) => (at(a) < at(b) ? -1 : at(a) > at(b) ? 1 : 0);
const lc = (s) => (s ?? '').toLowerCase();
const last = (arr) => arr[arr.length - 1];
const maxTime = (...ts) => ts.filter(Boolean).sort().pop() ?? '';

export function prState(raw, { profile, me, runStart = '' }) {
  const { pr } = raw;
  const H = lc(pr.head.sha);
  const H7 = H.slice(0, 7);
  const comments = [...raw.comments].sort(byTime);
  const reviews = raw.reviews.filter((r) => r.submitted_at).sort(byTime);
  const labels = pr.labels.map((l) => l.name);
  const withLine = (c) => ({ ...c, line: firstLine(c.body) });
  const lined = comments.map(withLine);
  const matching = (re) => lined.filter((c) => re.test(c.line));

  // Round markers are comments; the findings live in the matching review.
  const markers = lined.flatMap((c) => {
    const m = ROUND_RE.exec(c.line);
    if (!m) return [];
    const cold = m[1].toLowerCase().startsWith('cold');
    return [{ at: at(c), line: c.line, kind: cold ? 'cold' : 'semi-cold', verdict: (m[2] ?? m[3]).toLowerCase(), sha: lc(m[4]) }];
  });
  const L = last(markers) ?? null;
  const roundReviews = reviews.map(withLine).filter((r) => ROUND_RE.test(r.line));

  // An ID R<k>-X<n> is raised by the k-th round review. Semi-cold reviews give
  // verdicts; the latest verdict for an ID wins.
  const raised = new Map();
  const verdicts = new Map();
  roundReviews.forEach((r, i) => {
    const k = i + 1;
    const semi = /^semi-cold:/i.test(r.line);
    for (const text of (r.body ?? '').replace(/\r/g, '').split('\n')) {
      const v = semi && VERDICT_RE.exec(text);
      if (v) { verdicts.set(v[1].toUpperCase(), v[2].toLowerCase()); continue; }
      for (const m of text.matchAll(ID_RE)) {
        const id = `R${m[1]}-${m[2].toUpperCase()}${m[3]}`;
        if (Number(m[1]) === k && !raised.has(id)) raised.set(id, { id, tier: m[2].toUpperCase(), text: text.trim() });
      }
    }
  });
  const isOpen = (f) => !['closed', 'withdrawn'].includes(verdicts.get(f.id));
  const openCriticals = [...raised.values()].filter((f) => f.tier === 'C' && isOpen(f));
  const openMediums = [...raised.values()].filter((f) => f.tier === 'M' && isOpen(f));
  const blocking = profile['mediums block'] === 'yes' ? [...openCriticals, ...openMediums] : openCriticals;
  const latestReview = L ? last(roundReviews.filter((r) => r.line.toLowerCase() === L.line.toLowerCase())) : null;
  // Only the latest round decides: once a fresh round has re-baselined the PR
  // (with R<k>- IDs, or clean), older legacy rounds no longer matter.
  const latestBody = (latestReview?.body ?? '').replace(/\r/g, '');
  const legacyIds = !!latestReview && LEGACY_ID_RE.test(latestBody) && !/\bR\d+-[CMN]\d+\b/.test(latestBody);
  const latestK = latestReview ? roundReviews.indexOf(latestReview) + 1 : 0;
  const latestRaisedCM = [...raised.values()].filter((f) => f.id.startsWith(`R${latestK}-`) && f.tier !== 'N');

  // Record comments.
  const latestReopen = last(matching(REOPENED_RE)) ?? null;
  const reopenAt = latestReopen ? at(latestReopen) : '';
  const latestFix = last(matching(FIX_RE)) ?? null;
  const latestFixAt = latestFix ? at(latestFix) : '';
  const dispatches = lined.flatMap((c) => {
    const m = DISPATCH_RE.exec(c.line);
    return m ? [{ at: at(c), sha: lc(m[1]), job: m[2].toLowerCase() }] : [];
  });
  const floor = maxTime(L?.at, reopenAt);
  const hasAtHead = (prefix) => lined.some((c) => c.line.toLowerCase().startsWith(prefix) && new RegExp(`@ ?${H7}\\b`, 'i').test(c.line));

  // Human input: this account, a user (not a bot), no machine prefix.
  const humanItems = [
    ...lined.map((c) => ({ ...c, where: 'comment' })),
    ...reviews.map(withLine).filter((r) => r.body && !ROUND_RE.test(r.line)).map((r) => ({ ...r, where: 'review' })),
    ...raw.reviewComments.map(withLine).map((c) => ({ ...c, where: 'inline' })),
  ].filter((c) => c.user?.type === 'User' && lc(c.user?.login) === lc(me) && c.line && !isMachine(c.line)).sort(byTime);
  const holdCommands = humanItems.filter((c) => HOLD_ON_RE.test(c.line) || HOLD_OFF_RE.test(c.line));
  const answeredAt = at(last(matching(ANSWERED_RE)) ?? {});
  const unanswered = humanItems.filter((c) => !holdCommands.includes(c) && at(c) > answeredAt);

  // Terminal state: labels, or (labels: no) the latest record comment.
  const useLabels = profile.labels === 'yes';
  const recs = lined.flatMap((c) => {
    let m = SETTLED_RE.exec(c.line);
    if (m) return [{ at: at(c), kind: 'settled', sha: lc(m[1]) }];
    m = UNSETTLED_RE.exec(c.line);
    return m ? [{ at: at(c), kind: 'unsettled', reason: m[1].trim().toLowerCase(), sha: lc(m[2]) }] : [];
  });
  const record = last(recs) ?? null;
  const recordLive = !!record && !(reopenAt > record.at);
  let settled = false, unsettled = false, humanRemoved = null, staleLabel = null;
  if (useLabels) {
    const hasS = labels.includes(profile['settled label']);
    const hasU = labels.includes(profile['unsettled label']);
    if (recordLive) {
      settled = hasS; unsettled = hasU;
      if (!hasS && !hasU) humanRemoved = record.kind === 'settled' ? profile['settled label'] : `${profile['unsettled label']}: ${record.reason}`;
    } else if (hasS || hasU) {
      staleLabel = hasS ? profile['settled label'] : profile['unsettled label'];
    }
  } else if (recordLive) {
    if (record.kind === 'settled') settled = true;
    else if (unanswered.some((c) => at(c) > record.at)) humanRemoved = `unsettled: ${record.reason}`;
    else unsettled = true;
  }
  const hold = useLabels ? labels.includes(profile['hold label']) : HOLD_ON_RE.test(last(holdCommands)?.line ?? '');

  const settledAtHead = settled && record?.kind === 'settled' && record.sha === H7;
  const harnessFiles = raw.files.filter((f) => matchesAny(f, profile['all harness paths']));
  const highRiskFiles = raw.files.filter((f) => matchesAny(f, profile['high risk']));
  const waitsForHuman = profile['merge policy'] !== 'auto' || harnessFiles.length > 0 || highRiskFiles.length > 0;

  const runs = raw.checkRuns ?? [];
  const pending = runs.filter((r) => r.status !== 'completed');
  const failed = runs.filter((r) => r.status === 'completed' && !PASSING.includes(r.conclusion));
  const soft = runs.filter((r) => ['neutral', 'skipped', 'stale'].includes(r.conclusion));
  const since = maxTime(runStart, reopenAt);

  const scope = profile['pr scope'];
  const author = lc(pr.user?.login);
  const inScope = scope === 'loop-label' ? labels.includes(profile['loop label'])
    : scope === 'loop-body' ? author === lc(me) && (pr.body ?? '').includes(profile['loop line'])
    : author === lc(me);

  // Ship verdicts at this head: `ship: REVIEWER SHIP|NO-SHIP @ SHA7`.
  const shipsAtHead = {};
  for (const c of lined) {
    const m = /^ship: (\w+) (SHIP|NO-SHIP) @ ?([0-9a-f]{7})\b/i.exec(c.line);
    if (m && lc(m[3]) === H7) shipsAtHead[lc(m[1])] = m[2].toUpperCase();
  }

  // A PR that never had a finding, and the clean cold rounds at this head.
  const everFound = [...raised.values()].some((f) => f.tier !== 'N');
  let cleanAtHead = 0;
  for (let i = markers.length - 1; i >= 0 && markers[i].kind === 'cold' && markers[i].verdict === 'clean' && markers[i].sha === H7; i--) cleanAtHead++;

  return {
    number: pr.number, state: pr.merged ? 'merged' : pr.state, draft: !!pr.draft, branch: pr.head.ref,
    head: H, head7: H7, inScope, labels, mergeable: pr.mergeable ?? null,
    latest: L, latestReviewId: latestReview?.id ?? null,
    latestReviewMissing: !!L && !latestReview,
    malformedLatest: !!L && L.kind === 'cold' && L.verdict === 'findings' && !!latestReview && latestRaisedCM.length === 0,
    // k counts round *reviews*, because IDs are matched to reviews by
    // position; a marker whose review never landed must not shift it.
    nextRoundK: roundReviews.length + 1, legacyIds,
    roundsThisRun: markers.filter((m) => m.at > since).length,
    openCriticals, openMediums, blocking, everFound, cleanAtHead, shipsAtHead,
    openVerdicts: Object.fromEntries(blocking.map((f) => [f.id, verdicts.get(f.id) ?? 'unjudged'])),
    fixNewerThanL: !!latestFix && (!L || latestFixAt > L.at), latestFixAt,
    findingFixersSinceL: dispatches.filter((d) => d.job === 'findings' && d.at > floor).length,
    checkFixerAtHead: dispatches.some((d) => d.job === 'check' && d.sha === H7),
    rerunAtHead: hasAtHead('checks: rerun'), noneAtHead: hasAtHead('checks: none'),
    knownLimitationsAtHead: hasAtHead('loop: known limitations'),
    latestReopen: latestReopen ? { at: reopenAt, line: latestReopen.line } : null,
    unansweredHuman: unanswered.map((c) => ({ where: c.where, at: at(c), url: c.html_url, line: c.line })),
    hold, settled, unsettled, settledAtHead, humanRemoved, staleLabel, record,
    needsReopen: recordLive && (settled || unsettled) && record.sha !== H7,
    parked: unsettled || (settledAtHead && waitsForHuman),
    waitsForHuman, harnessFiles, highRiskFiles,
    checks: {
      total: runs.length, pending: pending.map((r) => r.name),
      failed: failed.map((r) => `${r.name}: ${r.conclusion}`), soft: soft.map((r) => `${r.name}: ${r.conclusion}`),
      green: runs.length > 0 && !pending.length && !failed.length,
    },
  };
}

// The routing table (brief/loop/REVIEW.md "Routing"). Rows are tested in
// order; the first that matches decides. Returns the one next action.
export function route(s, profile) {
  const cap = profile['round cap'];
  const settleRounds = profile['settle rounds'];
  const out = (row, action, why) => ({ row, action, why });
  if (s.state !== 'open') return out('-', 'none', `PR is ${s.state}`);
  if (!s.inScope) return out('-', 'none', 'not a loop PR under this profile\'s pr scope');
  if (s.hold) return out('-', 'none', 'hold: the human\'s veto');
  if (s.staleLabel) return out('pre', 'remove-label', `"${s.staleLabel}" is left over from before the latest reopened: marker: remove it, route again`);
  if (s.needsReopen) return out('pre', 'reopen', `head ${s.head7} differs from the ${s.record.kind} record @ ${s.record.sha}: post "reopened: ${s.head7} — commits", clear the state, route again`);
  if (s.humanRemoved) return out('pre', 'post-reopened-human', `post "reopened: ${s.head7} — human, was ${s.humanRemoved}", then route again`);
  if (s.parked) return out('-', 'none', s.unsettled ? `parked: unsettled (${s.record?.reason})` : 'parked: settled, waiting for a human merge');

  const L = s.latest;
  const H = s.head7;
  const open = s.blocking.length > 0;
  // The settling exception: a PR with nothing open may take the rounds that
  // settle it past the ceiling. Any new finding ends it.
  const allowance = (settling) => cap + (settling && !open ? settleRounds : 0);
  const round = (row, kind, why, settling = false) => (s.roundsThisRun < allowance(settling)
    ? out(row, kind, why)
    : out(row, 'unsettle: ran out of rounds', `round ceiling ${cap} reached (${s.roundsThisRun} this run); push what can be fixed first`));

  if (s.settledAtHead) {
    if (s.unansweredHuman.length) return out(0, 'fix: human', 'settled, but a human comment is unanswered');
    if (s.mergeable === false) return out(0, 'fix: conflict', 'settled, but not mergeable: merge the base in, never rebase');
    return out(0, 'merge-check', 'settled at head: run the merge test');
  }

  // Row 1: a human cleared a terminal state, and nothing has acted on it yet.
  const rp = s.latestReopen;
  if (rp && /human, was/i.test(rp.line) && (!L || rp.at > L.at) && rp.at > s.latestFixAt) {
    const was = rp.line.replace(/^.*human, was\s*/i, '').toLowerCase();
    if (was.includes('needs a decision')) return out(1, 'fix: human', 'the human answered a decision: hand the fixer every human comment since the record');
    if (was.includes('latched')) return out(1, 'fix: latched-retry', 'retry the pinned push once; if it is refused again, re-apply latched');
    if (was.includes('settled')) return round(1, 'cold', 'the human removed the settled state and wants another look');
    // ran out of rounds / head moved / hold: route normally with counts restarted.
  }

  if (s.legacyIds) return round('legacy', 'cold', 'the latest round\'s finding IDs predate R<k>- numbering and cannot be tracked: a fresh cold round re-baselines this PR');
  if (s.latestReviewMissing) return round('void', 'cold', 'the latest marker has no matching review: the round is void');
  if (s.malformedLatest) return round('void', 'cold', 'a cold: findings review with no parseable Critical or Medium IDs is malformed');
  if (!L) return round(2, 'cold', 'no round yet');

  if (open) {
    if (H !== L.sha) return round(3, 'semi-cold', 'code landed since the latest round');
    const allNeedDecision = Object.values(s.openVerdicts).every((v) => v === 'open, needs a decision');
    if (L.kind === 'semi-cold' && !s.fixNewerThanL && allNeedDecision) return out(4, 'unsettle: needs a decision', 'a semi-cold round agreed every open finding needs a human');
    if (s.fixNewerThanL) return round(5, 'semi-cold', 'judge the fixer\'s rejections, body edits and declines');
    if (s.findingFixersSinceL < 2) return out(6, 'fix: findings', `${s.blocking.length} open: ${s.blocking.map((f) => f.id).join(', ')}`);
    return out(7, 'unsettle: ran out of rounds', 'two fixers since the latest round and no fix reply');
  }
  if (L.kind === 'semi-cold' && L.verdict === 'closes') return round(8, 'cold', 'the settling round', true);
  if (L.kind === 'cold') {
    if (H !== L.sha) return round(9, 'cold', 'new code nobody has read');
    if (s.unansweredHuman.length) return out(10, 'fix: human', 'the fixer answers the human');
    if (settleRounds === 2 && !s.everFound && s.cleanAtHead < 2) return round(11, 'cold', 'a PR that never had a finding needs two clean cold rounds at its head', true);
    return out(11, 'checks', 'nothing open at a cold round on this head: read the checks, then the settle bar');
  }
  return round(12, 'cold', 'anything else');
}

// The checks section of REVIEW.md, for row 11.
export function checksAction(s, { baseGreen, preCi }) {
  if (preCi && s.checks.total === 0) return { action: 'settle-bar', why: 'pre-CI exception: no workflow on the base branch yet' };
  if (!baseGreen) return { action: 'wait', why: 'the base branch is red or has no finished run; a PR cannot pass while it fails' };
  if (s.checks.total === 0) {
    if (!s.noneAtHead) return { action: 'post: checks: none', why: `post "checks: none @ ${s.head7}"; a later lap acts if there are still none` };
    if (!s.checkFixerAtHead) return { action: 'fix: check', why: 'still no check runs on the head: find out why CI did not run' };
    return { action: 'unsettle: ran out of rounds', why: 'no check runs, and a check fixer already looked' };
  }
  if (s.checks.pending.length) return { action: 'wait', why: `pending: ${s.checks.pending.join(', ')}` };
  if (s.checks.green) return { action: 'settle-bar', why: 'every run completed and passed' };
  if (!s.rerunAtHead && !s.checkFixerAtHead) return { action: 'rerun-or-fix', why: `failed: ${s.checks.failed.join(', ')}. If the latest review says the diff did not cause it: gh run rerun RUN_ID --failed, then post "checks: rerun @ ${s.head7}". Otherwise dispatch a check fixer.` };
  if (!s.checkFixerAtHead) return { action: 'fix: check', why: `failed: ${s.checks.failed.join(', ')}` };
  return { action: 'unsettle: ran out of rounds', why: 'one check fixer per head is the cap' };
}

// The settle bar (brief/loop/FIX.md). Every item must hold at the head.
export function settleBar(s, profile) {
  const fails = [];
  if (!s.latest || s.latest.kind !== 'cold' || s.latest.sha !== s.head7) fails.push('the latest round is not a cold round at this head');
  if (s.blocking.length) fails.push(`open: ${s.blocking.map((f) => f.id).join(', ')}`);
  if (profile['settle rounds'] === 2 && !s.everFound && s.cleanAtHead < 2) fails.push('a PR that never had a finding needs two clean cold rounds at its head');
  if (s.unansweredHuman.length) fails.push('a human comment is unanswered');
  if (!s.checks.green) fails.push('the checks are not all green');
  const mediums = profile['mediums block'] === 'yes' ? [] : s.openMediums.map((f) => f.id);
  if (mediums.length && !s.knownLimitationsAtHead) fails.push(`post "loop: known limitations @ ${s.head7}" listing ${mediums.join(', ')} first`);
  // Ship reviews come last: they judge a head that has met everything else.
  const shipNeeded = profile['ship reviewers'].filter((r) => s.shipsAtHead[r] !== 'SHIP');
  const otherFails = fails.length;
  if (shipNeeded.length) fails.push(`ship review needed at ${s.head7}: ${shipNeeded.join(', ')}`);
  return { ok: fails.length === 0, fails, mediums, shipNeeded: otherFails ? [] : shipNeeded };
}
