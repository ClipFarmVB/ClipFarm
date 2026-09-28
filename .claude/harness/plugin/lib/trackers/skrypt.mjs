// Tracker: Skrypt work sessions. Every operation goes through the repo's own
// CLI (`pnpm work:session …`), never its API or database: the CLI holds the
// token, and there is exactly one implementation of the lifecycle. The loop
// never reorders the engine's plan and never closes a task.
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fillTicketLine } from './common.mjs';

const taskNumber = (id) => {
  const m = /^(?:SK-)?(\d+)$/i.exec(String(id).trim());
  if (!m) throw new Error(`a Skrypt ticket id is SK-<n>, got "${id}"`);
  return Number(m[1]);
};

// `plan` prints text today and ignores unknown flags; once it grows `--json`
// the adapter uses that. Anything else is parsed from the text, and a shape
// it does not recognise is an error, never an empty plan.
export function parsePlanText(text) {
  const lines = text.replace(/\r/g, '').split('\n');
  const section = (name) => {
    const start = lines.indexOf(name);
    if (start < 0) return null;
    const out = [];
    for (let i = start + 1; i < lines.length && lines[i].startsWith('  '); i++) out.push(lines[i]);
    return out;
  };
  const doFirst = section('Do these first');
  const inProgress = section('In progress');
  const sessions = section('Active sessions');
  if (!doFirst || !inProgress || !sessions) throw new Error('unrecognised work:session plan output (no "Do these first" / "In progress" / "Active sessions" sections)');
  const task = (line) => {
    const m = /^ {2}SK-(\d+)\s{2,}(.*?)(?: {2}— (.*))?$/.exec(line);
    return m ? { taskNumber: Number(m[1]), title: m[2].trim(), reason: m[3]?.trim() } : null;
  };
  const session = (line) => {
    const m = /^ {2}SK-(\d+)\s{2,}(\S+)\s{2}(\S+)\s{2}@([0-9a-f]{7})\s{2}rev (\d+)/.exec(line);
    return m ? { taskNumber: Number(m[1]), repository: m[2], branch: m[3], head7: m[4], revision: Number(m[5]) } : null;
  };
  const queued = /\(\+(\d+) queued\)/.exec(doFirst.join('\n'));
  return {
    briefing: { items: doFirst.map(task).filter(Boolean), queuedCount: queued ? Number(queued[1]) : 0 },
    inProgress: inProgress.map(task).filter(Boolean),
    activeSessions: sessions.map(session).filter(Boolean),
    source: 'text',
  };
}

export function parseBootstrapText(text) {
  const field = (k) => new RegExp(`^ {2}${k}:\\s+(.+)$`, 'm').exec(text.replace(/\r/g, ''))?.[1]?.trim();
  const branch = field('branch');
  const worktree = field('worktree');
  const task = field('task');
  if (!branch || !worktree) throw new Error('unrecognised work:session bootstrap output (no branch / worktree lines)');
  return { branch, worktree, task, mutation: /^work session bootstrap: (.+)$/m.exec(text)?.[1]?.trim() };
}

export function parseStatusWorktree(text) {
  return /^\s+worktree (.+)$/m.exec(text.replace(/\r/g, ''))?.[1]?.trim() ?? null;
}

// `pnpm work:session` is `tsx scripts/work-session.ts` (package.json). Running
// the repo's own tsx under this node needs no shell and no pnpm on PATH —
// pnpm is often a shell shim over corepack that cmd.exe cannot run.
export function defaultCommand(root) {
  const pkgPath = join(root, 'package.json');
  if (!existsSync(pkgPath)) throw new Error(`no package.json at ${root}: run from the skrypt-os main checkout, or set profile tracker command`);
  const script = JSON.parse(readFileSync(pkgPath, 'utf8')).scripts?.['work:session'];
  const m = /^tsx\s+(\S+)$/.exec(script ?? '');
  if (!m) throw new Error(`package.json "work:session" is "${script}", not "tsx <script>": set profile tracker command`);
  const tsxPkg = join(root, 'node_modules', 'tsx', 'package.json');
  if (!existsSync(tsxPkg)) throw new Error('tsx is not installed in this checkout: run pnpm install');
  const bin = JSON.parse(readFileSync(tsxPkg, 'utf8')).bin;
  const cli = typeof bin === 'string' ? bin : bin?.tsx;
  return [process.execPath, resolve(dirname(tsxPkg), cli), resolve(root, m[1])];
}

