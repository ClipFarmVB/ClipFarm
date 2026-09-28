#!/usr/bin/env node
// harness — the deterministic half of the loop brief. The orchestrator runs
// `node <plugin>/bin/harness.mjs <command>` from the repo's main checkout and
// acts on the JSON it prints. Nothing here writes to GitHub except `push`,
// `labels` and `claim`/`release`, and each says so in its output.
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadProfile, ProfileError, PROFILE_PATH } from '../lib/profile.mjs';
import { api, apiWithHeaders, ghAll, me, repoSlug, fetchPr, transport, readBody, run, GitHubError } from '../lib/gh.mjs';
import * as W from '../lib/writes.mjs';
import { vendor, check as checkVendored, selfCheck as selfCheckVendored, readManifest as readManifestVendored, VENDOR_DIR } from '../lib/vendor.mjs';
import { prState, route, checksAction, settleBar, firstLine } from '../lib/pr.mjs';
import { linkedIssues, latestClaim, classifyClaim } from '../lib/tickets.mjs';
import { getTracker, TrackerError } from '../lib/trackers/index.mjs';
import { guardedPush, whoMovedHead, isShallow } from '../lib/git.mjs';
import { nowUtc, stamp, toUtc, runStartFrom, TimeError } from '../lib/time.mjs';

const PLUGIN_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

const USAGE = `harness <command> [--root DIR] [--run-start UTC]

  where                     plugin root (the brief lives in <root>/brief)
  profile                   validate and print this repo's profile
  now | stamp               UTC now | a colon-free stamp for paths
  utc DAY HH:MM ZONE        wall-clock time in an IANA zone -> UTC
  pr NUM                    one PR: state, routing row, next action
  prs                       every open PR in scope: one routing line each
  rounds NUM                rounds this run on one PR, and the run budget used
  budget                    rounds this run across every loop PR
  merge-check NUM           the merge test, item by item
  base                      the base branch's check runs: green or not
  tickets                   the tracker's eligible work, in order; why others wait; resumable
  claim ID                  claim a ticket (tracker github: comment + label; skrypt: bootstrap)
  release ID OUTCOME        give it back (github: label off + released:; skrypt: end session)
  synced ID [--cwd DIR]     after a push (skrypt: work:session sync; github: nothing)
  ticket-line ID            the line the PR body must carry
  linked                    issues an open PR already closes
  claims                    claimed targets: this run, too recent, orphaned
  push --branch B --started SHA [--cwd DIR] [--dry-run]
                            guarded push; prints one outcome word
  moved --pr NUM --started SHA [--cwd DIR]
                            did our fixer move the head, or a person?
  labels                    create the profile's labels (writes to GitHub)
  vendor [--into DIR] [--check]   pin this plugin into a repo (for cloud sessions),
                            or check the pinned copy still matches
  whoami                    the login, the transport (gh, or rest via curl), the repo, the root
  rate                      rate-limit headers and token kind (never the credential)
  get PATH [--all]          any REST read (GET only), e.g. a review body by id

 Writes (the only way the brief posts; gh locally, curl in a cloud session):
  comment N --body-file F|-           issue/PR comment, read back
  review N --body-file F|-            a COMMENT review on a PR
  label N add|remove LABEL…           adds to the set (never replaces it)
  pr-body N --body-file F|-           replace a PR's body, read back
  issue N [--body-file F] [--title T] [--close completed|not_planned]
  issue-new --title T --body-file F [--labels a,b]
  open-pr --head B --title T --body-file F [--base B] [--ready]   draft by default
  ready N                             draft -> ready (needs gh: GraphQL)
  merge N --sha FULL                  refuses unless merge-check passes at that head
  rerun RUN                           rerun a workflow run's failed jobs
  failed-log RUN                      failed jobs, failed steps, log tails
`;

function parseArgs(argv) {
  const pos = [];
  const flags = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const k = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) flags[k] = true;
      else { flags[k] = next; i++; }
    } else pos.push(a);
  }
  return { pos, flags };
}

