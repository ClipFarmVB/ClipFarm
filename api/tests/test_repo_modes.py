"""CF-381: every file under `.hooks/` is tracked as executable.

**Why this exists.** Git skips a hook that is not executable. It says so only
in a one-line hint (silenced by `advice.ignoredHook false`) and the commit
goes through with nothing run. `.hooks/pre-commit` sat at mode 100644 on
`main`, so every clone that followed the setup in `CLAUDE.md` had no local
checks, while `git config core.hooksPath` still printed `.hooks`.

**Why here and not in the hook.** The hook cannot catch the loss of its own
bit: once the bit is gone, the hook does not run. So the check lives in a test
CI runs. Git for Windows runs hooks by their `#!` line and ignores the bit,
which is how the bad mode went unnoticed there.

**What it reads.** The mode recorded in git's index (`git ls-files -s`), not
the file's permissions on disk, so it gives the same answer on every OS and
whatever `core.fileMode` is set to. It is the mode a fresh clone checks out.

Skips where there is no `git` or no `.git` (a source tree not checked out by
git, such as a Docker build context).
"""
import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(
    GIT is None or not (REPO / ".git").exists(),
    reason="needs git and a git checkout",
)


def test_every_hook_is_tracked_as_executable():
    r = subprocess.run(
        [GIT, "-C", str(REPO), "ls-files", "-s", "--", ".hooks/"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    # Each line: "<mode> <blob> <stage>\t<path>"
    entries = [line.split("\t", 1) for line in r.stdout.splitlines() if line]
    assert entries, "git ls-files found nothing under .hooks/"
    wrong = [f"{path} is {meta.split()[0]}" for meta, path in entries
             if meta.split()[0] != "100755"]
    assert not wrong, (
        "hooks must be tracked as 100755 or git skips them: "
        + ", ".join(wrong)
        + ". Fix with: git update-index --chmod=+x <path>"
    )
