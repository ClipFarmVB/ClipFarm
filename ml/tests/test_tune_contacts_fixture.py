"""CF-174: the tuner can be pointed at a fixture, and its row follows it.

`tune_contacts` grew a CF-174 note telling the reader how to interpret rows "on
the 1080p fixtures", while `main()` called `load()` with no argument — so the
only fixture it could sweep was test1, at 360p, the one resolution where the
note does not apply. The advice was unreachable from the entry point.

Adding the selector alone would have been worse than leaving it: two of the
outputs were test1's, inlined. The rally denominator was the literal `126`,
and the "expect …" line reported a row recorded against test1. Pointed at test2
those print a wrong denominator and invite reading a different video's numbers
as drift, so all three move together.

The expectation is now read from the results row whose constants still match
the shipping ones, rather than written down at all: the literal it replaced was
taken at `CONTACT_RESIDUAL_MIN_PXPS = 480` against a shipping 240, so step 0
could not match and the tool called its own output untrustworthy every run
(CF-309, #359).

Behavioural rather than a source scan: the guards in this suite were converted
away from `inspect.getsource` substring checks in CF-174's own review rounds.
"""
import pytest

pytest.importorskip("numpy")

from ml.eval import tune_contacts as T  # noqa: E402
from ml.pipeline import ball as B  # noqa: E402

_ROW = dict(contacts=214, windows=40, hit=100, live=517.0,
            dead=0.684, recall=0.584, cond=0.58)


def test_the_rally_denominator_comes_from_the_fixture():
    """`126` is test1's rally count. On any other fixture it is simply wrong,
    and it was invisible while test1 was the only reachable fixture."""
    assert "/126" in T._row("x", _ROW, 126)
    assert "/45" in T._row("x", _ROW, 45)
    assert "/126" not in T._row("x", _ROW, 45)


def test_the_row_keeps_its_column_width_across_denominators():
    """The header is built with fixed widths, so a denominator of a different
    length must not shift the columns to its right."""
    assert len(T._row("x", _ROW, 126)) == len(T._row("x", _ROW, 45))


def test_main_sweeps_the_fixture_it_was_given(monkeypatch):
    seen = {}

    def _fake_load(test_id=T.DEFAULT_FIXTURE):
        seen["test_id"] = test_id
        raise _Stop()

    class _Stop(Exception):
        pass

    monkeypatch.setattr(T, "load", _fake_load)

    with pytest.raises(_Stop):
        T.main(["test2"])
    assert seen["test_id"] == "test2"

    seen.clear()
    with pytest.raises(_Stop):
        T.main([])
    assert seen["test_id"] == T.DEFAULT_FIXTURE, "no argument must still mean test1"


def test_the_expectation_is_read_from_a_row_matching_the_shipping_constants():
    """`test1_deadtime.jsonl` holds two rows. The older one is where the
    hardcoded "517s live-lost, 68.4% dead-rm, 58.4% recall" came from, and it
    carries `CONTACT_RESIDUAL_MIN_PXPS = 480` against a shipping 240 — so it
    could never match, which is CF-309 (#359). The pin must be the other one."""
    row = T._recorded_baseline("test1")
    assert row is not None
    assert row["git_commit"] == "29a83a5", "picked the row recorded at 480"
    snap = row["config_snapshot"]["ml.pipeline.ball"]
    assert snap["CONTACT_RESIDUAL_MIN_PXPS"] == B.CONTACT_RESIDUAL_MIN_PXPS

    note = T._baseline_note("test1")
    assert "68.4" not in note, "still printing the 480-era numbers"
    assert "56.2" in note


def test_a_constant_moving_under_the_row_retires_the_pin(monkeypatch):
    """The property that makes this unable to drift again, and the whole reason
    for reading the row instead of re-typing its numbers: move a constant and
    the row stops being the pin, rather than staying and being wrong."""
    assert T._recorded_baseline("test1") is not None
    monkeypatch.setattr(B, "CONTACT_HIT_SPEED_PXPS", B.CONTACT_HIT_SPEED_PXPS + 1)
    assert T._recorded_baseline("test1") is None
    assert "not pinned" in T._baseline_note("test1")


def test_a_deleted_constant_does_not_retire_every_row(monkeypatch):
    """Both committed rows snapshot `MIN_SPEED_PXPS`, which CF-174 removed. If
    a key the module no longer has counted as a mismatch, the first deletion
    would make every recorded row stale forever."""
    assert not hasattr(B, "MIN_SPEED_PXPS")
    snap = T._recorded_baseline("test1")["config_snapshot"]["ml.pipeline.ball"]
    assert "MIN_SPEED_PXPS" in snap


def test_the_newest_matching_row_wins(tmp_path, monkeypatch):
    """Rows are appended, so a later re-recording must supersede an earlier one.

    Undistinguishable against the committed data — only one row there matches
    the shipping constants, so "first match" and "last match" agree — which is
    why this builds a file where two do. Without it the docstring's "newest"
    would be an untested word.
    """
    import json

    def _row(commit, extra):
        return json.dumps({
            "git_commit": commit, "version_tag": commit,
            "config_snapshot": {"ml.pipeline.ball": {
                "CONTACT_HIT_SPEED_PXPS": B.CONTACT_HIT_SPEED_PXPS}},
            "deadtime": {"live_removed_sec": extra, "dead_removed_pct": 0.5,
                         "kept_play_pct": 0.5},
        })

    (tmp_path / "twin_deadtime.jsonl").write_text(
        _row("older", 111.0) + "\n" + _row("newer", 222.0) + "\n", encoding="utf-8")
    monkeypatch.setattr(T, "RESULTS_DIR", tmp_path)

    assert T._recorded_baseline("twin")["git_commit"] == "newer"
    assert "222s" in T._baseline_note("twin")


def test_a_fixture_with_no_matching_row_is_not_pinned():
    assert T._recorded_baseline("nosuchfixture") is None
    assert "not pinned" in T._baseline_note("nosuchfixture")


def test_main_restores_the_global_logging_level():
    """`main()` disables INFO for the sweep's duration. It takes an argument
    now, so it is callable rather than only a `__main__` entry point, and a
    global it never puts back leaks into the caller — in this suite, into every
    test that runs after it."""
    import logging

    class _Stop(Exception):
        pass

    def _fake_load(test_id=T.DEFAULT_FIXTURE):
        raise _Stop()

    # Set the level explicitly first. Reading it as "before" is not enough: an
    # earlier test in this file also calls main(), so without the restore the
    # level is already disabled by the time this runs and the assertion holds
    # either way. That is how the first version of this test passed against the
    # unrestored build.
    logging.disable(logging.NOTSET)
    T_load = T.load
    T.load = _fake_load
    try:
        with pytest.raises(_Stop):
            T.main([])
    finally:
        T.load = T_load
    assert logging.root.manager.disable == logging.NOTSET