const print = (x) => process.stdout.write(`${typeof x === 'string' ? x : JSON.stringify(x, null, 2)}\n`);
const fail = (msg, code = 1) => { process.stderr.write(`harness: ${msg}\n`); process.exit(code); };

// The repo root whose profile applies. A subagent runs this from its own
// worktree, where a gitignored profile does not exist; every worktree shares
// the main checkout's git dir, so fall back to the main checkout.
function profileRoot(flags) {
  if (flags.root) return flags.root;
  const here = process.cwd();
  if (existsSync(join(here, PROFILE_PATH))) return here;
  try {
    const common = run('git', ['rev-parse', '--path-format=absolute', '--git-common-dir'], { cwd: here }).trim();
    const main = dirname(common);
    if (existsSync(join(main, PROFILE_PATH))) return main;
  } catch { /* not a git checkout */ }
  return here;
}

function context(flags) {
  const root = profileRoot(flags);
  const profile = loadProfile(root);
  const repo = repoSlug(profile);
  let runStart = flags['run-start'] ?? '';
  if (!runStart) {
    const logPath = join(root, profile.log);
    runStart = existsSync(logPath) ? runStartFrom(readFileSync(logPath, 'utf8')) : '';
  } else if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(runStart)) {
    fail(`--run-start must be Z-suffixed UTC, got ${runStart}`);
  }
  return { root, profile, repo, runStart, login: me() };
}

function analyse(ctx, num) {
  const raw = fetchPr(ctx.repo, num);
  const s = prState(raw, { profile: ctx.profile, me: ctx.login, runStart: ctx.runStart });
  const r = route(s, ctx.profile);
  const result = { pr: num, route: r, state: s };
  if (r.action === 'checks' || r.action === 'merge-check') {
    const b = baseChecks(ctx);
    result.checks = checksAction(s, { baseGreen: b.green, preCi: !b.hasWorkflow });
    result.settleBar = settleBar(s, ctx.profile);
  }
  return result;
}

function baseChecks(ctx) {
  const base = ctx.profile.base;
  const runs = ghAll(`repos/${ctx.repo}/commits/${base}/check-runs?per_page=100`);
  let hasWorkflow = true;
  try {
    hasWorkflow = ghAll(`repos/${ctx.repo}/contents/.github/workflows?ref=${base}`).length > 0;
  } catch { hasWorkflow = false; }
  const pending = runs.filter((r) => r.status !== 'completed').map((r) => r.name);
  const failed = runs.filter((r) => r.status === 'completed' && !['success', 'neutral', 'skipped', 'stale'].includes(r.conclusion)).map((r) => `${r.name}: ${r.conclusion}`);
  return { branch: base, hasWorkflow, total: runs.length, pending, failed, green: runs.length > 0 && !pending.length && !failed.length };
}

function openPrs(ctx) {
  return ghAll(`repos/${ctx.repo}/pulls?state=open&per_page=100`);
}

function roundsAcross(ctx) {
  if (!ctx.runStart) fail('no run start: write "run start: <UTC>" to the log first, or pass --run-start');
  const since = encodeURIComponent(ctx.runStart);
  // Every PR touched this run, open or closed; issues?since covers both.
  const touched = ghAll(`repos/${ctx.repo}/issues?state=all&since=${since}&per_page=100`).filter((i) => i.pull_request);
  const per = [];
  for (const t of touched) {
    const comments = ghAll(`repos/${ctx.repo}/issues/${t.number}/comments?since=${since}&per_page=100`);
    const n = comments.filter((c) => c.created_at > ctx.runStart && /^(cold: (findings|clean)|semi-cold: (closes|does not close)) @ ?[0-9a-f]{7}/i.test(firstLine(c.body))).length;
    if (n) per.push({ pr: t.number, rounds: n });
  }
  const used = per.reduce((a, b) => a + b.rounds, 0);
  const budget = ctx.profile['round budget'];
  return { runStart: ctx.runStart, used, budget: budget || 'none', left: budget ? Math.max(0, budget - used) : null, per };
}

