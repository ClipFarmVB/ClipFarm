"""
Quantify the MIN_RALLY_CONTACTS cliff (CF-376, #476).

CF-174 scaled the contact *speed* thresholds by ``frame_height / 360`` and did
not scale ``MIN_RALLY_CONTACTS``, which is an absolute count enforced as
``if len(seg) < MIN_RALLY_CONTACTS: continue``. So a rally that drops from 3
detected contacts to 2 is not shortened — it is discarded whole. Production saw
34 clips become 11 on the same R2 ball cache.

#476 asks for one number before the fix is chosen: **how many segments land in
the 2-contact band under scaling**, because that is the population the gate is
deleting. This prints that, both switch positions side by side, off the same
dump ``tune_contacts`` uses.

  docker compose --env-file .env.docker run --rm --no-deps eval \\
      python -m ml.eval.contact_cliff test2

The fixture id selects the dump, and the useful ones are the **1080p** fixtures
(test2/test4). test1 is 360p, where ``_scale_for`` returns exactly 1.0 and the
two columns are identical by construction — that is CF-375 (#475)'s whole
point, and running this on test1 to see "no cliff" would be reading the
absence of the change as evidence about it.

**Two gates, not one.** #476 names ``MIN_RALLY_CONTACTS``; ``MIN_RALLY_DURATION``
(2.0s) is a second cliff immediately after it, and it is not independent — a
rally that loses its *outermost* contacts also gets shorter, so tightening
contact detection can push a segment under the duration gate while it still has
three contacts. The ladder below separates the two, because a fix aimed at the
count gate does nothing for a segment dying on duration and the totals alone
cannot tell them apart.

This tool measures. It decides nothing and changes no threshold.
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter

from ml.eval.tune_contacts import load
from ml.pipeline import ball as B

# The band the count gate deletes, and the band one lost contact away from it.
# Named rather than written as literals below so that a change to
# MIN_RALLY_CONTACTS keeps the report describing the gate that is actually
# compiled in, rather than the 3 this was written against.
DELETED_BAND = range(1, B.MIN_RALLY_CONTACTS)
AT_RISK = B.MIN_RALLY_CONTACTS


class Ladder:
    """One switch position's attrition, contacts through to rallies."""

    def __init__(self, contacts: list[dict], frame_height: int, duration: float,
                 *, normalize: bool):
        self.normalize = normalize
        self.contacts = contacts
        # The same segmentation contacts_to_rallies runs below — it calls
        # this function, which is why the extraction exists. If that ever
        # stops being true the ladder attributes losses to the wrong gate
        # silently, so the coupling is stated rather than assumed.
        self.segments = B.contact_segments(self.contacts)
        self.sizes = Counter(len(s) for s in self.segments)
        # The real function, not a reimplementation of its two gates: the point
        # of the card is what production emits.
        self.rallies = B.contacts_to_rallies(self.contacts, duration, frame_height)

    @classmethod
    def from_track(cls, track, frame_height: int, duration: float, *, normalize: bool):
        """Run the detector at one switch position, then measure it.

        The split exists so the accounting below can be tested against
        hand-built contact lists: a ball track that produces a segment of
        exactly two contacts, on demand, is far harder to construct than the
        two contacts themselves, and it is the gates that are under test here
        rather than find_contacts.
        """
        return cls(
            B.find_contacts(track, frame_height=frame_height, normalize=normalize),
            frame_height,
            duration,
            normalize=normalize,
        )

    @property
    def deleted_by_count(self) -> int:
        """Segments the count gate discards."""
        return sum(self.sizes[n] for n in DELETED_BAND)

    @property
    def survive_count_gate(self) -> int:
        return sum(c for n, c in self.sizes.items() if n >= B.MIN_RALLY_CONTACTS)

    @property
    def deleted_by_duration(self) -> int:
        """Segments that clear the count gate and then die on MIN_RALLY_DURATION."""
        return self.survive_count_gate - len(self.rallies)

    @property
    def at_risk(self) -> int:
        """EMITTED RALLIES sitting exactly on the gate — one contact from deletion.

        Counted off the rallies rather than off the segment histogram. A
        segment with exactly three contacts that then dies on the duration gate
        is not a rally at risk, it is a rally already gone; taking it from
        `sizes` reported it as the former and could make this number exceed the
        rally count directly above it.

        `_make_rally` puts the count in `features`, so this needs no second
        pass over the segments.
        """
        return sum(
            1 for r in self.rallies if r["features"]["contact_count"] == AT_RISK
        )

    def rows(self) -> list[tuple[str, int]]:
        return [
            ("contacts found", len(self.contacts)),
            # Its own row, because a rally can be lost BEFORE either gate: tighten
            # contact detection far enough and every contact in a rally goes, so
            # the segment never forms. That is the same mechanism as the count
            # gate one step earlier, it is a plausible large contributor at
            # 1080p, and with no row for it the ladder's two gate rows silently
            # failed to add up to the rallies lost.
            ("segments (both gates ahead)", len(self.segments)),
            (f"  deleted: <{B.MIN_RALLY_CONTACTS} contacts", self.deleted_by_count),
            ("  survive the count gate", self.survive_count_gate),
            (f"  then deleted: <{B.MIN_RALLY_DURATION:g}s", self.deleted_by_duration),
            ("RALLIES EMITTED", len(self.rallies)),
            (f"  ...of them at exactly {AT_RISK} contacts", self.at_risk),
        ]


