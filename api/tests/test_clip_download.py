"""CF-100: per-clip download filenames, and the endpoint that mints them.

Two halves. `services/filenames.py` is pure string work and is tested directly.
The endpoint is exercised by calling the router coroutine with a fake session —
the house pattern here — because what matters is that it reuses /share's
authorization and threads a name through to the presigner, neither of which
needs a database or R2.
"""
import asyncio
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from app.services.filenames import (  # noqa: E402
    MAX_COMPONENT_CHARS,
    MAX_FILENAME_CHARS,
    clip_download_filename,
    condensed_download_filename,
    format_timestamp,
    sanitize_component,
)


class TestSanitizeComponent:
    def test_plain_text_survives(self):
        assert sanitize_component("Panthers vs Sharks") == "Panthers vs Sharks"

    def test_none_and_empty(self):
        assert sanitize_component(None) == ""
        assert sanitize_component("") == ""

    @pytest.mark.parametrize("ch", list('";\\/:*?<>|'))
    def test_every_forbidden_character_is_dropped(self, ch):
        """The header-hostile set and the Windows-illegal set, one by one.

        Parametrised rather than asserted over one string so a regression names
        the character that got through.
        """
        assert ch not in sanitize_component(f"a{ch}b")

    def test_quote_cannot_escape_the_header(self):
        # The value lands inside filename="..." — a surviving quote would end
        # the quoted string and let the rest be read as header parameters.
        assert '"' not in sanitize_component('Match" ; attachment')

    def test_non_ascii_is_dropped_not_mangled(self):
        assert sanitize_component("Мatch") == "atch"

    def test_a_fully_non_ascii_component_becomes_empty(self):
        # The caller has to cope with this rather than assume every component
        # survives — clip_download_filename drops empties.
        assert sanitize_component("Матч") == ""

    def test_whitespace_is_collapsed(self):
        assert sanitize_component("Semi\t\tFinal  2") == "Semi Final 2"

    def test_long_values_are_capped(self):
        assert len(sanitize_component("x" * 500)) == MAX_COMPONENT_CHARS


class TestFormatTimestamp:
    def test_uses_a_dash_not_a_colon(self):
        # A colon is illegal in a Windows filename; this is the whole reason
        # the scheme does not read mm:ss.
        assert ":" not in format_timestamp(252.0)

    def test_minutes_and_seconds(self):
        assert format_timestamp(0) == "0-00-00"
        assert format_timestamp(9.9) == "0-00-09"
        assert format_timestamp(252.0) == "0-04-12"

    def test_rolls_into_hours_like_the_ui_does(self):
        """Uploads are capped at four hours.

        Without the rollover a late clip reads `239-59` where ClipCard and
        ClipModal both show `3:59:59`, and a reader has to divide to find the
        moment in the game.
        """
        assert format_timestamp(3661) == "1-01-01"
        assert format_timestamp(3599) == "0-59-59"
        assert format_timestamp(3600) == "1-00-00"
        assert format_timestamp(14399) == "3-59-59"

    def test_negative_clamps_to_zero(self):
        assert format_timestamp(-5) == "0-00-00"


class TestClipDownloadFilename:
    def test_all_components(self):
        assert clip_download_filename("Match", "spike", "Rosa", 252.0) == (
            "Match - spike - Rosa - 0-04-12.mp4"
        )

    def test_missing_player_leaves_no_empty_separator(self):
        assert clip_download_filename("Match", "spike", None, 252.0) == (
            "Match - spike - 0-04-12.mp4"
        )

    def test_everything_stripped_still_yields_a_usable_name(self):
        # A non-Latin title and player with no action: the timestamp is what
        # survives, and the name must still end .mp4 rather than being bare.
        out = clip_download_filename("Матч", None, "Ирина", 65)
        assert out == "0-01-05.mp4"

    def test_always_ends_with_the_extension(self):
        assert clip_download_filename(None, None, None, 0).endswith(".mp4")

    def test_path_characters_cannot_traverse(self):
        out = clip_download_filename("../../etc/passwd", "spike", None, 0)
        assert "/" not in out and ".." not in out.replace(".mp4", "")

    def test_a_header_injection_attempt_is_neutralised(self):
        out = clip_download_filename('a"; filename="b', "spike", None, 0)
        assert '"' not in out and ";" not in out


# ── the endpoint ────────────────────────────────────────────────────────────
# Driven by calling the router coroutine with a fake session, the pattern the
# rest of api/tests/ uses: no database, no R2, no TestClient. The presigner is
# monkeypatched so the assertions are about what filename the route computed and
# whether it asked at all — not about botocore.

from app.models.clip import ActionType  # noqa: E402
from app.models.visibility import Visibility  # noqa: E402
from app.routers.clips import download_clip  # noqa: E402
from fastapi import HTTPException  # noqa: E402

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()


class _Game:
    def __init__(self, visibility=Visibility.private, title="Panthers vs Sharks"):
        self.id = uuid.uuid4()
        self.owner_id = OWNER
        self.visibility = visibility
        self.title = title


