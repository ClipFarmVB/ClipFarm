// Every GitHub write the loop makes, over REST (lib/gh.mjs picks gh or curl).
// Each returns what it wrote and, where cheap, reads it back: a write's own
// success response is not evidence that it landed as intended.
import { api, apiAll, transport, run, GitHubError } from './gh.mjs';

const enc = encodeURIComponent;

export function comment(repo, n, body) {
  const c = api('POST', `repos/${repo}/issues/${n}/comments`, { body });
  const back = api('GET', `repos/${repo}/issues/comments/${c.id}`);
  if ((back.body ?? '').trim() !== body.trim()) throw new GitHubError(`comment ${c.id} on #${n} reads back differently than written (was anything stripped?)`);
  return { wrote: 'comment', id: c.id, url: c.html_url };
}

export function editComment(repo, id, body) {
  const c = api('PATCH', `repos/${repo}/issues/comments/${id}`, { body });
  return { wrote: 'comment edit', id: c.id, url: c.html_url };
}

// A PR review that only comments (the round's findings). Never approves or
// requests changes: the loop's verdicts live in its markers.
export function review(repo, n, body) {
  const r = api('POST', `repos/${repo}/pulls/${n}/reviews`, { body, event: 'COMMENT' });
  return { wrote: 'review', id: r.id, url: r.html_url };
}

// POST .../labels ADDS to the set (unlike the MCP label write, which replaces
// it). Read the labels back from the response.
export function addLabels(repo, n, labels) {
  const now = api('POST', `repos/${repo}/issues/${n}/labels`, { labels });
  const names = now.map((l) => l.name);
  const missing = labels.filter((l) => !names.includes(l));
  if (missing.length) throw new GitHubError(`labels not applied to #${n}: ${missing.join(', ')}`);
  return { wrote: `labels +${labels.join(', +')}`, labels: names };
}

export function removeLabel(repo, n, label) {
  try {
    const now = api('DELETE', `repos/${repo}/issues/${n}/labels/${enc(label)}`);
    return { wrote: `label -${label}`, labels: (now ?? []).map((l) => l.name) };
  } catch (e) {
    if (e.status === 404) return { skipped: `#${n} did not carry ${label}` };
    throw e;
  }
}

export function ensureLabel(repo, name, color, description) {
  try {
    api('POST', `repos/${repo}/labels`, { name, color, description });
    return 'created';
  } catch (e) {
    if (e.status !== 422) throw e;
    api('PATCH', `repos/${repo}/labels/${enc(name)}`, { color, description });
    return 'updated';
  }
}

export function ensureMilestones(repo, wanted) {
  const have = new Set(apiAll(`repos/${repo}/milestones?state=all&per_page=100`).map((m) => m.title));
  const made = [];
  for (const [title, description] of wanted) {
    if (have.has(title)) continue;
    api('POST', `repos/${repo}/milestones`, { title, description });
    made.push(title);
  }
  return made;
}

export function editPrBody(repo, n, body) {
  const pr = api('PATCH', `repos/${repo}/pulls/${n}`, { body });
  if ((pr.body ?? '').trim() !== body.trim()) throw new GitHubError(`PR #${n} body reads back differently than written`);
  return { wrote: 'PR body', url: pr.html_url };
}

export function editIssue(repo, n, { body, title, state, stateReason }) {
  const patch = {};
  if (body !== undefined) patch.body = body;
  if (title !== undefined) patch.title = title;
  if (state) patch.state = state;
  if (stateReason) patch.state_reason = stateReason;
  const i = api('PATCH', `repos/${repo}/issues/${n}`, patch);
  return { wrote: `issue #${n}: ${Object.keys(patch).join(', ')}`, url: i.html_url, state: i.state, stateReason: i.state_reason };
}

export function createIssue(repo, { title, body, labels = [] }) {
  const i = api('POST', `repos/${repo}/issues`, { title, body, labels });
  return { wrote: 'issue', number: i.number, url: i.html_url };
}

