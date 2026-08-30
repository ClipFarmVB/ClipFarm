"""Unit tests for the shared interval primitives (ml/pipeline/intervals.py).

Split out of test_dead_time.py by CF-95. They import from ml.pipeline.intervals
directly rather than through dead_time, which still re-exports the names for its
own use — a test that reached through the re-export would pass even if the move
had not happened.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.pipeline.intervals import merge_intervals

# Below the import deliberately: ruff exempts an import that follows `sys.path`
# manipulation from E402, and a plain assignment in between forfeits that.
REPO_ROOT = Path(__file__).resolve().parents[2]


class TestMergeIntervals:
    def test_empty(self):
        assert merge_intervals([]) == []

    def test_disjoint_stay_separate(self):
        assert merge_intervals([(0, 1), (5, 6)], merge_gap_seconds=1.0) == [(0, 1), (5, 6)]

    def test_within_gap_merge(self):
        assert merge_intervals([(0, 1), (2, 3)], merge_gap_seconds=1.5) == [(0, 3)]

    def test_overlapping_merge(self):
        assert merge_intervals([(0, 5), (3, 8)]) == [(0, 8)]

    def test_contained_interval_absorbed(self):
        assert merge_intervals([(0, 10), (2, 4)]) == [(0, 10)]

    def test_unsorted_input(self):
        assert merge_intervals([(5, 6), (0, 1)], merge_gap_seconds=0.5) == [(0, 1), (5, 6)]



class TestTheImportStaysLight:
    """The invariant this module exists for, enforced instead of asserted.

    `ml/eval/metrics.py` documents itself as having no app, no DB and no I/O
    imports, and reached into a pipeline module for `merge_intervals` to keep
    that promise. Moving the primitives here is what makes that safe — but
    nothing was checking it, so the promise held only as long as nobody added
    an import.

    That is not hypothetical: CF-187 (#243) grew `dead_time.py` a numpy import
    while #301 sat open, which is what made this move necessary in the first
    place. It broke the invariant and every gate stayed green.

    A note on why the gates cannot catch it. An earlier version of the module
    docstring said the eval unit tests "run in CI with only ruff, mypy and
    pytest installed". They do not — `.github/workflows/ci.yml` pins and
    installs `numpy==1.26.4` immediately before `python -m pytest ml/tests/`,
    deliberately, and says so in its own comment. Only `ruff check ml/eval` and
    `mypy ml/eval` run numpy-free, and neither executes an import. So the
    environment that would have exposed a heavy import does not exist in CI at
    the moment it would matter, and this test manufactures it.
    """

    #: Import roots that must not be reachable. numpy and cv2 are the ones this
    #: has actually been broken by; torch and ultralytics are the heavier things
    #: the detection side pulls in and would be worse.
    BLOCKED = frozenset({"numpy", "cv2", "torch", "ultralytics"})

    def _import_under_blocker(self, module_name):
        """Import `module_name` in a fresh interpreter with BLOCKED roots made
        unimportable. Raises ImportError carrying the child's stderr if it fails.

        A subprocess rather than a blocker installed in this one. The earlier
        version purged `sys.modules` by name before importing, because a fresh
        import that hits the cache never reaches the blocker and the check
        passes without checking. That worked, and it was only ever as good as
        the purge predicate:

          - It began by purging `ml.eval` and `ml.pipeline`, so a heavy import
            reached through any other `ml` module survived in cache. A violation
            added that way passed the full suite while failing when this file
            was run alone — the worst shape a guard can have, since the run that
            would catch it is the one nobody does.
          - Widening it to the whole `ml` tree closed that, and left the same
            hole one package boundary out: a repo-root module outside `ml` that
            imports numpy, imported earlier by some other test, still satisfies
            the import from cache. Nothing in the tree does that today — every
            numpy/cv2/torch/ultralytics import lives under `ml/` — so this was
            a latent hole rather than a live one, but it is the same hole, and
            the next widening would just move it again.

        A child process has no cache to purge and no `sys.modules` to restore,
        so the predicate disappears rather than getting another clause. It also
        removes the cleanup entirely: the old version had to put back everything
        it deleted, in the right order, or leak stale module objects into every
        test that ran after it.
        """
        source = textwrap.dedent(
            """
            import importlib
            import sys

            BLOCKED = frozenset({blocked!r})

            # Nothing blocked may already be in the child's cache: an import
            # satisfied from cache never reaches the blocker, and the check
            # would pass without checking. That is the failure the purging
            # version of this guard existed to prevent, and a fresh interpreter
            # only avoids it as long as nothing imports a blocked root during
            # startup — a site-packages .pth hook can. Asserted rather than
            # assumed, and loudly, because the symptom is a green test.
            _already = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED)
            if _already:
                raise SystemExit("blocked roots imported at startup: " + repr(_already))

            class _Blocker:
                def find_spec(self, name, path=None, target=None):
                    if name.split(".")[0] in BLOCKED:
                        raise ImportError("BLOCKED: " + name)
                    return None

            sys.meta_path.insert(0, _Blocker())
            importlib.import_module({module!r})
            """
        ).format(blocked=sorted(self.BLOCKED), module=module_name)

        # `-E` so an inherited PYTHONPATH cannot change what the child resolves;
        # `cwd` is what puts the repo root on its path. Deliberately NOT `-S`:
        # without site-packages the blocked roots are not installed in the child
        # at all, so every one of these tests would pass whether or not the
        # blocker worked — the control included, since an absent numpy and a
        # blocked numpy both end in ImportError. The child asserts its own cache
        # is clean instead, which is the actual thing `-S` was reaching for.
        proc = subprocess.run(
            [sys.executable, "-E", "-c", source],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ImportError(proc.stderr.strip())

    def test_the_blocker_can_actually_block(self):
        """The control. Without it the two tests below pass whether or not the
        blocker works, which is the failure mode they exist to rule out."""
        with pytest.raises(ImportError, match="BLOCKED: numpy"):
            self._import_under_blocker("numpy")

    def test_intervals_imports_with_nothing_installed(self):
        self._import_under_blocker("ml.pipeline.intervals")

    def test_metrics_imports_with_nothing_installed(self):
        """The one that matters. `metrics.py` is the module whose promise this
        move exists to keep, so it is the module the guard has to reach."""
        self._import_under_blocker("ml.eval.metrics")