export function skryptTracker({ root, profile }) {
  const override = profile['tracker command'] ? profile['tracker command'].split(/\s+/).filter(Boolean) : null;
  let command = override && override[0] === 'node' ? [process.execPath, ...override.slice(1)] : override;
  const cmd = () => (command ??= defaultCommand(root));
  // Only a non-node override needs a shell (to run a .cmd shim on Windows).
  // Arguments are fixed words, validated task ids and --flags; a directory is
  // never an argument (it is the cwd), so a shell receives nothing it could
  // reinterpret.
  const run = (args, cwd = root) => {
    const [exe, ...pre] = cmd();
    return execFileSync(exe, [...pre, ...args], {
      cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 64 * 1024 * 1024,
      shell: process.platform === 'win32' && exe !== process.execPath,
    });
  };

  // Today's CLI refuses `plan --json` with its usage line; once the flag
  // exists it answers JSON. Only a usage refusal falls back to text: any
  // other failure (an expired token is a 401) is an error, never a plan.
  const plan = () => {
    let out;
    try {
      out = run(['plan', '--json']);
    } catch (e) {
      const msg = `${e.stderr ?? ''}${e.stdout ?? ''}`;
      if (!/Usage: .*work:session plan/i.test(msg)) throw e;
      return parsePlanText(run(['plan']));
    }
    try {
      const j = JSON.parse(out);
      const p = j.plan ?? j;
      if (p?.briefing) return { ...p, source: 'json' };
    } catch { /* not JSON */ }
    return parsePlanText(out);
  };

  const worktreeFor = (n) => parseStatusWorktree(run(['status', '--task', `SK-${n}`]));

  return {
    name: 'skrypt',

    // "Do these first" is the engine's order, and the only work the loop may
    // start. In-progress tasks with a live session are resumable, not new.
    tickets() {
      const p = plan();
      const withSession = new Set(p.activeSessions.map((s) => s.taskNumber));
      const shape = (t) => ({ id: `SK-${t.taskNumber}`, number: t.taskNumber, title: t.title, priority: null, areas: [], ...(t.reason ? { reason: t.reason } : {}) });
      const eligible = p.briefing.items.filter((t) => !withSession.has(t.taskNumber)).map(shape);
      const resumable = [...p.briefing.items, ...p.inProgress]
        .filter((t, i, all) => withSession.has(t.taskNumber) && all.findIndex((x) => x.taskNumber === t.taskNumber) === i)
        .map((t) => ({ ...shape(t), branch: p.activeSessions.find((s) => s.taskNumber === t.taskNumber)?.branch }));
      const skipped = p.inProgress.filter((t) => !withSession.has(t.taskNumber))
        .map((t) => ({ ...shape(t), why: ['your task, in progress with no work session: you are working it by hand; the loop leaves it alone'] }));
      return { eligible, skipped, resumable, queued: p.briefing.queuedCount, source: p.source };
    },

    // bootstrap converges: re-running it for a task you already hold returns
    // the same branch and worktree, so claim is safe to repeat.
    claim(id) {
      const n = taskNumber(id);
      const b = parseBootstrapText(run(['bootstrap', '--task', `SK-${n}`]));
      return { id: `SK-${n}`, wrote: `work session bootstrap: ${b.mutation ?? 'done'}`, branch: b.branch, worktree: b.worktree, next: 'run pnpm install in the worktree before trusting tsc or tests' };
    },

    // Ends the session (abandoning the branch). Merging the PR ends it by
    // itself, so a ticket whose PR opened is released by doing nothing here.
    release(id, outcome) {
      const n = taskNumber(id);
      if (/^PR #\d+/i.test(outcome ?? '')) return { id: `SK-${n}`, skipped: 'a PR is open: the session stays until it merges' };
      const cwd = worktreeFor(n);
      if (!cwd) throw new Error(`no session worktree for SK-${n} (work:session status shows none)`);
      run(['end', '--task', `SK-${n}`], cwd);
      return { id: `SK-${n}`, wrote: `work session ended (${outcome ?? 'released'}); list ${cwd} for removal` };
    },

    // After each push: re-evaluate Skrypt Context at the new head.
    synced(id, { cwd } = {}) {
      const n = taskNumber(id);
      const where = cwd ?? worktreeFor(n);
      if (!where) throw new Error(`no session worktree for SK-${n}`);
      run(['sync', '--task', `SK-${n}`], where);
      return { id: `SK-${n}`, wrote: 'work session synced (Skrypt Context re-checked)' };
    },

    ticketLine(id) {
      return fillTicketLine(profile['ticket line'], taskNumber(id));
    },
  };
}