export function openPr(repo, { title, head, base, body, draft = true }) {
  const pr = api('POST', `repos/${repo}/pulls`, { title, head, base, body, draft });
  return { wrote: `${draft ? 'draft ' : ''}PR`, number: pr.number, url: pr.html_url, head: pr.head.sha };
}

// Draft -> ready is GraphQL-only on GitHub. Locally gh does it; in a cloud
// session (no gh, GraphQL refused) it cannot be done from here.
export function markReady(repo, n) {
  if (transport() !== 'gh') {
    throw new GitHubError(`marking PR #${n} ready needs GraphQL, which this session cannot reach. Use the GitHub MCP tool update_pull_request with draft: false, then read the PR back and confirm draft is false.`);
  }
  run('gh', ['pr', 'ready', String(n), '-R', repo]);
  const pr = api('GET', `repos/${repo}/pulls/${n}`);
  if (pr.draft) throw new GitHubError(`PR #${n} is still a draft after gh pr ready`);
  return { wrote: `PR #${n} ready for review` };
}

// Squash-merge only if the head is still `sha`; then delete the branch
// through the API (not --delete-branch, which also deletes a local branch a
// worktree may hold). Confirms .merged by reading it back.
export function mergePr(repo, n, sha) {
  if (!/^[0-9a-f]{40}$/i.test(sha ?? '')) throw new GitHubError('merge needs --sha FULL_40_CHAR_HEAD');
  const pr = api('GET', `repos/${repo}/pulls/${n}`);
  if (pr.draft) markReady(repo, n);
  api('PUT', `repos/${repo}/pulls/${n}/merge`, { merge_method: 'squash', sha });
  const after = api('GET', `repos/${repo}/pulls/${n}`);
  if (!after.merged) throw new GitHubError(`PR #${n} reports merged: false after the merge call`);
  let branch = 'kept';
  if (after.head.repo?.full_name === repo) {
    try { api('DELETE', `repos/${repo}/git/refs/heads/${after.head.ref.split('/').map(enc).join('/')}`); branch = 'deleted'; } catch (e) { branch = `not deleted: ${e.message}`; }
  }
  return { wrote: `merged #${n}`, mergeSha: after.merge_commit_sha, branch };
}

export function rerunFailed(repo, runId) {
  api('POST', `repos/${repo}/actions/runs/${runId}/rerun-failed-jobs`);
  return { wrote: `rerun of failed jobs in run ${runId}` };
}

// The failed jobs of a run and the tail of each one's log. The log endpoint
// redirects to blob storage, which a cloud proxy may not allow: then say so.
export function failedLog(repo, runId, tailLines = 200) {
  const jobs = apiAll(`repos/${repo}/actions/runs/${runId}/jobs?per_page=100`).filter((j) => j.conclusion && !['success', 'skipped', 'neutral'].includes(j.conclusion));
  return jobs.map((j) => {
    const failedSteps = (j.steps ?? []).filter((s) => s.conclusion === 'failure').map((s) => s.name);
    let log;
    try {
      log = transport() === 'gh'
        ? run('gh', ['api', `repos/${repo}/actions/jobs/${j.id}/logs`])
        : run('curl', ['-sSL', '-H', 'Accept: application/vnd.github+json', '-H', 'User-Agent: claude-harness', ...(process.env.GH_TOKEN || process.env.GITHUB_TOKEN ? ['-H', `Authorization: Bearer ${process.env.GH_TOKEN || process.env.GITHUB_TOKEN}`] : []), `${(process.env.HARNESS_API_BASE ?? 'https://api.github.com').replace(/\/$/, '')}/repos/${repo}/actions/jobs/${j.id}/logs`]);
      log = log.split('\n').slice(-tailLines).join('\n');
    } catch (e) {
      log = `log unavailable here (${(e.stderr ?? e.message).toString().trim().slice(0, 200)}); the failed steps are named above`;
    }
    return { job: j.name, conclusion: j.conclusion, failedSteps, log };
  });
}
