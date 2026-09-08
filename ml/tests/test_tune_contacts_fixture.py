"""CF-174: the tuner can be pointed at a fixture, and its row follows it.

`tune_contacts` grew a CF-174 note telling the reader how to interpret rows "on
the 1080p fixtures", while `main()` called `load()` with no argument — so the
only fixture it could sweep was test1, at 360p, the one resolution where the
note does not apply. The advice was unreachable from the entry point.

Adding the selector alone would have been worse than leaving it: two of the
outputs were test1's, inlined. The rally denominator was the literal `126`,
and the "expect 214 contacts…" line pins a row recorded against test1. Pointed
at test2 those print a wrong denominator and invite reading a different video's
numbers as drift, so all three move together and are pinned together here.

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
