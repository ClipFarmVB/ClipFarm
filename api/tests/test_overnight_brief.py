"""The overnight brief's own token figures, recomputed rather than trusted.

`docs/overnight/README.md` carries a table of per-file token costs and five lap
costs, and a run reads them to plan what it can afford. They are pure functions
of the brief's own file sizes, so nothing but discipline kept them true — and
discipline lost: they had drifted by a third before CF-275 re-took them, and the
page still says "re-measure them when you add a section" as if asking were
enough. CF-371 (#464) is that class of failure, a figure that was right when
written and rotted untouched.

**What this does not reach.** CF-370 lists six wrong published figures; three
are this shape and three are a figure composed before its command was read. No
test sees a sentence that was written first, so `RULES.md`'s prose rule still
carries those.

Deliberately unpinned: the across-the-split figures further down that page
(32.25k, 19.09k, 18.13k, 26.70k, 28.05k, 23.60k). Those describe files at
revision `596755d`, which the page says the table cannot reproduce, and reading
them needs `git ls-tree` against an old revision — which a shallow clone serves
silently and wrongly, a trap `RULES.md` already lists.
"""

import pathlib
import re
from decimal import ROUND_HALF_UP, Decimal

BRIEF = pathlib.Path(__file__).resolve().parents[2] / "docs" / "overnight"
README = BRIEF / "README.md"

# Which files each lap reads, from the reading protocol. Every lap reads
# README.md itself — "this file's own row included, which is why there is one".
ALL_FILES = [
    "README.md", "START.md", "RULES.md", "REVIEW.md", "BRIEFS.md",
    "FIX.md", "TICKETS.md", "REPORTING.md", "RATIONALE.md",
]
LAPS = {
    "step-1, select only": ["README.md", "RULES.md", "REVIEW.md"],
    "the whole brief": ALL_FILES,
    "step-1, spawning a round": ["README.md", "RULES.md", "REVIEW.md", "BRIEFS.md"],
    "step-2": ["README.md", "RULES.md", "FIX.md", "BRIEFS.md"],
    "step-3": ["README.md", "RULES.md", "TICKETS.md"],
}

_ROW = re.compile(r"^\| \[`(?P<name>[A-Z]+\.md)`\]\([^)]*\) \|.*\| (?P<stated>[0-9]+\.[0-9])k \|$", re.MULTILINE)

# Pinned by its own sentence, never by sweeping for `\d+k`: the page carries
# nine historical figures it says are not recomputable, and `32k` appears twice.
_LAPS_SENTENCE = re.compile(
    r"A step-1 lap that only selects costs about (?P<select>\d+)k tokens of brief\s+"
    r"instead of (?P<all>\d+)k;\s+one that also spawns a round, about (?P<spawn>\d+)k\.\s+"
    r"A step-2 lap is about (?P<step2>\d+)k, a step-3 lap\s+about (?P<step3>\d+)k\."
)
_CHEAPER = re.compile(r"only selects is ~(?P<k>\d+)k cheaper than one")


def _size(name):
    """Bytes of a brief file, with CRLF normalised to LF.

    `.gitattributes` pins `eol=lf` for `*.sh` and `.hooks/*` only, and its own
    comment says this repo is worked on from Windows — so with
    `core.autocrlf=true` these files check out fatter and five of the nine rows
    would change tenth. The figures describe the content, not the checkout.
    """
    return len((BRIEF / name).read_bytes().replace(b"\r\n", b"\n"))


def _tenths(size):
    """`bytes/4` in thousands, to the nearest tenth.

    Half-up, not Python's `round`, which is half-even: a human re-measuring
    5.2475k -> 5.25k writes 5.3 and `round()` gives 5.2. `BRIEFS.md` sits ten
    bytes below that exact tie, so the two rules are one edit apart from
    disagreeing.
    """
    return (Decimal(size) / 4000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _whole(size):
    """The same, to the nearest whole k — what the page's "about Nk" claims."""
    return (Decimal(size) / 4000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def test_the_table_lists_exactly_the_brief_files():
    """No file missing a row, and no row for a file that is gone.

    A new phase file with no row is invisible to a run planning its budget, and
    every lap that reads it is understated by however large it is.
    """
    listed = {m.group("name") for m in _ROW.finditer(README.read_text(encoding="utf-8"))}
    present = {p.name for p in BRIEF.glob("*.md")}
    assert listed == present, (
        f"docs/overnight/README.md's table lists {sorted(listed)} but the "
        f"directory holds {sorted(present)}. Add the row (with its token cost) "
        "or drop the stale one, and re-measure the lap figures — a file with no "
        "row is one no lap counts (CF-371, #464)."
    )


def test_every_row_states_the_size_the_file_actually_is():
    wrong = []
    for match in _ROW.finditer(README.read_text(encoding="utf-8")):
        name, stated = match.group("name"), Decimal(match.group("stated"))
        actual = _tenths(_size(name))
        if stated != actual:
            wrong.append(f"{name}: table says {stated}k, file is {actual}k ({_size(name)} bytes)")
    assert not wrong, (
        "docs/overnight/README.md's token table has drifted from the files it "
        "describes:\n  " + "\n  ".join(wrong) + "\nRe-measure the table and the "
        "five lap figures below it. This is the drift CF-275 had to re-take by "
        "hand once already (CF-371, #464)."
    )


def test_the_five_lap_figures_match_the_files_each_lap_reads():
    """The costs a run plans against, recomputed from the files.

    These move on almost any brief edit, and that is the point rather than a
    flaw: the page tells the next reader what a lap costs, so an edit that
    changes the cost has to change the page. Editing README.md moves all five,
    since every lap reads it.
    """
    text = README.read_text(encoding="utf-8")
    stated = _LAPS_SENTENCE.search(text)
    assert stated, (
        "docs/overnight/README.md's lap-cost sentence no longer matches the "
        "shape this test reads. It is pinned by its own wording on purpose — "
        "the page carries historical across-the-split figures that must not be "
        "recomputed. Update _LAPS_SENTENCE here if the wording changed "
        "deliberately (CF-371, #464)."
    )
    keys = {"select": "step-1, select only", "all": "the whole brief",
            "spawn": "step-1, spawning a round", "step2": "step-2", "step3": "step-3"}
    wrong = []
    for group, lap in keys.items():
        total = sum(_size(name) for name in LAPS[lap])
        actual = _whole(total)
        if Decimal(stated.group(group)) != actual:
            wrong.append(f"{lap}: page says {stated.group(group)}k, files sum to {actual}k ({total} bytes)")
    assert not wrong, (
        "docs/overnight/README.md's lap costs no longer match the files those "
        "laps read:\n  " + "\n  ".join(wrong) + "\nRe-measure them (CF-371, #464)."
    )


def test_the_spawning_lap_costs_briefs_md_more():
    """The one derived claim on the page, checked against the file it names.

    The difference between the two step-1 laps is `BRIEFS.md` by construction,
    so what is worth pinning is that the stated `~5k` is still what that file
    rounds to.
    """
    stated = _CHEAPER.search(README.read_text(encoding="utf-8"))
    assert stated, "docs/overnight/README.md no longer states the select-vs-spawn difference (CF-371, #464)."
    actual = _whole(_size("BRIEFS.md"))
    assert Decimal(stated.group("k")) == actual, (
        f"docs/overnight/README.md says a select-only lap is ~{stated.group('k')}k cheaper than a "
        f"spawning one, but BRIEFS.md — the only file between them — is {actual}k "
        f"({_size('BRIEFS.md')} bytes). Re-measure (CF-371, #464)."
    )
