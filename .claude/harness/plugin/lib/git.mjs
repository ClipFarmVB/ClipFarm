// Git guards that were shell prose in the old briefs. Each returns one outcome
// word, so a caller never has to interpret git's stderr itself.
import { run } from './gh.mjs';

const git = (args, cwd) => run('git', args, { cwd });
const tryGit = (args, cwd) => {
  try { return { ok: true, out: git(args, cwd) }; } catch (e) { return { ok: false, out: `${e.stdout ?? ''}${e.stderr ?? ''}`.trim(), code: e.status }; }
};

// Push HEAD to BRANCH only if the remote still points at STARTED (the full
// SHA the work was built on). A plain push rejects commits added on top but
// ACCEPTS a rewind, because the fix still descends from the rewound tip, and
// silently restores a commit someone removed. ls-remote, not fetch +
// FETCH_HEAD: a failed fetch leaves FETCH_HEAD empty and passes by accident.
// Never --force-with-lease: it is a force flag.
export function guardedPush({ branch, started, cwd, remote = 'origin', dryRun = false }) {
  if (!/^[0-9a-f]{40}$/i.test(started ?? '')) return { outcome: 'bad-args', detail: '--started must be the full 40-character SHA' };
  const ls = tryGit(['ls-remote', remote, `refs/heads/${branch}`], cwd);
  const remoteSha = ls.ok ? ls.out.split(/\s/)[0] : '';
  if (!remoteSha) return { outcome: 'remote unreadable', detail: ls.out || 'no such branch on the remote' };
  if (remoteSha.toLowerCase() !== started.toLowerCase()) return { outcome: 'head moved', detail: `remote is ${remoteSha}, work started at ${started}` };
  if (dryRun) return { outcome: 'would push', detail: `remote still at ${started}` };
  const push = tryGit(['push', remote, `HEAD:refs/heads/${branch}`], cwd);
  if (push.ok) return { outcome: 'pushed', detail: git(['rev-parse', 'HEAD'], cwd).trim() };
  if (/fetch first|non-fast-forward|\(stale info\)/i.test(push.out)) return { outcome: 'head moved', detail: push.out };
  if (/could not read|unable to access|Could not resolve host|timed out|Connection/i.test(push.out)) return { outcome: 'remote unreadable', detail: push.out };
  return { outcome: 'push refused', detail: push.out };
}

// Did the head move because of the loop's own fixer, or because a person
// pushed? The loop and the human commit as the same identity, so the tell is
// the round tag every fixer commit subject ends with: "(round @<sha7>)".
export function whoMovedHead({ pr, started, cwd, remote = 'origin' }) {
  const f = tryGit(['fetch', '-q', remote, `pull/${pr}/head`], cwd);
  if (!f.ok) return { outcome: 'remote unreadable', detail: f.out };
  const head = git(['rev-parse', 'FETCH_HEAD'], cwd).trim();
  if (head.toLowerCase() === started.toLowerCase()) return { outcome: 'unchanged', head };
  // Ancestry first: an empty range makes "every subject is tagged" vacuously true.
  if (!tryGit(['merge-base', '--is-ancestor', started, head], cwd).ok) return { outcome: 'rewritten', head, detail: 'the branch no longer contains the start: head moved' };
  const subjects = git(['log', '--format=%s', `${started}..${head}`], cwd).split('\n').filter(Boolean);
  const tag = `(round @${started.slice(0, 7).toLowerCase()})`;
  const untagged = subjects.filter((s) => !s.toLowerCase().endsWith(tag));
  if (!subjects.length) return { outcome: 'unchanged', head };
  return untagged.length
    ? { outcome: 'person pushed', head, detail: untagged }
    : { outcome: 'our push landed', head, detail: subjects };
}

export function isShallow(cwd) {
  return git(['rev-parse', '--is-shallow-repository'], cwd).trim() === 'true';
}
