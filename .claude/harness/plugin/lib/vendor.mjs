// A pinned copy of the plugin inside a repository, for sessions that cannot
// install the plugin: a cloud session loads only what the repo carries.
//
//   .claude/harness/plugin/{bin,lib,brief}/…   the CLI and the brief
//   .claude/harness/plugin/VENDORED.json       version, source, a hash per file
//   .claude/skills/harness-<name>/…            each skill, renamed and repointed
//
// It is generated, never edited: `harness vendor --check` fails when the copy
// differs from the plugin or from its own manifest (a hand edit).
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';

export const VENDOR_DIR = '.claude/harness/plugin';
export const SKILL_PREFIX = 'harness-';
const PLUGIN_PATH = '${CLAUDE_PROJECT_DIR}/.claude/harness/plugin';

const posix = (p) => p.split(sep).join('/');
const sha = (s) => createHash('sha256').update(s).digest('hex');

function walk(dir) {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

// Skill names are namespaced by the plugin (`harness:build-loop`); a project
// skill is not, so the copy is `harness-build-loop`, and every reference in
// the copied text follows it.
export function rewrite(text, skills) {
  let out = text.replaceAll('${CLAUDE_PLUGIN_ROOT}', PLUGIN_PATH);
  for (const s of skills) {
    out = out.replaceAll(`/harness:${s}`, `/${SKILL_PREFIX}${s}`).replaceAll(`harness:${s}`, `${SKILL_PREFIX}${s}`);
  }
  return out;
}

function skillFrontmatter(text, name) {
  return text.replace(/^---\n([\s\S]*?)\n---\n/, (_, fm) => {
    const lines = fm.split('\n').map((l) => (l.startsWith('name:') ? `name: ${SKILL_PREFIX}${name}` : l))
      .map((l) => (l.startsWith('description:') ? `description: (Repo-pinned copy of the harness plugin's ${name} skill, for sessions without the plugin; prefer harness:${name} where the plugin is installed.) ${l.slice('description:'.length).trim()}` : l));
    return `---\n${lines.join('\n')}\n---\n`;
  });
}

// Every file the vendored copy should contain, with its content.
export function plan(pluginRoot) {
  const skills = readdirSync(join(pluginRoot, 'skills'));
  const files = new Map();
  for (const part of ['bin', 'lib', 'brief']) {
    for (const f of walk(join(pluginRoot, part))) {
      const rel = posix(relative(pluginRoot, f));
      const raw = readFileSync(f, 'utf8').replace(/\r\n/g, '\n');
      files.set(`${VENDOR_DIR}/${rel}`, rel.endsWith('.md') ? rewrite(raw, skills) : raw);
    }
  }
  for (const s of skills) {
    for (const f of walk(join(pluginRoot, 'skills', s))) {
      const rel = posix(relative(join(pluginRoot, 'skills', s), f));
      let text = rewrite(readFileSync(f, 'utf8').replace(/\r\n/g, '\n'), skills);
      if (rel === 'SKILL.md') text = skillFrontmatter(text, s);
      files.set(`.claude/skills/${SKILL_PREFIX}${s}/${rel}`, text);
    }
  }
  return { skills, files };
}

export function vendor(pluginRoot, repoRoot, { version, source }) {
  const { skills, files } = plan(pluginRoot);
  // Remove the previous copy first, so a file dropped from the plugin goes too.
  const old = readManifest(repoRoot);
  for (const p of Object.keys(old?.files ?? {})) rmSync(join(repoRoot, p), { force: true });
  pruneEmpty(join(repoRoot, VENDOR_DIR));
  for (const [p, text] of files) {
    mkdirSync(dirname(join(repoRoot, p)), { recursive: true });
    writeFileSync(join(repoRoot, p), text);
  }
  const manifest = { version, source, generatedBy: 'harness vendor', skills: skills.map((s) => SKILL_PREFIX + s), files: Object.fromEntries([...files].map(([p, t]) => [p, sha(t)])) };
  writeFileSync(join(repoRoot, VENDOR_DIR, 'VENDORED.json'), `${JSON.stringify(manifest, null, 2)}\n`);
  return { wrote: files.size + 1, version, skills: manifest.skills };
}

function pruneEmpty(dir) {
  if (!existsSync(dir) || !statSync(dir).isDirectory()) return;
  for (const f of readdirSync(dir)) pruneEmpty(join(dir, f));
  if (readdirSync(dir).length === 0) rmSync(dir, { recursive: true, force: true });
}

// From inside a vendored copy there is no plugin to compare against; check
// the files against the manifest's hashes (a hand edit, a missing file). A
// repo's CI can run this.
export function selfCheck(repoRoot) {
  const manifest = readManifest(repoRoot);
  if (!manifest) return { ok: false, problems: ['not vendored: no VENDORED.json'] };
  const problems = [];
  for (const [p, hash] of Object.entries(manifest.files)) {
    const abs = join(repoRoot, p);
    if (!existsSync(abs)) { problems.push(`missing: ${p}`); continue; }
    if (sha(readFileSync(abs, 'utf8').replace(/\r\n/g, '\n')) !== hash) problems.push(`edited by hand: ${p}`);
  }
  return { ok: problems.length === 0, version: manifest.version, source: manifest.source, problems };
}

export function readManifest(repoRoot) {
  const p = join(repoRoot, VENDOR_DIR, 'VENDORED.json');
  return existsSync(p) ? JSON.parse(readFileSync(p, 'utf8')) : null;
}

// What differs: files the plugin would write differently (stale), files
// missing, files edited since they were vendored (hand edits), and files the
// manifest lists that the plugin no longer has.
export function check(pluginRoot, repoRoot, { version }) {
  const manifest = readManifest(repoRoot);
  if (!manifest) return { ok: false, problems: ['not vendored: no VENDORED.json'] };
  const { files } = plan(pluginRoot);
  const problems = [];
  if (manifest.version !== version) problems.push(`vendored ${manifest.version}, plugin is ${version}`);
  for (const [p, text] of files) {
    const abs = join(repoRoot, p);
    if (!existsSync(abs)) { problems.push(`missing: ${p}`); continue; }
    const have = readFileSync(abs, 'utf8').replace(/\r\n/g, '\n');
    if (manifest.files[p] && sha(have) !== manifest.files[p]) problems.push(`edited by hand: ${p}`);
    else if (have !== text) problems.push(`stale: ${p}`);
  }
  for (const p of Object.keys(manifest.files)) if (!files.has(p)) problems.push(`no longer in the plugin: ${p}`);
  return { ok: problems.length === 0, version: manifest.version, problems };
}
