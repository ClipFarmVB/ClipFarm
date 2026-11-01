"""
Tests for the AI labeller tooling (CF-594): the draft writer's invariants,
and frame / contact-sheet extraction on a synthetic ffmpeg clip.

The writer is the part that matters: it is the only path by which an AI label
pass becomes a file in the fixture format, so it is where "AI labels must
never be passed off as human ones" is enforced. The ffmpeg tests assert the
sidecars' timestamps, not burnt-in text — the sidecar is the record, the text
a reading aid that depends on drawtext and a font being present.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.eval import ai_label, harness
from ml.eval.ai_label import (
    DRAFTS_DIR,
    FIXTURES_DIR,
    LABELER,
    LABELING,
    REVIEW_BELOW,
    DraftSpan,
    _fmt_ts,
    build_draft,
    content_md5,
    find_video_by_md5,
    load_spans_file,
    score_draft,
    validate_spans,
    write_draft,
)
from ml.eval.harness import load_deadtime_fixture, parse_timestamp

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


def _spans():
    return [
        DraftSpan(10.0, 25.0, 0.9, note="rally"),
        DraftSpan(40.0, 52.5, 0.95),
        DraftSpan(60.0, 61.0, 0.5, note="serve into net?"),
    ]


def _draft(spans=None, **overrides):
    kwargs = dict(
        duration_sec=120.0,
        source_video_md5="0" * 32,
        source_frame_height=1080,
        source_video_file="game.mp4",
        labeled_at="2026-11-01",
        model="claude-test",
    )
    kwargs.update(overrides)
    return build_draft("draftgame", spans if spans is not None else _spans(), **kwargs)


class TestTimestampFormat:
    @pytest.mark.parametrize("seconds, text", [
        (0.0, "00:00"), (65.0, "01:05"), (90.5, "01:30.5"), (3599.0, "59:59"),
        (3600.0, "1:00:00"), (3616.25, "1:00:16.2"), (7325.0, "2:02:05"),
    ])
    def test_writes_the_fixture_form(self, seconds, text):
        assert _fmt_ts(seconds) == text

    @pytest.mark.parametrize("seconds", [0.0, 1.5, 59.9, 60.0, 754.3, 3600.0, 4000.5])
    def test_round_trips_through_the_harness_parser(self, seconds):
        assert parse_timestamp(_fmt_ts(seconds)) == pytest.approx(seconds, abs=0.05)


class TestBuildDraft:
    def test_is_always_stamped_as_an_ai_draft(self):
        d = _draft()
        assert d["labeler"] == LABELER == "claude-vision"
        assert d["labeling"] == LABELING == "ai-draft"
        assert "NOT GROUND TRUTH" in d["note"]
        assert "source_r2_key" not in d

    def test_carries_the_video_pins_the_fixture_suite_requires(self):
        d = _draft()
        assert len(d["source_video_md5"]) == 32
        assert d["source_frame_height"] == 1080
        assert d["video_duration_sec"] == 120.0
        assert d["source_video_file"] == "game.mp4"
        assert d["model"] == "claude-test"
        assert d["labeled_at"] == "2026-11-01"

    def test_is_tier_less(self):
        d = _draft()
        assert "keep_tiers" not in d
        assert all("tier" not in s for s in d["spans"])

    def test_every_span_records_a_confidence_in_the_fixture_timestamp_form(self):
        d = _draft()
        assert [(s["start"], s["end"], s["confidence"]) for s in d["spans"]] == [
            ("00:10", "00:25", 0.9), ("00:40", "00:52.5", 0.95), ("01:00", "01:01", 0.5),
        ]
        assert d["spans"][0]["note"] == "rally"
        assert "note" not in d["spans"][1]

    def test_low_confidence_forces_review_and_is_counted(self):
        d = _draft()
        assert [s["review"] for s in d["spans"]] == [False, False, True]
        assert d["review_count"] == 1
        assert d["review_below"] == REVIEW_BELOW

    def test_a_caller_can_flag_a_confident_span_but_not_unflag_an_unsure_one(self):
        spans = [DraftSpan(1.0, 2.0, 0.99, review=True), DraftSpan(3.0, 4.0, 0.2, review=False)]
        d = _draft(spans)
        assert [s["review"] for s in d["spans"]] == [True, True]
        assert d["review_count"] == 2

    def test_the_threshold_boundary_is_strict(self):
        d = _draft([DraftSpan(1.0, 2.0, REVIEW_BELOW)])
        assert d["spans"][0]["review"] is False

    def test_labeled_at_defaults_to_a_date(self):
        d = _draft(labeled_at=None)
        assert len(d["labeled_at"]) == 10 and d["labeled_at"][4] == "-"


class TestValidateSpans:
    @pytest.mark.parametrize("spans, message", [
        ([DraftSpan(10.0, 5.0, 0.9)], "end is not after start"),
        ([DraftSpan(10.0, 10.0, 0.9)], "end is not after start"),
        ([DraftSpan(-1.0, 5.0, 0.9)], "starts before 0"),
        ([DraftSpan(10.0, 121.0, 0.9)], "ends past the video"),
        ([DraftSpan(10.0, 20.0, 0.9), DraftSpan(15.0, 30.0, 0.9)], "overlaps"),
        ([DraftSpan(30.0, 40.0, 0.9), DraftSpan(10.0, 20.0, 0.9)], "overlaps or precedes"),
        ([DraftSpan(10.0, 20.0, 1.5)], "confidence .* is not in"),
        ([DraftSpan(10.0, 20.0, -0.1)], "confidence .* is not in"),
        ([DraftSpan(10.0, float("nan"), 0.9)], "non-finite"),
        ([], "at least one span"),
    ])
    def test_rejects_malformed_spans(self, spans, message):
        with pytest.raises(ValueError, match=message):
            validate_spans(spans, 120.0)

    def test_rejects_a_non_positive_duration(self):
        with pytest.raises(ValueError, match="duration"):
            validate_spans(_spans(), 0.0)

    def test_adjacent_spans_and_a_span_ending_at_the_duration_are_fine(self):
        validate_spans([DraftSpan(0.0, 10.0, 0.9), DraftSpan(10.0, 120.0, 0.9)], 120.0)


class TestWriteDraft:
    def test_refuses_the_fixtures_directory_itself(self, tmp_path):
        with pytest.raises(ValueError, match="refusing to write into"):
            write_draft(FIXTURES_DIR / "draftgame_deadtime.json", _draft())
        assert not (FIXTURES_DIR / "draftgame_deadtime.json").exists()

    def test_refuses_a_draft_whose_stamp_was_edited(self, tmp_path):
        d = _draft()
        d["labeling"] = "independent"
        with pytest.raises(ValueError, match="not an AI draft"):
            write_draft(tmp_path / "x_deadtime.json", d)
        d = _draft()
        d["labeler"] = "Owen"
        with pytest.raises(ValueError, match="not an AI draft"):
            write_draft(tmp_path / "x_deadtime.json", d)

    def test_refuses_to_overwrite_a_file_that_is_not_an_ai_draft(self, tmp_path):
        target = tmp_path / "drafts" / "human_deadtime.json"
        target.parent.mkdir()
        target.write_text(json.dumps({"labeling": "independent", "spans": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="refusing to overwrite"):
            write_draft(target, _draft())
        assert json.loads(target.read_text(encoding="utf-8"))["labeling"] == "independent"

    def test_overwrites_an_earlier_draft(self, tmp_path):
        target = tmp_path / "drafts" / "draftgame_deadtime.json"
        write_draft(target, _draft())
        write_draft(target, _draft([DraftSpan(1.0, 2.0, 0.9)]))
        assert len(json.loads(target.read_text(encoding="utf-8"))["spans"]) == 1

    def test_round_trips_through_the_harness_loader(self, tmp_path, monkeypatch):
        """What the condense harness would read if a reviewed draft were
        promoted: every span in-play, duration from the file."""
        drafts = tmp_path / "drafts"
        write_draft(drafts / "draftgame_deadtime.json", _draft())
        monkeypatch.setattr(harness, "FIXTURES_DIR", drafts)
        fx = load_deadtime_fixture("draftgame")
        assert fx.test_id == "draftgame"
        assert fx.duration == 120.0
        assert fx.keep == [(10.0, 25.0), (40.0, 52.5), (60.0, 61.0)]
        assert fx.raw["labeling"] == "ai-draft"


class TestDraftsStayOutOfTheFixtureSuite:
    def test_drafts_dir_is_a_child_of_fixtures_dir(self):
        assert DRAFTS_DIR.parent == FIXTURES_DIR
        assert FIXTURES_DIR == harness.FIXTURES_DIR

    def test_no_ai_draft_sits_among_the_fixtures(self):
        """The pinning suite globs fixtures/*_deadtime.json non-recursively and
        treats every hit as ground truth. A draft at that level would score as
        a human label pass."""
        for path in FIXTURES_DIR.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data.get("labeling") != LABELING, f"{path.name} is an AI draft at fixture level"
            assert data.get("labeler") != LABELER, f"{path.name} names the AI labeller"

    def test_the_fixture_glob_does_not_descend_into_drafts(self, tmp_path):
        (tmp_path / "drafts").mkdir()
        (tmp_path / "drafts" / "x_deadtime.json").write_text("{}", encoding="utf-8")
        (tmp_path / "real_deadtime.json").write_text("{}", encoding="utf-8")
        assert [p.name for p in tmp_path.glob("*_deadtime.json")] == ["real_deadtime.json"]


class TestLoadSpansFile:
    def test_reads_mixed_timestamp_forms(self, tmp_path):
        path = tmp_path / "spans.json"
        path.write_text(json.dumps([
            {"start": "1:30", "end": 112, "confidence": 0.8, "note": "x"},
            {"start": "0:02:00", "end": "2:05.5", "confidence": "0.4", "review": True},
        ]), encoding="utf-8")
        spans = load_spans_file(path)
        assert spans[0] == DraftSpan(90.0, 112.0, 0.8, note="x", review=None)
        assert spans[1] == DraftSpan(120.0, 125.5, 0.4, note="", review=True)

    def test_accepts_the_spans_key_wrapper(self, tmp_path):
        path = tmp_path / "spans.json"
        path.write_text(json.dumps({"spans": [{"start": 1, "end": 2, "confidence": 1}]}), encoding="utf-8")
        assert len(load_spans_file(path)) == 1

    def test_a_span_without_a_confidence_is_refused(self, tmp_path):
        path = tmp_path / "spans.json"
        path.write_text(json.dumps([{"start": 1, "end": 2}]), encoding="utf-8")
        with pytest.raises(ValueError, match="confidence"):
            load_spans_file(path)


class TestScoreDraft:
    def _human(self, tmp_path, monkeypatch):
        human = {
            "test_id": "scoreme",
            "labeler": "A Person",
            "labeling": "independent",
            "source_video_md5": "0" * 32,
            "video_duration_sec": 120.0,
            "spans": [{"start": "00:10", "end": "00:25"}, {"start": "00:40", "end": "00:52.5"},
                      {"start": "01:00", "end": "01:01"}],
        }
        (tmp_path / "scoreme_deadtime.json").write_text(json.dumps(human), encoding="utf-8")
        monkeypatch.setattr(harness, "FIXTURES_DIR", tmp_path)

    def test_a_perfect_draft_scores_no_divergence(self, tmp_path, monkeypatch, capsys):
        self._human(tmp_path, monkeypatch)
        draft = write_draft(tmp_path / "drafts" / "scoreme_deadtime.json", _draft())
        s = score_draft(draft, "scoreme")
        assert s.live_removed_sec == 0.0
        assert s.dead_removed_pct == pytest.approx(1.0)
        assert s.over_cut_live == [] and s.missed_dead == []
        out = capsys.readouterr().out
        assert "3 human spans, 3 draft spans, 1 flagged" in out
        assert "WARNING" not in out

    def test_a_missed_rally_shows_as_live_removed(self, tmp_path, monkeypatch):
        self._human(tmp_path, monkeypatch)
        spans = [DraftSpan(10.0, 25.0, 0.9), DraftSpan(60.0, 61.0, 0.9)]
        draft = write_draft(tmp_path / "drafts" / "scoreme_deadtime.json", _draft(spans))
        s = score_draft(draft, "scoreme")
        assert s.live_removed_sec == pytest.approx(12.5)
        assert s.over_cut_live == [(40.0, 52.5)]

    def test_a_different_source_file_is_warned_about(self, tmp_path, monkeypatch, capsys):
        self._human(tmp_path, monkeypatch)
        draft = write_draft(tmp_path / "drafts" / "scoreme_deadtime.json", _draft(source_video_md5="f" * 32))
        score_draft(draft, "scoreme")
        assert "WARNING: draft md5" in capsys.readouterr().out

    def test_refuses_to_score_a_file_that_is_not_a_draft(self, tmp_path, monkeypatch):
        self._human(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="not an AI draft"):
            score_draft(tmp_path / "scoreme_deadtime.json", "scoreme")


# ── ffmpeg-backed ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def synthetic_clip(tmp_path_factory):
    """12 s of testsrc at 10 fps, 320x180 — small enough to decode in a blink."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    path = tmp_path_factory.mktemp("clip") / "synthetic game.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=12:size=320x180:rate=10",
        "-pix_fmt", "yuv420p", str(path),
    ], check=True, timeout=120)
    return path


@needs_ffmpeg
class TestProbeAndHash:
    def test_probe_reads_duration_size_and_fps(self, synthetic_clip):
        info = ai_label.probe(synthetic_clip)
        assert info.duration_sec == pytest.approx(12.0, abs=0.2)
        assert (info.width, info.height) == (320, 180)
        assert info.fps == pytest.approx(10.0)

    def test_content_md5_is_the_streamed_file_hash(self, synthetic_clip):
        assert content_md5(synthetic_clip) == hashlib.md5(synthetic_clip.read_bytes()).hexdigest()

    def test_find_video_by_md5_matches_content_not_name(self, synthetic_clip, tmp_path):
        copy = tmp_path / "renamed.MP4"
        shutil.copy(synthetic_clip, copy)
        (tmp_path / "other.mp4").write_bytes(b"not the same bytes")
        assert find_video_by_md5(content_md5(synthetic_clip).upper(), tmp_path) == copy
        assert find_video_by_md5("0" * 32, tmp_path) is None

    def test_draft_from_video_pins_the_file(self, synthetic_clip):
        d = ai_label.draft_from_video("synth", synthetic_clip, [DraftSpan(1.0, 4.0, 0.9)], labeled_at="2026-11-01")
        assert d["source_video_md5"] == content_md5(synthetic_clip)
        assert d["source_frame_height"] == 180
        assert d["video_duration_sec"] == pytest.approx(12.0, abs=0.2)
        assert d["source_video_file"] == "synthetic game.mp4"


@needs_ffmpeg
class TestExtractFrames:
    def test_one_frame_per_second_with_a_timestamp_sidecar(self, synthetic_clip, tmp_path):
        frames = ai_label.extract_frames(synthetic_clip, tmp_path / "1fps", every_sec=1.0, height=90)
        assert [f.sec for f in frames] == [float(i) for i in range(12)]
        assert all(f.path.exists() and f.path.stat().st_size > 0 for f in frames)
        assert frames[11].path.name == "frame_00011.0s.jpg"
        sidecar = json.loads((tmp_path / "1fps" / "frames.json").read_text(encoding="utf-8"))
        assert [(x["file"], x["sec"]) for x in sidecar["frames"]] == [(f.path.name, f.sec) for f in frames]
        assert sidecar["every_sec"] == 1.0 and sidecar["height"] == 90

    def test_a_range_offsets_the_timestamps(self, synthetic_clip, tmp_path):
        frames = ai_label.extract_frames(synthetic_clip, tmp_path / "r", every_sec=1.0, start=5.0, end=9.0, height=90)
        assert [f.sec for f in frames] == [5.0, 6.0, 7.0, 8.0]

    def test_dense_frames_around_a_transition(self, synthetic_clip, tmp_path):
        frames = ai_label.dense_frames(synthetic_clip, 6.0, tmp_path / "d", radius=2.0, every_sec=0.5, height=90)
        assert [f.sec for f in frames] == [4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5]
        assert frames[1].path.name == "frame_00004.5s.jpg"

    def test_dense_frames_clamp_at_the_start_of_the_video(self, synthetic_clip, tmp_path):
        frames = ai_label.dense_frames(synthetic_clip, 1.0, tmp_path / "d0", radius=2.0, every_sec=1.0, height=90)
        assert [f.sec for f in frames] == [0.0, 1.0, 2.0]

    def test_a_rerun_replaces_rather_than_appends(self, synthetic_clip, tmp_path):
        ai_label.extract_frames(synthetic_clip, tmp_path / "x", every_sec=1.0, start=0.0, end=3.0, height=90)
        frames = ai_label.extract_frames(synthetic_clip, tmp_path / "x", every_sec=1.0, start=0.0, end=2.0, height=90)
        assert [f.sec for f in frames] == [0.0, 1.0]
        assert not list((tmp_path / "x").glob("_raw_*"))

    @pytest.mark.parametrize("kwargs", [{"every_sec": 0.0}, {"start": 5.0, "end": 5.0}])
    def test_rejects_an_empty_sampling(self, synthetic_clip, tmp_path, kwargs):
        with pytest.raises(ValueError):
            ai_label.extract_frames(synthetic_clip, tmp_path / "bad", **kwargs)


@needs_ffmpeg
class TestContactSheets:
    def test_tiles_map_to_timestamps_through_the_sidecar(self, synthetic_clip, tmp_path):
        sheets = ai_label.contact_sheets(synthetic_clip, tmp_path / "s", every_sec=1.0, cols=3, rows=2, tile_height=90)
        assert [s.name for s in sheets] == ["sheet_001.jpg", "sheet_002.jpg"]
        assert all(s.stat().st_size > 0 for s in sheets)
        sidecar = json.loads((tmp_path / "s" / "sheets.json").read_text(encoding="utf-8"))
        assert sidecar["seconds_per_sheet"] == 6.0
        assert isinstance(sidecar["burnt_in_timestamps"], bool)
        first, second = sidecar["sheets"]
        assert (first["file"], first["first_sec"], first["last_sec"]) == ("sheet_001.jpg", 0.0, 5.0)
        assert (second["file"], second["first_sec"], second["last_sec"]) == ("sheet_002.jpg", 6.0, 11.0)
        assert [(t["row"], t["col"], t["sec"]) for t in second["tiles"]] == [
            (0, 0, 6.0), (0, 1, 7.0), (0, 2, 8.0), (1, 0, 9.0), (1, 1, 10.0), (1, 2, 11.0),
        ]
        assert second["tiles"][3]["ts"] == "00:09"

    def test_a_partial_last_sheet_lists_only_real_tiles(self, synthetic_clip, tmp_path):
        ai_label.contact_sheets(synthetic_clip, tmp_path / "p", every_sec=1.0, cols=5, rows=2, tile_height=90)
        sidecar = json.loads((tmp_path / "p" / "sheets.json").read_text(encoding="utf-8"))
        assert [len(s["tiles"]) for s in sidecar["sheets"]] == [10, 2]
        assert sidecar["sheets"][1]["tiles"][-1]["sec"] == 11.0

    def test_a_range_offsets_every_tile(self, synthetic_clip, tmp_path):
        ai_label.contact_sheets(synthetic_clip, tmp_path / "r", every_sec=2.0, cols=2, rows=2, tile_height=90,
                                start=3.0, end=11.0)
        sidecar = json.loads((tmp_path / "r" / "sheets.json").read_text(encoding="utf-8"))
        assert [t["sec"] for s in sidecar["sheets"] for t in s["tiles"]] == [3.0, 5.0, 7.0, 9.0]

    def test_rejects_a_range_outside_the_video(self, synthetic_clip, tmp_path):
        with pytest.raises(ValueError):
            ai_label.contact_sheets(synthetic_clip, tmp_path / "bad", start=20.0)
