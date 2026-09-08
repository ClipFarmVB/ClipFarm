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
import sys

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


def test_the_newest_matching_row_wins(tmp_path, monkeypatch):
    """Rows are appended, so a later re-recording must supersede an earlier one.

    Undistinguishable against the committed data — only one row there matches
    the shipping constants, so "first match" and "last match" agree — which is
    why this builds a file where two do. Without it the docstring's "newest"
    would be an untested word.
    """
    _write_rows(tmp_path, monkeypatch,
                _good_row("older"), _good_row("newer"))
    assert T._last_recorded_run("probe")["git_commit"] == "newer"


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


# ── the wiring, not just the helpers ────────────────────────────────────────

def _synthetic(monkeypatch, rallies):
    """A track short enough to sweep in milliseconds. It finds no contacts, and
    that is fine: these tests are about which numbers `_sweep` puts where, not
    about detection."""
    from ml.eval.harness import DeadFixture

    pos = [{"time": i * 0.1, "x": float(100 + (i % 20) * 15),
            "y": float(200 + (30 if (i // 20) % 2 else -30))} for i in range(200)]
    track = B.TrackedBall(positions=[
        B.BallPosition(frame=i, time=p["time"], x=p["x"], y=p["y"], confidence=1.0)
        for i, p in enumerate(pos)])
    fx = DeadFixture(test_id="synthetic", keep=rallies, duration=20.0, raw={})
    monkeypatch.setattr(T, "load", lambda test_id="synthetic": (track, pos, 360, fx))


def _run(monkeypatch, rallies, capsys, fixture="synthetic"):
    _synthetic(monkeypatch, rallies)
    T.main([fixture])
    return capsys.readouterr().out


def test_the_printed_denominator_comes_from_the_fixture(monkeypatch, capsys):
    """The helper tests pin `_row`; this pins the call. Reverting
    `_row(label, r, len(rallies))` to the old hardcoded `126` leaves every
    helper test green, because none of them runs `_sweep` at all."""
    out = _run(monkeypatch, [(1.0, 5.0), (8.0, 12.0)], capsys)
    assert "/2 " in out, out
    assert "/126" not in out


def test_the_printed_note_comes_from_the_helper(monkeypatch, capsys):
    """Likewise for `print(_baseline_note(test_id))`: with the old literal
    hardcoded back in, the helper tests still pass. The synthetic fixture has no
    results file, so the helper's no-run branch is what must appear."""
    out = _run(monkeypatch, [(1.0, 5.0)], capsys)
    assert "214 contacts" not in out
    assert "no recorded run for synthetic" in out, out


# ── what makes a recorded row usable as a pin ───────────────────────────────

def _write_rows(tmp_path, monkeypatch, *rows):
    import json
    (tmp_path / "probe_deadtime.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(T, "RESULTS_DIR", tmp_path)


def _good_row(commit="good"):
    return {"git_commit": commit, "version_tag": commit,
            "deadtime": {"live_removed_sec": 1.0, "dead_removed_pct": 0.5,
                         "kept_play_pct": 0.5}}


def test_a_truncated_line_does_not_lose_the_pin(tmp_path, monkeypatch):
    """Rows are appended, so a crashed write leaves a partial last line."""
    (tmp_path / "probe_deadtime.jsonl").write_text(
        __import__("json").dumps(_good_row()) + "\n{\"git_commit\": \"trunc",
        encoding="utf-8")
    monkeypatch.setattr(T, "RESULTS_DIR", tmp_path)
    assert T._last_recorded_run("probe")["git_commit"] == "good"


def test_the_cli_reads_its_fixture_from_argv(monkeypatch):
    """`main()` with no argument is the documented `python -m ml.eval.tune_contacts`
    path, and it must still take a fixture from the command line. Replacing the
    parse with `argv or []` leaves every other test green, because they all pass
    argv explicitly."""
    seen = {}

    class _Stop(Exception):
        pass

    def _fake_load(test_id=T.DEFAULT_FIXTURE):
        seen["test_id"] = test_id
        raise _Stop()

    monkeypatch.setattr(T, "load", _fake_load)
    monkeypatch.setattr(sys, "argv", ["tune_contacts", "test4"])
    with pytest.raises(_Stop):
        T.main()
    assert seen["test_id"] == "test4"

    monkeypatch.setattr(sys, "argv", ["tune_contacts"])
    with pytest.raises(_Stop):
        T.main()
    assert seen["test_id"] == T.DEFAULT_FIXTURE


def test_a_recorded_run_is_reported_without_claiming_it_matches():
    """The note is context, not a pin: an earlier version filtered rows by a
    config-match predicate that four rounds each found another hole in. It must
    not read as pass/fail."""
    note = T._baseline_note("test1")
    assert "last recorded run" in note
    assert "NOT a pass/fail check" in note
    assert "expect" not in note


def test_a_row_without_metrics_is_reported_as_such(tmp_path, monkeypatch):
    import json
    (tmp_path / "probe_deadtime.jsonl").write_text(
        json.dumps({"git_commit": "x", "deadtime": {}}) + "\n", encoding="utf-8")
    monkeypatch.setattr(T, "RESULTS_DIR", tmp_path)
    assert "no dead-time metrics" in T._baseline_note("probe")
