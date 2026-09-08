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
    _write_rows(tmp_path, monkeypatch,
                _good_row("older"), _good_row("newer"))
    assert T._recorded_baseline("probe")["git_commit"] == "newer"


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


def test_the_printed_pin_comes_from_the_recorded_row(monkeypatch, capsys):
    """Likewise for `print(_baseline_note(test_id))`: with the old literal
    hardcoded back in, the helper tests still pass."""
    out = _run(monkeypatch, [(1.0, 5.0)], capsys)
    assert "214 contacts" not in out
    assert "not pinned" in out, out


# ── what makes a recorded row usable as a pin ───────────────────────────────

def _write_rows(tmp_path, monkeypatch, *rows):
    import json
    (tmp_path / "probe_deadtime.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(T, "RESULTS_DIR", tmp_path)


def _good_row(commit="good", **overrides):
    ball = {k: getattr(B, k) for k in T.TUNABLES}
    app = dict(T._MIRRORED)
    ball.update(overrides.pop("ball", {}))
    app.update(overrides.pop("app", {}))
    return {"git_commit": commit, "version_tag": commit,
            "config_snapshot": {"ml.pipeline.ball": ball, "app.config": app},
            "deadtime": {"live_removed_sec": 1.0, "dead_removed_pct": 0.5,
                         "kept_play_pct": 0.5}}


def test_a_row_with_no_ball_snapshot_is_not_a_pin(tmp_path, monkeypatch):
    """`all()` over an empty mapping is True, so a row carrying no snapshot
    would match unconditionally — and being newest, supersede every verified
    row and print its figures as authoritative. `harness.config_snapshot`
    produces exactly that shape when an import fails, so it needs no
    hand-editing to occur. A wrong pin is worse than none: step 0's rule tells
    the operator to distrust everything below an unmatched baseline."""
    bogus = {"git_commit": "NOSNAPSHOT", "version_tag": "bogus",
             "config_snapshot": {"app.config": {}},
             "deadtime": {"live_removed_sec": 9999.0, "dead_removed_pct": 0.99,
                          "kept_play_pct": 0.01}}
    _write_rows(tmp_path, monkeypatch, _good_row(), bogus)
    assert T._recorded_baseline("probe")["git_commit"] == "good"
    assert "9999" not in T._baseline_note("probe")


def test_a_row_missing_one_swept_constant_is_not_a_pin(tmp_path, monkeypatch):
    """A snapshot too thin to say anything about what was swept is not a pin
    either — the vacuous-match hole one step narrower."""
    thin = _good_row("thin")
    del thin["config_snapshot"]["ml.pipeline.ball"][T.TUNABLES[0]]
    _write_rows(tmp_path, monkeypatch, thin)
    assert T._recorded_baseline("probe") is None


def test_a_condense_knob_moving_retires_the_row(tmp_path, monkeypatch):
    """Every figure in the row depends on the condense settings too — harness.py
    says they decide every dead-time number (CF-98) — and this tool mirrors them
    in COND because it runs with no app installed. Matching on ball constants
    alone would let the pin survive a knob it actually depends on."""
    _write_rows(tmp_path, monkeypatch,
                _good_row("drifted", app={"condense_pad_before": T.COND["pad_before"] + 1}))
    assert T._recorded_baseline("probe") is None


def test_a_truncated_line_does_not_lose_the_pin(tmp_path, monkeypatch):
    """Rows are appended, so a crashed write leaves a partial last line."""
    (tmp_path / "probe_deadtime.jsonl").write_text(
        __import__("json").dumps(_good_row()) + "\n{\"git_commit\": \"trunc",
        encoding="utf-8")
    monkeypatch.setattr(T, "RESULTS_DIR", tmp_path)
    assert T._recorded_baseline("probe")["git_commit"] == "good"
