"""Unit tests for the shared interval primitives (ml/pipeline/intervals.py).

Split out of test_dead_time.py by CF-95. They import from ml.pipeline.intervals
directly rather than through dead_time, which still re-exports the names for its
own use — a test that reached through the re-export would pass even if the move
had not happened.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.pipeline.intervals import merge_intervals


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
        """Import `module_name` fresh with BLOCKED roots made unimportable."""
        import importlib

        class _Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in TestTheImportStaysLight.BLOCKED:
                    raise ImportError(f"BLOCKED: {name}")
                return None

        # Drop anything already imported, or the fresh import is a cache hit and
        # the blocker never runs — the check would pass without checking.
        saved = {
            k: v
            for k, v in sys.modules.items()
            if k.split(".")[0] in self.BLOCKED
            or k.startswith(("ml.eval", "ml.pipeline"))
        }
        for k in saved:
            del sys.modules[k]

        blocker = _Blocker()
        sys.meta_path.insert(0, blocker)
        try:
            importlib.import_module(module_name)
        finally:
            sys.meta_path.remove(blocker)
            for k in list(sys.modules):
                if k.startswith(("ml.eval", "ml.pipeline")):
                    del sys.modules[k]
            sys.modules.update(saved)

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
