// The one GitHub client. Every read and write goes through api()/apiAll(),
// over one of two transports:
//
//   gh    the gh CLI, when installed (it owns the local credential)
//   rest  curl against the REST API, when gh is not (a cloud session: no gh,
//         and its egress proxy attaches the credential — probed 2026-09-28:
//         GET 200, and a POST reached GitHub and came back GitHub's own 404).
//         curl, not fetch: Node 22's fetch ignores proxy environment
//         variables, and curl is what the probe proved.
//
// REST only, never GraphQL: a cloud session's proxy refuses GraphQL.
// execFileSync with argument arrays: no shell, so no Git Bash path rewriting,
// no PowerShell brace parsing, and no backticks running as commands.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export function run(cmd, args, opts = {}) {
  return execFileSync(cmd, args, {
    encoding: 'utf8',
    maxBuffer: 256 * 1024 * 1024,
    stdio: ['pipe', 'pipe', 'pipe'],
    ...opts,
  });
}

export class GitHubError extends Error {
  constructor(message, { status, retryAfter } = {}) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

let chosen;
export function transport() {
  if (chosen) return chosen;
  const forced = process.env.HARNESS_TRANSPORT;
  if (forced === 'gh' || forced === 'rest') return (chosen = forced);
  try { run('gh', ['--version']); chosen = 'gh'; } catch { chosen = 'rest'; }
  return chosen;
}

const API = () => (process.env.HARNESS_API_BASE ?? 'https://api.github.com').replace(/\/$/, '');
const strip = (path) => path.replace(/^\/+/, '');

// One HTTP request. Returns { status, headers, body } with body parsed.
function request(method, url, body) {
  if (transport() === 'gh') {
    // gh api takes a path (or a full URL, for pagination's next links).
    const args = ['api', '-X', method, '-i', url.startsWith('http') ? url : strip(url)];
    if (body !== undefined) args.push('--input', '-');
    let out;
    try {
      out = run('gh', args, body !== undefined ? { input: JSON.stringify(body) } : {});
    } catch (e) {
      out = `${e.stdout ?? ''}`;
      if (!/^HTTP\/[\d.]+ \d{3}/.test(out)) throw new GitHubError(`gh ${method} ${url}: ${(e.stderr ?? e.message).trim()}`);
    }
    return parseRaw(out.replace(/\r\n/g, '\n'));
  }
  const dir = mkdtempSync(join(tmpdir(), 'harness-rest-'));
  try {
    const head = join(dir, 'h');
    const out = join(dir, 'b');
    const full = url.startsWith('http') ? url : `${API()}/${strip(url)}`;
    const args = ['-sS', '-X', method, '-D', head, '-o', out, '-w', '%{http_code}',
      '-H', 'Accept: application/vnd.github+json', '-H', 'X-GitHub-Api-Version: 2022-11-28',
      '-H', 'User-Agent: claude-harness'];
    const token = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
    if (token) args.push('-H', `Authorization: Bearer ${token}`);
    let input;
    if (body !== undefined) { args.push('-H', 'Content-Type: application/json', '--data-binary', '@-'); input = JSON.stringify(body); }
    args.push(full);
    let code;
    try { code = run('curl', args, input !== undefined ? { input } : {}).trim(); } catch (e) {
      throw new GitHubError(`curl ${method} ${full}: ${(e.stderr ?? e.message).trim()}`);
    }
    const headers = parseHeaders(readFileSync(head, 'utf8'));
    const text = readFileSync(out, 'utf8');
    return { status: Number(code), headers, body: text.trim() ? safeJson(text) : null };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const safeJson = (t) => { try { return JSON.parse(t); } catch { return t; } };

function parseHeaders(text) {
  // With redirects there can be several header blocks; the last one is ours.
  const blocks = text.replace(/\r\n/g, '\n').trim().split(/\n\n(?=HTTP\/)/);
  const lines = blocks[blocks.length - 1].split('\n');
  const h = {};
  for (const l of lines.slice(1)) {
    const i = l.indexOf(':');
    if (i > 0) h[l.slice(0, i).trim().toLowerCase()] = l.slice(i + 1).trim();
  }
  h[':status'] = Number(/^HTTP\/[\d.]+ (\d{3})/.exec(lines[0])?.[1] ?? 0);
  return h;
}

function parseRaw(raw) {
  const at = raw.search(/\n\n/);
  const headText = at < 0 ? raw : raw.slice(0, at);
  const headers = parseHeaders(headText);
  const text = at < 0 ? '' : raw.slice(at + 2);
  return { status: headers[':status'], headers, body: text.trim() ? safeJson(text) : null };
}

function check(method, url, res) {
  if (res.status >= 200 && res.status < 300) return res;
  const msg = typeof res.body === 'object' && res.body?.message ? res.body.message : String(res.body ?? '').slice(0, 300);
  const retryAfter = res.headers['retry-after'];
  const limited = res.status === 429 || (res.status === 403 && (/rate limit/i.test(msg) || res.headers['x-ratelimit-remaining'] === '0' || retryAfter));
  throw new GitHubError(`${limited ? 'rate-limited: ' : ''}HTTP ${res.status} ${method} ${url}: ${msg}${retryAfter ? ` (retry-after ${retryAfter})` : ''}`, { status: res.status, retryAfter });
}

// A single call. Returns the parsed body (null for 204).
export function api(method, path, body) {
  return check(method, path, request(method, path, body)).body;
}

// A GET that also returns the response headers (rate limits, token scopes).
export function apiWithHeaders(path) {
  const res = check('GET', path, request('GET', path));
  return { headers: res.headers, body: res.body };
}

// Every page of a list endpoint, flattened, following Link rel="next".
export function apiAll(path) {
  const out = [];
  let url = path;
  for (let page = 0; url; page++) {
    if (page > 200) throw new GitHubError(`apiAll ${path}: more than 200 pages`);
    const res = check('GET', url, request('GET', url));
    const items = Array.isArray(res.body) ? res.body : (res.body?.items ?? res.body?.check_runs ?? []);
    out.push(...items);
    url = /<([^>]+)>;\s*rel="next"/.exec(res.headers.link ?? '')?.[1] ?? null;
  }
  return out;
}

// Back-compat names used across the CLI.
export const ghAll = apiAll;
export const ghJson = (args) => {
  if (args[0] !== 'api' || args.length !== 2) throw new Error('ghJson only supports ["api", path]; use api()');
  return api('GET', args[1]);
};

let cachedLogin;
// The account the loop acts as. In a cloud session writes are made as the
// user through the Claude GitHub app (seen on ClipFarm: 734 comments by the
// user, performed_via_github_app "claude"), so /user answers the same login.
export function me() {
  if (cachedLogin) return cachedLogin;
  if (process.env.HARNESS_LOGIN) return (cachedLogin = process.env.HARNESS_LOGIN);
  try {
    cachedLogin = api('GET', 'user')?.login;
  } catch (e) {
    throw new GitHubError(`cannot read this account's login (${e.message}). Set HARNESS_LOGIN; never widen a scope without it.`);
  }
  if (!cachedLogin) throw new GitHubError('GET /user returned no login. Set HARNESS_LOGIN.');
  return cachedLogin;
}

// owner/name from the profile, else from origin. A cloud session's origin
// can be a local git proxy URL (…/git/OWNER/NAME), so take the last two
// path segments.
export function parseSlug(url) {
  const m = /[/:]([^/:@\s]+)\/([^/\s]+?)(?:\.git)?\/?$/.exec(url.trim());
  return m ? `${m[1]}/${m[2]}` : null;
}

export function repoSlug(profile, cwd = process.cwd()) {
  if (profile?.repo) return profile.repo;
  const url = run('git', ['remote', 'get-url', 'origin'], { cwd }).trim();
  const slug = parseSlug(url);
  if (!slug) throw new GitHubError(`cannot read owner/name from origin "${url}": set profile repo`);
  return slug;
}

// Everything routing needs about one PR, in raw GitHub shapes.
export function fetchPr(repo, num) {
  const base = `repos/${repo}`;
  const pr = api('GET', `${base}/pulls/${num}`);
  const sha = pr.head.sha;
  return {
    pr,
    comments: apiAll(`${base}/issues/${num}/comments?per_page=100`),
    reviews: apiAll(`${base}/pulls/${num}/reviews?per_page=100`),
    reviewComments: apiAll(`${base}/pulls/${num}/comments?per_page=100`),
    files: apiAll(`${base}/pulls/${num}/files?per_page=100`).map((f) => f.filename),
    checkRuns: apiAll(`${base}/commits/${sha}/check-runs?per_page=100`),
  };
}

// For tests: forget the cached transport and login.
export function _reset() { chosen = undefined; cachedLogin = undefined; }

// Body text for writes, from --body-file PATH or "-" (stdin).
export function readBody(file) {
  if (!file || file === true) throw new Error('needs --body-file PATH (or - for stdin)');
  return readFileSync(file === '-' ? 0 : file, 'utf8');
}