class _Player:
    def __init__(self, name="Rosa Diaz"):
        self.id = uuid.uuid4()
        self.name = name


class _Clip:
    def __init__(self, game, player=None, labels=None, visibility=None):
        self.id = uuid.uuid4()
        self.game_id = game.id
        self.player_id = player.id if player else None
        self.action_type = ActionType.spike
        self.labels = labels if labels is not None else []
        self.start_time = 252.0
        self.visibility = visibility
        self.clip_url = "https://r2.example/clips/abc.mp4"


class _FakeSession:
    """`db.get(Model, pk)` over a dict, which is all the route uses."""

    def __init__(self, *objects):
        self._by_id = {o.id: o for o in objects}

    async def get(self, _model, pk):
        return self._by_id.get(pk)


def _download(session, clip_id, viewer_id):
    return asyncio.run(download_clip(clip_id, session, viewer_id))


@pytest.fixture
def presigned(monkeypatch):
    """Capture what the route hands the presigner."""
    calls = {}

    def fake(stored_url, expires_in=3600, download_filename=None):
        calls.update(
            stored_url=stored_url,
            expires_in=expires_in,
            download_filename=download_filename,
        )
        return "https://r2.example/signed"

    from app.services import storage

    monkeypatch.setattr(storage, "presign_from_stored_url", fake)
    return calls


class TestDownloadEndpoint:
    def test_owner_gets_a_named_url(self, presigned):
        game = _Game()
        player = _Player()
        clip = _Clip(game, player)
        out = _download(_FakeSession(game, player, clip), clip.id, OWNER)

        assert out == {"url": "https://r2.example/signed"}
        assert presigned["download_filename"] == (
            "Panthers vs Sharks - spike - Rosa Diaz - 0-04-12.mp4"
        )

    def test_expiry_matches_share(self, presigned):
        # /share hard-codes 3600 with a note that the expiry question is open.
        # Answering it differently here would settle it by accident.
        game = _Game()
        clip = _Clip(game)
        _download(_FakeSession(game, clip), clip.id, OWNER)
        assert presigned["expires_in"] == 3600

    def test_the_name_follows_action_type_not_labels(self, presigned):
        """`labels` is detector output, not a human correction.

        ml/pipeline/detect.py writes it on every clip in first-seen order, and
        update_clip_labels stores it through list(set(...)), so its order is not
        stable across restarts. action_type is the primary action by
        construction and is rewritten from a correction, so it is the field a
        filename can rely on — and it is what ClipModal badges.
        """
        game = _Game()
        clip = _Clip(game, labels=["dig", "block"])
        _download(_FakeSession(game, clip), clip.id, OWNER)
        assert " - spike - " in presigned["download_filename"]

    def test_untagged_clip_has_no_empty_separator(self, presigned):
        game = _Game()
        clip = _Clip(game)
        _download(_FakeSession(game, clip), clip.id, OWNER)
        assert presigned["download_filename"] == (
            "Panthers vs Sharks - spike - 0-04-12.mp4"
        )
        assert " -  - " not in presigned["download_filename"]

    def test_a_stranger_is_refused_on_a_private_clip(self, presigned):
        # Authorization parity with /share: same helper, same 404-not-403.
        game = _Game(visibility=Visibility.private)
        clip = _Clip(game)
        with pytest.raises(HTTPException) as exc:
            _download(_FakeSession(game, clip), clip.id, STRANGER)
        assert exc.value.status_code == 404
        assert presigned == {}, "must not mint a URL for a refused viewer"

    def test_a_stranger_may_download_a_public_clip(self, presigned):
        # The documented asymmetry: an override publishes *that clip*.
        game = _Game(visibility=Visibility.private)
        clip = _Clip(game, visibility=Visibility.public)
        _download(_FakeSession(game, clip), clip.id, STRANGER)
        assert presigned["download_filename"].endswith(".mp4")

    def test_the_filename_does_not_leak_a_private_games_title(self, presigned):
        """The filename rides in the URL, in cleartext, so it is disclosure.

        A public clip inside a private game is reachable by direct link, but
        the game's title is not — /share discloses nothing and list_clips is
        gated on the game. Naming the file after it would hand an anonymous
        caller the private game's title.

        The earlier version of the test above asserted only `.endswith(".mp4")`
        against this exact configuration, which is why the leak passed review.
        """
        game = _Game(visibility=Visibility.private, title="Under-14 County Final")
        player = _Player(name="Rosa Diaz")
        clip = _Clip(game, player=player, visibility=Visibility.public)
        _download(_FakeSession(game, player, clip), clip.id, STRANGER)

        name = presigned["download_filename"]
        assert "Under-14" not in name and "County" not in name
        assert "Rosa" not in name and "Diaz" not in name
        # Still useful, still identifies the moment.
        assert name == "spike - 0-04-12.mp4"

    def test_the_owner_still_gets_the_identifying_name(self, presigned):
        # The guard must not cost the person entitled to the information.
        game = _Game(visibility=Visibility.private, title="Under-14 County Final")
        player = _Player(name="Rosa Diaz")
        clip = _Clip(game, player=player, visibility=Visibility.public)
        _download(_FakeSession(game, player, clip), clip.id, OWNER)
        assert presigned["download_filename"] == (
            "Under-14 County Final - spike - Rosa Diaz - 0-04-12.mp4"
        )

    def test_a_stranger_never_causes_the_player_row_to_be_read(self, presigned):
        """Not just absent from the name — not fetched at all.

        Asserting only on the string would pass a version that loads the player
        and then forgets to use it, which still reads a row the viewer has no
        claim to.
        """
        game = _Game(visibility=Visibility.private, title="Match")
        player = _Player(name="Rosa Diaz")
        clip = _Clip(game, player=player, visibility=Visibility.public)
        session = _FakeSession(game, player, clip)

        fetched = []
        original = session.get

        async def spy(model, pk):
            fetched.append(pk)
            return await original(model, pk)

        session.get = spy  # type: ignore[method-assign]
        _download(session, clip.id, STRANGER)
        assert player.id not in fetched

    def test_a_missing_clip_is_404_not_500(self, presigned):
        game = _Game()
        with pytest.raises(HTTPException) as exc:
            _download(_FakeSession(game), uuid.uuid4(), OWNER)
        assert exc.value.status_code == 404


