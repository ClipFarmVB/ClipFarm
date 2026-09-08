"""CF-174: the tuner can be pointed at a fixture, and its row follows it.

`tune_contacts` grew a CF-174 note telling the reader how to interpret rows "on
the 1080p fixtures", while `main()` called `load()` with no argument — so the
only fixture it could sweep was test1, at 360p, the one resolution where the
note does not apply. The advice was unreachable from the entry point.

Adding the selector alone would have been worse than leaving it: two of the
outputs were test1's, inlined. The rally denominator was the literal `126`,
and the "expect 214 contacts…" line reports a row recorded against test1.
Pointed at test2 those print a wrong denominator and invite reading a different
video's numbers as drift, so all three move together — and all three are pinned
here, one test each. An earlier version of this docstring claimed that while
the third had no test at all.

Behavioural rather than a source scan: the guards in this suite were converted
away from `inspect.getsource` substring checks in CF-174's own review rounds.
"""
import pytest

pytest.importorskip("numpy")

from ml.eval import tune_contacts as T  # noqa: E402

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


def test_only_the_fixture_with_a_recorded_row_gets_an_expectation():
    """The third moved output. test1 has a recorded step-0 row; nothing else
    does, and printing test1's numbers under another video invites reading a
    different fixture as drift."""
    assert "214 contacts" in T._baseline_note(T.DEFAULT_FIXTURE)
    other = T._baseline_note("test2")
    assert "214 contacts" not in other
    assert "not pinned" in other


def test_the_recorded_row_is_labelled_stale():
    """It cannot reproduce even on test1: it was taken at
    CONTACT_RESIDUAL_MIN_PXPS=480 and CF-103 shipped 240, which is what CF-309
    (#359) is open for. Presenting it as a clean control is the failure — the
    tool's own docstring says nothing below step 0 is trustworthy if it does
    not match, so an unlabelled stale pin stops every sweep on a false alarm."""
    note = T._baseline_note(T.DEFAULT_FIXTURE)
    assert "STALE" in note
    assert "480" in note and "240" in note
    assert "#359" in note


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