function mergeCheck(ctx, num) {
  const a = analyse(ctx, num);
  const s = a.state;
  const b = baseChecks(ctx);
  const items = [
    ['merge policy is auto', ctx.profile['merge policy'] === 'auto'],
    ['base branch is green', b.green || !b.hasWorkflow],
    ['settled at this head', s.settledAtHead],
    ['no hold', !s.hold],
    ['no harness path in the diff', s.harnessFiles.length === 0],
    ['no high-risk path in the diff', s.highRiskFiles.length === 0],
    ['nothing open', s.blocking.length === 0 && s.unansweredHuman.length === 0],
    ['checks pass', s.checks.green || (!b.hasWorkflow && s.checks.total === 0)],
    ['mergeable is true', s.mergeable === true],
  ].map(([item, ok]) => ({ item, ok }));
  const ok = items.every((i) => i.ok);
  return {
    pr: num, ok, head: s.head, items,
    stillYours: ['the clock allows it (read it now)', 'nothing has merged since the base branch\'s checks last completed'],
    then: ok ? [`gh pr ready ${num}`, `gh pr merge ${num} --squash --match-head-commit ${s.head}`, `gh api -X DELETE repos/${ctx.repo}/git/refs/heads/${s.branch}`, 'confirm .merged, and that the linked issue closed'] : [],
    harnessFiles: s.harnessFiles, highRiskFiles: s.highRiskFiles,
  };
}

// One child process per PR, four at a time: each PR is ~7 sequential gh
// calls, and on Windows a gh call costs about two seconds.
async function prsParallel(ctx, flags) {
  const { execFile } = await import('node:child_process');
  const self = fileURLToPath(import.meta.url);
  const numbers = openPrs(ctx).map((p) => p.number);
  const extra = [...(flags.root ? ['--root', flags.root] : []), ...(ctx.runStart ? ['--run-start', ctx.runStart] : [])];
  const one = (n) => new Promise((res) => execFile(process.execPath, [self, 'pr', String(n), ...extra], { maxBuffer: 64 * 1024 * 1024 }, (err, out, errOut) => {
    if (err) return res({ pr: n, error: (errOut || err.message).trim() });
    const a = JSON.parse(out);
    res(a.state.inScope ? { pr: n, head: a.state.head7, row: a.route.row, action: a.route.action, why: a.route.why, rounds: a.state.roundsThisRun, checks: a.checks?.action } : null);
  }));
  const results = [];
  for (let i = 0; i < numbers.length; i += 4) results.push(...await Promise.all(numbers.slice(i, i + 4).map(one)));
  return { runStart: ctx.runStart || null, prs: results.filter(Boolean) };
}

// Pin a copy of this plugin into a repository (for cloud sessions), or check
// that the pinned copy still matches. Needs no profile and no GitHub.
function vendorCmd(flags) {
  const into = typeof flags.into === 'string' ? flags.into : run('git', ['rev-parse', '--show-toplevel']).trim();
  // Run from a vendored copy: only the self-check makes sense.
  if (existsSync(join(PLUGIN_ROOT, 'VENDORED.json'))) {
    if (!flags.check) fail('this is a vendored copy; run vendor from the harness plugin (node <plugin>/bin/harness.mjs vendor)');
    const r = selfCheckVendored(join(PLUGIN_ROOT, '..', '..', '..'));
    if (!r.ok) { print(r); process.exit(1); }
    return r;
  }
  const version = JSON.parse(readFileSync(join(PLUGIN_ROOT, '.claude-plugin', 'plugin.json'), 'utf8')).version;
  if (flags.check) {
    const r = checkVendored(PLUGIN_ROOT, into, { version });
    if (!r.ok) { print(r); process.exit(1); }
    return r;
  }
  let source = 'unknown';
  try { source = run('git', ['rev-parse', 'HEAD'], { cwd: PLUGIN_ROOT }).trim(); } catch { /* an installed cache is not a git checkout */ }
  const r = vendor(PLUGIN_ROOT, into, { version, source });
  // A path .gitignore hides would never reach a cloud clone. Check every file
  // written (a `build/` rule once hid a whole brief directory), plus the profile.
  const written = [...Object.keys(readManifestVendored(into).files), `${VENDOR_DIR}/VENDORED.json`, PROFILE_PATH];
  let ignored = [];
  try {
    ignored = run('git', ['check-ignore', '--stdin'], { cwd: into, input: written.join('\n') }).split('\n').map((s) => s.trim()).filter(Boolean);
  } catch (e) {
    if (e.status !== 1) throw e; // 1 = nothing ignored
  }
  const noProfile = !existsSync(join(into, PROFILE_PATH));
  return {
    ...r, into, ignored,
    ...(ignored.length ? { fix: `.gitignore hides ${ignored.length} file(s), e.g. ${ignored.slice(0, 3).join(', ')}. Un-ignore .claude/harness/ and .claude/skills/harness-*/ (keep .claude/harness/log.md and status.md ignored), or they never reach a cloud clone.` } : {}),
    ...(noProfile ? { profile: `no ${PROFILE_PATH}: a cloud clone needs the profile committed too` } : {}),
  };
}