class TestCondensedDownloadFilename:
    """The pre-existing game-download name, now routed through the sanitiser."""

    def test_keeps_the_shape_users_already_receive(self):
        assert condensed_download_filename("Panthers vs Sharks") == (
            "Panthers vs Sharks (condensed).mp4"
        )

    def test_a_colon_no_longer_reaches_the_disk(self):
        # presign_url strips only `";\` — a `:` used to survive into the header
        # and produce a filename Windows rejects.
        assert ":" not in condensed_download_filename("Semi: the rematch")

    def test_a_long_title_is_capped_but_not_at_the_component_budget(self):
        """Capping at 80 would silently shorten a name users already receive.

        This is a single-component filename, so the title gets the whole budget
        rather than the per-component share the four-part clip scheme uses.
        """
        out = condensed_download_filename("x" * 500)
        assert len(out) <= MAX_FILENAME_CHARS
        assert len(out) > MAX_COMPONENT_CHARS + len(" (condensed).mp4")

    def test_a_title_longer_than_a_component_survives_whole(self):
        title = "y" * 120
        assert condensed_download_filename(title) == f"{title} (condensed).mp4"

    def test_an_unusable_title_still_yields_a_name(self):
        assert condensed_download_filename("Матч") == "condensed.mp4"
        assert condensed_download_filename(None) == "condensed.mp4"


class TestFilenameLengthBound:
    def test_the_whole_name_fits_a_filesystem(self):
        # Four capped components plus separators reach 258 — over the 255-byte
        # limit most filesystems impose on a single name.
        out = clip_download_filename("g" * 200, "a" * 200, "p" * 200, 252.0)
        assert len(out) <= MAX_FILENAME_CHARS

    def test_the_timestamp_survives_truncation(self):
        # Trimmed from the front on purpose: the timestamp is what keeps two
        # clips from one game apart once a long title has eaten the budget.
        out = clip_download_filename("g" * 200, "a" * 200, "p" * 200, 252.0)
        assert out.endswith("0-04-12.mp4")


class TestTimestampEdgeCases:
    def test_nan_does_not_raise(self):
        # int(float("nan")) is a ValueError; a corrupt start_time should cost
        # the position in the name, not turn the download into a 500.
        assert format_timestamp(float("nan")) == "0-00-00"

    def test_infinity_does_not_raise(self):
        assert format_timestamp(float("inf")) == "0-00-00"
        assert format_timestamp(float("-inf")) == "0-00-00"


class TestLeadingCharacters:
    def test_a_leading_dash_is_stripped(self):
        # A name starting with `-` reads as a flag to any CLI it is passed to.
        assert not clip_download_filename("--rf", None, None, 0).startswith("-")


class TestLeadingDotIsAnAcceptedCost:
    def test_a_legitimate_leading_dot_is_stripped(self):
        """Documented trade-off, pinned so nobody restores it by accident.

        A downloaded file the user cannot see in Finder is worse than a name
        missing its first character — but the cost is real, so it is on the
        record rather than a surprise.
        """
        assert sanitize_component(".38 Special") == "38 Special"


class TestNamesSortChronologically:
    def test_a_late_clip_does_not_sort_before_an_early_one(self):
        """These land in one downloads folder and are sorted as strings.

        Mixing `59-59` with `1-00-00` put the 59-minute clip *after* the
        three-hour one, because "5" sorts after "1". Always carrying the hour
        fixes it, which is why a four-minute clip reads `0-04-12`.
        """
        names = [
            clip_download_filename("Match", "spike", None, s)
            for s in (252.0, 3599, 3600, 14399)
        ]
        assert names == sorted(names)
