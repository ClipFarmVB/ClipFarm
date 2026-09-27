"""CF-564: the overnight brief's push guard, run as written against real git.

**Why this exists.** With several PRs cycling at once, a fixer can finish after
someone else pushed to the same branch. `docs/overnight/RULES.md` prescribes a
guard for every push to a PR branch, and argues for it from one specific gap:
git refuses a push when commits were added on top, but *accepts* one when the
branch was rewound to an ancestor, because the fix still descends from the new
tip — and the push quietly restores the commit that was removed.

That argument is a claim about git, and the guard is a shell block in a
markdown file. Neither is tested by being read. So this runs the block exactly
as the brief prints it, against throwaway repositories, in each case the brief
names — and runs a plain push beside it in the rewind case, because if a plain
push were refused there too, the guard would be ceremony and the paragraph
defending it would be wrong.

**Extracted, never copied.** The block is read out of `RULES.md` by its first
line, so editing the brief's guard changes what this test runs, and a copy here
cannot pass while the brief's version rots.

Skips where there is no POSIX `bash` and `git` — the block is bash.
"""
import os
import pathlib
import re
import shutil
import subprocess

import tempfile

import pytest

RULES = pathlib.Path(__file__).resolve().parents[2] / "docs" / "overnight" / "RULES.md"
MARKER = "# push guard (CF-564)"


def _working_bash():
    """The bash to run the guard with, or None.

    On Windows, `bash` on PATH can be the WSL launcher with no distribution
    installed: it exists and fails. So this probes, and the path it probed is
    the one the tests then run — resolving `bash` a second time could pick a
    different one.
    """
    bash = shutil.which("bash")
    if not bash or not shutil.which("git"):
        return None
    try:
        r = subprocess.run([bash, "-c", "echo ok"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bash if r.returncode == 0 and r.stdout.strip() == "ok" else None


BASH = _working_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="needs a working bash and git")


def _guard() -> str:
    """The guard block as RULES.md prints it."""
    text = RULES.read_text(encoding="utf-8")
    blocks = [b for b in re.findall(r"```\n(.*?)\n```", text, re.S) if b.startswith(MARKER)]
    assert len(blocks) == 1, f"expected exactly one block opening {MARKER!r} in RULES.md, found {len(blocks)}"
    return blocks[0]


# An empty global config, so a developer's own settings (signing, hooks, a
# default branch) cannot change what these throwaway repositories do.
_EMPTY_GLOBAL = tempfile.NamedTemporaryFile(prefix="gitconfig-", delete=False)
_EMPTY_GLOBAL.close()

ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": _EMPTY_GLOBAL.name,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(cwd, *args) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=ENV)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout.strip()


def _commit(cwd, name) -> str:
    (pathlib.Path(cwd) / name).write_text(name, encoding="utf-8")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", name)
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    """A remote with branch `feat` at A-B, a human clone, and a fixer that has
    committed F on top of B from a detached checkout — the brief's shape."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))

    human = tmp_path / "human"
    _git(tmp_path, "clone", "-q", str(remote), str(human))
    _git(human, "checkout", "-q", "-b", "feat")
    a = _commit(human, "A")
    b = _commit(human, "B")
    _git(human, "push", "-q", "origin", "feat")

    fixer = tmp_path / "fixer"
    _git(tmp_path, "clone", "-q", str(remote), str(fixer))
    _git(fixer, "fetch", "-q", "origin", "feat")
    _git(fixer, "checkout", "-q", "--detach", "FETCH_HEAD")
    started_at = _git(fixer, "rev-parse", "HEAD")
    assert started_at == b
    f = _commit(fixer, "F")
    return {"remote": remote, "human": human, "fixer": fixer, "a": a, "b": b, "f": f}


def _run_guard(r):
    return subprocess.run(
        [BASH, "-c", _guard()],
        cwd=r["fixer"],
        capture_output=True,
        text=True,
        env={**ENV, "BRANCH": "feat", "STARTED_AT": r["b"]},
    )


def _remote_head(r) -> str:
    return _git(r["remote"], "rev-parse", "refs/heads/feat")


def test_the_guard_never_forces():
    """The hard rules forbid force-pushing, and the brief explains why
    `--force-with-lease` is not used either. Pin both, so the gap the brief
    admits to is not closed later by the flag it rejected."""
    guard = _guard()
    assert "--force" not in guard
    assert not re.search(r"\bpush\b[^\n]*\s-f\b", guard)


def test_an_unmoved_branch_takes_the_push(repos):
    result = _run_guard(repos)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _remote_head(repos) == repos["f"]


def test_commits_added_on_top_stop_the_push(repos):
    """The case git would also refuse — the guard must refuse it first, and
    say why, rather than leave the fixer to read a rejection."""
    c = _commit(repos["human"], "C")
    _git(repos["human"], "push", "-q", "origin", "feat")

    result = _run_guard(repos)
    assert result.returncode != 0
    assert "head moved" in result.stdout
    assert _remote_head(repos) == c, "the human's commit must still be the tip"


def test_a_rewound_branch_stops_the_push(repos):
    """The case that justifies the guard: the human rewound `feat` to A,
    removing B. The fixer's F descends from A, so git itself would accept it."""
    _git(repos["human"], "push", "-q", "--force", "origin", f"{repos['a']}:refs/heads/feat")

    result = _run_guard(repos)
    assert result.returncode != 0
    assert "head moved" in result.stdout
    assert _remote_head(repos) == repos["a"], "the rewind must stand"


def test_an_unreadable_remote_stops_the_push(repos):
    """If the guard cannot read the remote head it must refuse, not compare
    against something stale. Review asked whether the first, fetch-based
    version would; the guard now fails closed by what it says."""
    shutil.move(str(repos["remote"]), str(repos["remote"]) + ".gone")
    try:
        result = _run_guard(repos)
    finally:
        shutil.move(str(repos["remote"]) + ".gone", str(repos["remote"]))
    assert result.returncode != 0
    assert "cannot read the remote head" in result.stdout
    assert _remote_head(repos) == repos["b"]


def test_a_deleted_branch_stops_the_push(repos):
    """A branch deleted under the fixer reads as no head at all — refuse,
    rather than recreate it."""
    _git(repos["human"], "push", "-q", "origin", "--delete", "feat")
    result = _run_guard(repos)
    assert result.returncode != 0
    assert "cannot read the remote head" in result.stdout
    assert _git(repos["remote"], "branch", "--list", "feat") == ""


def test_a_plain_push_would_have_undone_the_rewind(repos):
    """The control. If this ever fails — if git starts refusing the rewind case
    on its own — the guard's stated reason is gone and the brief is wrong."""
    _git(repos["human"], "push", "-q", "--force", "origin", f"{repos['a']}:refs/heads/feat")

    _git(repos["fixer"], "push", "-q", "origin", "HEAD:feat")
    assert _remote_head(repos) == repos["f"]
    log = _git(repos["remote"], "log", "--format=%s", "refs/heads/feat")
    assert "B" in log.splitlines(), "the removed commit came back with the fix"