function labels(ctx) {
  if (ctx.profile.labels !== 'yes') fail('labels: no in the profile: this repo keeps state in record comments, nothing to create');
  const p = ctx.profile;
  const want = [
    [p['loop label'], '5319e7', 'a PR the harness loop may review, fix and merge'],
    [p['ready label'], '0e8a16', 'a ticket the loop may build unattended'],
    [p['claim label'], 'fbca04', 'claimed by a running loop'],
    [p['hold label'], 'b60205', 'the human\'s veto: the loop leaves it alone'],
    [p['decision label'], 'd93f0b', 'waiting on a human decision'],
    [p['settled label'], '0e8a16', 'review settled at the recorded head'],
    [p['unsettled label'], 'e99695', 'the loop cannot close what is open; see the unsettled: comment'],
    ['P0', 'b60205', 'The base is red or the primary path is broken'],
    ['P1', 'd93f0b', 'Primary-path work that does not work yet'],
    ['P2', 'fbca04', 'Depth, realism, edge states, docs'],
    ['P3', 'c2e0c6', 'Nobody will notice soon'],
    ['feat', '1d76db', 'New capability'], ['bug', 'd73a4a', 'Something is broken'],
    ['ui', '5319e7', 'Visible polish or design work'], ['chore', 'bfdadc', 'Tooling, CI, housekeeping'],
    ['docs', '0075ca', 'Documentation'],
    [`${p['area prefix']}docs`, 'c5def5', 'Docs; exempt from area exclusivity'],
  ];
  if (p.status === 'issue') want.push(['status', '000000', 'The harness loop\'s status issue']);
  if (p['pr scope'] !== 'loop-label') want.shift();
  const hackathon = p.pack === 'hackathon';
  if (hackathon) want.push(['submission', '0e8a16', 'Submission materials']);
  const made = [];
  for (const [name, color, description] of want) {
    made.push(`${name} (${W.ensureLabel(ctx.repo, name, color, description)})`);
  }
  // The hackathon pack's milestones. state=all, so closed ones are not re-created.
  const milestones = hackathon ? W.ensureMilestones(ctx.repo, [
    ['M0 Skeleton', 'Scaffold, CI gate, tokens, app shell, fakes, smoke test'],
    ['M1 Demo path', 'Every demo beat works end to end on fakes'],
    ['M2 Wow', 'The hard part works for real, with fallbacks'],
    ['M3 Prizes', 'Every targeted prize requirement visibly met'],
    ['M4 Polish', 'Every demo screen passes the ui-craft rubric'],
    ['M5 Submission', 'README, demo script, write-up, video plan, gallery'],
  ]) : [];
  return { wrote: 'labels (created or updated) and missing milestones', labels: made, milestonesCreated: milestones };
}

function claims(ctx) {
  const p = ctx.profile;
  const targets = ghAll(`repos/${ctx.repo}/issues?state=open&labels=${encodeURIComponent(p['claim label'])}&per_page=100`);
  const now = nowUtc();
  return targets.map((t) => {
    const c = latestClaim(ghAll(`repos/${ctx.repo}/issues/${t.number}/comments?per_page=100`), ctx.login);
    return {
      number: t.number, isPr: !!t.pull_request, title: t.title, claim: c,
      status: ctx.runStart ? classifyClaim(c, { runStart: ctx.runStart, now, staleMinutes: 120 }) : (c ? 'claimed' : 'not ours'),
    };
  });
}