def _delta(on: int, off: int) -> str:
    d = on - off
    return f"{d:+d}" if d else "  ."


def report(on: Ladder, off: Ladder, frame_height: int, scale: float) -> str:
    out: list[str] = []
    out.append(
        f"fixture frame_height={frame_height} -> CF-174 threshold scale {scale:.2f}"
    )
    if scale == 1.0:
        out.append(
            "  NOTE: scale is exactly 1.0, so the two columns below are the same run.\n"
            "  This fixture cannot show the cliff (CF-375, #475)."
        )
    out.append("")
    out.append("%-34s %10s %10s %8s" % ("", "scale ON", "scale OFF", "delta"))
    for (label, a), (_, b) in zip(on.rows(), off.rows()):
        out.append("%-34s %10d %10d %8s" % (label, a, b, _delta(a, b)))

    out.append("")
    out.append("contacts-per-segment histogram (before either gate)")
    widest = max([*on.sizes, *off.sizes], default=0)
    for n in range(1, widest + 1):
        a, b = on.sizes[n], off.sizes[n]
        if not (a or b):
            continue
        mark = ""
        if n in DELETED_BAND:
            mark = "  <- deleted by MIN_RALLY_CONTACTS"
        elif n == AT_RISK:
            mark = "  <- one contact from deletion"
        out.append("%-34s %10d %10d %8s%s" % (f"  {n} contact(s)", a, b, _delta(a, b), mark))

    out.append("")
    lost = len(off.rallies) - len(on.rallies)
    plural = "rally" if abs(lost) == 1 else "rallies"
    if scale == 1.0:
        # The conclusion is the quotable line, and at a scale of 1.0 the two
        # columns are the same run — so stating a cost of zero here reads as
        # evidence that there is no cliff, which is the one thing this fixture
        # cannot show. The note at the top says so; a reader who scrolls to the
        # bottom sees only this.
        out.append(
            "No conclusion available from this fixture: at a scale of 1.00 the "
            "two columns are the same run. Use a 1080p fixture (CF-375, #475)."
        )
    elif lost > 0:
        # Three channels, not two. A rally can also be lost before either gate,
        # by losing every contact it had — the segment then never forms, and
        # attributing the whole loss to the gates would hide it.
        never_formed = len(off.segments) - len(on.segments)
        by_count = on.deleted_by_count - off.deleted_by_count
        by_dur = on.deleted_by_duration - off.deleted_by_duration
        out.append(
            f"Scaling costs {lost} {plural} on this fixture: {never_formed} whose "
            f"segment never formed, {by_count} to the count gate, {by_dur} to the "
            f"duration gate."
        )
        assert never_formed + by_count + by_dur == lost, (
            "the three channels must account for every lost rally"
        )
        out.append(
            f"{on.at_risk} of the {len(on.rallies)} surviving rallies sit at exactly "
            f"{AT_RISK} contacts, so they are one detection away from the same fate."
        )
    elif lost < 0:
        out.append(f"Scaling GAINS {-lost} {plural} on this fixture.")
    else:
        out.append("Scaling costs no rallies on this fixture.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    # `or ""` because -OO strips docstrings, and a measurement tool should not
    # die on its own --help.
    summary = ((__doc__ or "").splitlines() or [""])[1:2]
    ap = argparse.ArgumentParser(description=summary[0] if summary else None)
    ap.add_argument("test_id", nargs="?", default="test2",
                    help="fixture id whose ball-track dump to read (default test2, 1080p)")
    args = ap.parse_args(argv)

    # find_contacts logs _scale_for's multi-line 1080p warning on every call, and
    # this tool calls it twice; tune_contacts silences the same noise for the
    # same reason.
    logging.disable(logging.INFO)
    track, _positions, frame_h, fx = load(args.test_id)
    # log=False for the label: find_contacts has already warned about this
    # frame height, and a second copy in the header is noise, not a second
    # opinion.
    scale = B._scale_for(frame_h, log=False, normalize=True)
    on = Ladder.from_track(track, frame_h, fx.duration, normalize=True)
    off = Ladder.from_track(track, frame_h, fx.duration, normalize=False)
    print(report(on, off, frame_h, scale))


if __name__ == "__main__":
    main()
