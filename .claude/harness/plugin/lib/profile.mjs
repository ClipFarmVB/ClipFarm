// A repo's harness profile: `.claude/harness/profile.md`, a markdown file whose
// first ```harness fenced block holds `key: value  # comment` lines. The prose
// around the block is repo context the orchestrator passes to subagents.
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

export const PROFILE_PATH = '.claude/harness/profile.md';

// Every key the brief refers to, with its default. A key not listed here is a
// typo, and a typo in a safety setting must not silently fall back.
export const DEFAULTS = {
  repo: '',                         // owner/name; empty = the checkout's origin
  base: 'main',
  tracker: 'github',                // github | skrypt
  'tracker command': '',            // skrypt: how to run work:session (default: the repo's tsx on scripts/work-session.ts)
  labels: 'yes',                    // yes | no (record comments only)
  'pr scope': 'loop-label',         // loop-label | loop-body | own
  'loop label': 'loop',
  'loop line': 'Opened by the harness loop.',
  'ready label': 'loop-ok',
  'claim label': 'in-progress',
  'hold label': 'hold',
  'decision label': 'needs-decision',
  'settled label': 'review-settled',
  'unsettled label': 'unsettled',
  'area prefix': 'area:',
  'merge policy': 'human',          // auto | human
  'wip limit': '3',
  'round cap': '7',
  'round budget': '0',              // rounds per run across all PRs; 0 = no budget
  'pr cap': '0',                    // PRs a run may open; 0 = no cap
  'settle rounds': '1',             // 1 | 2 clean cold rounds for a PR that never had a finding
  'mediums block': 'no',            // no | yes
  areas: 'labels',                  // labels | files
  'serial dirs': '',                // dirs held whole (migrations), comma-separated
  'self-approve cards': 'no',       // no | yes: may the run put the ready label on its own cards
  'plan step': 'off',               // off | on: a read-only planner before each implementer
  gate: '',                         // the one command that is the gate
  branch: 'type/NUM-slug',
  'ticket line': 'Closes #NUM',
  'port base': '3100',
  'harness paths': '',              // extra paths, comma-separated
  'high risk': '',                  // paths that make a PR high-risk, comma-separated
  'ship reviewers': 'none',         // none | comma list of: sonnet, sol
  status: 'issue',                  // issue | file
  pack: 'none',                     // none | hackathon
  log: '.claude/harness/log.md',
};

const ENUMS = {
  tracker: ['github', 'skrypt'],
  labels: ['yes', 'no'],
  'pr scope': ['loop-label', 'loop-body', 'own'],
  'merge policy': ['auto', 'human'],
  status: ['issue', 'file'],
  pack: ['none', 'hackathon'],
  'settle rounds': ['1', '2'],
  'mediums block': ['no', 'yes'],
  areas: ['labels', 'files'],
  'self-approve cards': ['no', 'yes'],
  'plan step': ['off', 'on'],
};
const INTS = ['wip limit', 'round cap', 'port base', 'settle rounds'];
const ZERO_OK = ['round budget', 'pr cap'];
const LISTS = ['harness paths', 'high risk', 'ship reviewers', 'serial dirs'];

// Paths that are harness in every repo: the loop never merges a change to its
// own rules or CI.
export const CORE_HARNESS_PATHS = ['.claude/', 'CLAUDE.md', 'AGENTS.md', '.github/'];

export class ProfileError extends Error {}

export function parseProfile(text) {
  const m = /^```harness[^\n]*\n([\s\S]*?)^```/m.exec(text.replace(/\r\n/g, '\n'));
  if (!m) throw new ProfileError('no ```harness block in the profile');
  const raw = {};
  m[1].split('\n').forEach((line, i) => {
    const body = line.replace(/\s+#.*$/, '').trim();
    if (!body || body.startsWith('#')) return;
    const at = body.indexOf(':');
    if (at < 1) throw new ProfileError(`line ${i + 1}: expected "key: value", got "${line.trim()}"`);
    const key = body.slice(0, at).trim().toLowerCase();
    if (!(key in DEFAULTS)) throw new ProfileError(`unknown key "${key}"`);
    if (key in raw) throw new ProfileError(`key "${key}" appears twice`);
    raw[key] = body.slice(at + 1).trim();
  });
  const out = { ...DEFAULTS, ...raw };
  for (const [k, allowed] of Object.entries(ENUMS)) {
    if (!allowed.includes(out[k])) throw new ProfileError(`${k}: "${out[k]}" is not one of ${allowed.join(' | ')}`);
  }
  for (const k of INTS) {
    if (!/^\d+$/.test(out[k]) || Number(out[k]) < 1) throw new ProfileError(`${k}: "${out[k]}" is not a positive integer`);
    out[k] = Number(out[k]);
  }
  for (const k of ZERO_OK) {
    if (!/^\d+$/.test(out[k])) throw new ProfileError(`${k}: "${out[k]}" is not a whole number`);
    out[k] = Number(out[k]);
  }
  for (const k of LISTS) {
    out[k] = out[k] && out[k] !== 'none' ? out[k].split(',').map((s) => s.trim()).filter(Boolean) : [];
  }
  for (const r of out['ship reviewers']) {
    if (!['sonnet', 'sol'].includes(r)) throw new ProfileError(`ship reviewers: unknown reviewer "${r}"`);
  }
  if (out.labels === 'no' && out['pr scope'] === 'loop-label') {
    throw new ProfileError('labels: no needs pr scope loop-body or own (a loop-label scope needs labels)');
  }
  if (out.tracker === 'skrypt' && out['merge policy'] === 'auto') {
    // docs/epics/autonomous-harness.md: unattended work is drafts first.
    throw new ProfileError('tracker skrypt: merge policy must be human (unattended Skrypt work is drafts first)');
  }
  out['all harness paths'] = [...CORE_HARNESS_PATHS, ...out['harness paths']];
  return out;
}

export function loadProfile(root = process.cwd()) {
  const p = join(root, PROFILE_PATH);
  if (!existsSync(p)) throw new ProfileError(`no profile at ${p} — run the harness:init skill`);
  return parseProfile(readFileSync(p, 'utf8'));
}

// `docs/` matches docs/x.md; `CLAUDE.md` matches only that file.
export function matchesAny(file, prefixes) {
  return prefixes.some((p) => (p.endsWith('/') ? file.startsWith(p) : file === p || file.startsWith(p + '/')));
}