function main() {
  const { pos, flags } = parseArgs(process.argv.slice(2));
  const [cmd, ...rest] = pos;
  try {
    switch (cmd) {
      case undefined: case 'help': case '-h': return print(USAGE);
      case 'where': return print(PLUGIN_ROOT.replace(/\\/g, '/'));
      case 'now': return print(nowUtc());
      case 'stamp': return print(stamp());
      case 'utc': return print(toUtc(...rest));
      case 'profile': return print(loadProfile(profileRoot(flags)));
      case 'vendor': return print(vendorCmd(flags));
    }
    // Reads that init needs before a profile exists: the repo comes from origin.
    const READ_ONLY = ['get', 'whoami', 'rate'];
    const root = profileRoot(flags);
    const ctx = READ_ONLY.includes(cmd) && !existsSync(join(root, PROFILE_PATH))
      ? { root, profile: null, repo: typeof flags.repo === 'string' ? flags.repo : repoSlug(null, root), runStart: '', login: me() }
      : context(flags);
    const runId = () => {
      if (!/^\d+$/.test(rest[0] ?? '')) fail(`${cmd} needs a workflow run id`);
      return rest[0];
    };
    const ticketId = () => {
      if (!rest[0]) fail(`${cmd} needs a ticket id (#N, or SK-N for tracker skrypt)`);
      return rest[0];
    };
    const num = () => {
      const n = Number(rest[0] ?? flags.pr);
      if (!Number.isInteger(n) || n < 1) fail(`${cmd} needs a PR number`);
      return n;
    };
    switch (cmd) {
      case 'pr': return print(analyse(ctx, num()));
      case 'prs': return prsParallel(ctx, flags).then(print);
      case 'rounds': {
        const a = analyse(ctx, num());
        return print({ pr: a.pr, roundsThisRun: a.state.roundsThisRun, cap: ctx.profile['round cap'], nextRoundK: a.state.nextRoundK, budget: ctx.runStart ? roundsAcross(ctx) : 'no run start' });
      }
      case 'budget': return print(roundsAcross(ctx));
      case 'merge-check': return print(mergeCheck(ctx, num()));
      case 'base': return print(baseChecks(ctx));
      case 'tickets': return print({ tracker: ctx.profile.tracker, ...getTracker(ctx).tickets() });
      case 'claim': return print(getTracker(ctx).claim(ticketId()));
      case 'release': {
        const outcome = rest.slice(1).join(' ') || flags.outcome;
        if (!outcome || outcome === true) fail('release needs an outcome: harness release ID OUTCOME…');
        return print(getTracker(ctx).release(ticketId(), outcome));
      }
      case 'synced': return print(getTracker(ctx).synced(ticketId(), { cwd: flags.cwd }));
      case 'ticket-line': return print(getTracker(ctx).ticketLine(ticketId()));
      case 'linked': return print(Object.fromEntries(linkedIssues(openPrs(ctx))));
      case 'claims': return print(claims(ctx));
      case 'labels': return print(labels(ctx));
      // Writes. The only way the brief posts to GitHub, so they work the same
      // with gh (local) and without it (a cloud session).
      case 'comment': return print(W.comment(ctx.repo, num(), readBody(flags['body-file'])));
      case 'review': return print(W.review(ctx.repo, num(), readBody(flags['body-file'])));
      case 'label': {
        const [n, op, ...names] = rest;
        if (!/^\d+$/.test(n ?? '') || !['add', 'remove'].includes(op) || !names.length) fail('label N add|remove LABEL…');
        return print(op === 'add' ? W.addLabels(ctx.repo, Number(n), names) : names.map((l) => W.removeLabel(ctx.repo, Number(n), l)));
      }
      case 'pr-body': return print(W.editPrBody(ctx.repo, num(), readBody(flags['body-file'])));
      case 'issue': {
        const n = num();
        const close = flags.close && flags.close !== true ? flags.close : null;
        if (close && !['completed', 'not_planned'].includes(close)) fail('--close completed|not_planned');
        return print(W.editIssue(ctx.repo, n, {
          body: flags['body-file'] ? readBody(flags['body-file']) : undefined,
          title: typeof flags.title === 'string' ? flags.title : undefined,
          state: close ? 'closed' : undefined, stateReason: close ?? undefined,
        }));
      }
      case 'issue-new': {
        if (typeof flags.title !== 'string') fail('issue-new --title T --body-file F [--labels a,b]');
        const labels = typeof flags.labels === 'string' ? flags.labels.split(',').map((s) => s.trim()).filter(Boolean) : [];
        return print(W.createIssue(ctx.repo, { title: flags.title, body: readBody(flags['body-file']), labels }));
      }
      case 'open-pr': {
        if (typeof flags.head !== 'string' || typeof flags.title !== 'string') fail('open-pr --head BRANCH --title T --body-file F [--base B] [--ready]');
        return print(W.openPr(ctx.repo, { title: flags.title, head: flags.head, base: typeof flags.base === 'string' ? flags.base : ctx.profile.base, body: readBody(flags['body-file']), draft: !flags.ready }));
      }
      case 'ready': return print(W.markReady(ctx.repo, num()));
      case 'merge': {
        const n = num();
        const check = mergeCheck(ctx, n);
        if (!check.ok) { print({ refused: 'the merge test fails', items: check.items.filter((i) => !i.ok) }); return process.exit(3); }
        if (flags.sha !== check.head) fail(`--sha must be the head the merge test just read (${check.head})`);
        return print({ ...W.mergePr(ctx.repo, n, check.head), stillYours: check.stillYours });
      }
      case 'rerun': return print(W.rerunFailed(ctx.repo, runId()));
      case 'failed-log': return print(W.failedLog(ctx.repo, runId()));
      case 'whoami': return print({ login: ctx.login, transport: transport(), repo: ctx.repo, root: ctx.root });
      case 'rate': {
        const { headers } = apiWithHeaders(`repos/${ctx.repo}`);
        const pick = (k) => headers[k] ?? null;
        return print({ limit: pick('x-ratelimit-limit'), remaining: pick('x-ratelimit-remaining'), used: pick('x-ratelimit-used'), resource: pick('x-ratelimit-resource'), reset: pick('x-ratelimit-reset'), oauthScopes: pick('x-oauth-scopes'), tokenKind: headers['x-oauth-scopes'] ? 'user token' : 'app or proxy token (no scopes header)' });
      }
      case 'get': {
        const path = rest[0];
        if (!path || /^https?:/.test(path)) fail('get PATH (a REST path such as repos/o/r/pulls/5/reviews/123; GET only)');
        return print(flags.all ? ghAll(path) : api('GET', path));
      }
      case 'push': {
        if (!flags.branch || !flags.started) fail('push needs --branch and --started');
        const r = guardedPush({ branch: flags.branch, started: flags.started, cwd: flags.cwd ?? process.cwd(), dryRun: !!flags['dry-run'] });
        print(r);
        return process.exit(r.outcome === 'pushed' || r.outcome === 'would push' ? 0 : 3);
      }
      case 'moved': {
        if (!flags.pr || !flags.started) fail('moved needs --pr and --started');
        const cwd = flags.cwd ?? process.cwd();
        if (isShallow(cwd)) fail('shallow clone: run git fetch --unshallow first; ancestry checks lie in a shallow clone');
        return print(whoMovedHead({ pr: flags.pr, started: flags.started, cwd }));
      }
      default: fail(`unknown command "${cmd}"\n\n${USAGE}`, 2);
    }
  } catch (e) {
    if (e instanceof ProfileError || e instanceof TimeError || e instanceof TrackerError) fail(e.message, 2);
    if (e instanceof GitHubError && /^rate-limited/.test(e.message)) fail(e.message, 4);
    const detail = `${e.stderr ?? ''}`.trim() || e.message;
    if (/rate limit|secondary rate|HTTP 429|retry-after/i.test(detail)) fail(`rate-limited: ${detail}`, 4);
    fail(detail);
  }
}

main();
