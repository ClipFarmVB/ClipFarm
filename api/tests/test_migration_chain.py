"""Every migration file must declare an id nobody else claims (CF-243).

The api container runs `alembic upgrade head` on startup, and CF-189 does the
same locally whenever `DATABASE_URL` is local. So a broken revision graph is not
a failed test — it is a boot failure, and it takes every endpoint with it.

**Why a duplicate id is the dangerous shape.** Alembic does not refuse it. It
emits `UserWarning: Revision NNN is present more than once`, resolves the id to
one of the two files, and computes an upgrade path that silently omits the
other. `alembic upgrade head` then exits 0 having skipped a migration, so the
deploy reports success and the missing table surfaces later as a 500.

This repo had it loaded. When this file was written, `main` carried
`014_upload_id_text.py` with `revision = "014"` and the CF-109 social stack
carried `014_posts.py` with `revision = "014"` and the same `down_revision` —
different filenames, so git would have merged them with no conflict in
`versions/` at all and left two files claiming `014`. That particular pair is
defused: the stack has since renumbered posts to `016`. The shape is not — and
on `main` nothing reports it, `test_migration_010_backfill.py` being the only
other test that reads `versions/` and it loads one revision to exercise a
backfill helper.

Parsed from the files rather than by building alembic's graph: no database, no
config, and — deliberately — no `pytest.importorskip`. A guard that can skip
itself into a green pass is the failure mode this file exists to prevent, so it
uses nothing outside the standard library.
"""
import collections
import pathlib
import re

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"

# ---------------------------------------------------------------------------
# Merge note, for whoever resolves the conflict on this path.
#
# This exact path is also shipped — as one identical blob — by four branches of
# the CF-109 social stack: api/CF-109-posts, api/CF-110-follow-graph,
# api/CF-111-home-feed and web/CF-112-feed-ui. `main` has no such file, so
# whichever lands second arrives as an add/add conflict here; `git merge-tree`
# against each of the four reports that conflict and no other. Do not resolve
# it by taking one side whole — each has something the other cannot express.
#
# Theirs has PENDING_UPSTREAM: a dict of revisions that exist only on a still
# open PR, which turns a parent missing from versions/ into a declared
# merge-order dependency instead of a failure, plus a test that forces the
# entry out again once that PR lands. This file has no equivalent. Keep it.
#
# This one parses into a LIST — see `_parsed()`. Theirs parses into
# `dict[revision_id, down_revision]`, where two files claiming one id collapse
# into a single entry and every check reading it sees a chain that looks fine;
# it works around that with a second, list-shaped parser used by its duplicate
# check alone, while its other tests still read the dict. Folding both onto one
# list parser is the load-bearing half of the resolution and the half that is
# easy to drop, because the tests stay green either way.
#
# Also here and not there: the filename-prefix check and the down_revision
# shape check (`test_every_down_revision_is_a_string_or_none`).
# ---------------------------------------------------------------------------

# Both spellings: the annotated `revision: str = "014"` that every file here
# uses and that alembic 1.14's own `script.py.mako` emits, and the bare
# `revision = "014"` that older alembic templates emitted and that a hand-written
# file may still carry. A regex that matched only one would parse zero files and
# report a clean chain — which `test_every_migration_file_declares_a_revision`
# is what catches.
_REVISION = re.compile(r'^revision\s*(?::[^=]+)?=\s*["\']([^"\']+)["\']', re.M)
# Captures whatever is on the right of the `=` rather than only the two shapes
# we accept. The two regexes are deliberately asymmetric in what a miss means:
# a missing `revision` is caught by test_every_migration_file_declares_a_revision,
# but `None` is a *legal* down_revision meaning "root" — so a down_revision this
# file could not read would otherwise be indistinguishable from a root, and would
# surface as a phantom extra head rather than as a parse failure. Keeping the raw
# text is what lets test_every_down_revision_is_a_string_or_none say so directly.
_DOWN_RAW = re.compile(r"^down_revision\s*(?::[^=]+)?=\s*(.+?)\s*(?:#.*)?$", re.M)
_QUOTED = re.compile(r'^["\']([^"\']+)["\']$')


def _files():
    return sorted(VERSIONS.glob("*.py"))


def _parsed() -> list[tuple[str, str, str | None, str | None]]:
    """`(filename, revision, down_revision, raw_down)` per migration, as a LIST.

    A list, not a dict keyed by revision — that is the whole point. Keyed by id,
    two files claiming the same one collapse into a single entry and every check
    downstream reads a chain that looks fine.

    `raw_down` is the unparsed right-hand side, kept so a `down_revision` this
    file cannot read is reportable as such instead of silently becoming a root.
    It carries two distinct states, and `down is None` collapses both into
    "root":

      - `raw_down is None` — no `down_revision` line was found at all,
      - `raw_down` set but unquoted — a line this file cannot read, such as
        alembic's merge form `("013", "014")`.

    Both are reported by `test_every_down_revision_is_a_string_or_none`. Only
    `raw_down == "None"` is a real root.
    """
    out: list[tuple[str, str, str | None, str | None]] = []
    for path in _files():
        text = path.read_text(encoding="utf-8")
        rev = _REVISION.search(text)
        if not rev:
            continue
        raw_match = _DOWN_RAW.search(text)
        raw = raw_match.group(1) if raw_match else None
        down: str | None = None
        if raw is not None and (quoted := _QUOTED.match(raw)):
            down = quoted.group(1)
        out.append((path.name, rev.group(1), down, raw))
    return out


def test_every_migration_file_declares_a_revision():
    """Pins the parser against the files rather than against a magic number.

    If the declaration style drifts and the regexes stop matching, every other
    test here starts passing over an empty list. Comparing counts means that
    shows up as this failure instead of as silence.
    """
    files, parsed = _files(), _parsed()
    missed = {f.name for f in files} - {name for name, _, _, _ in parsed}
    assert not missed, (
        f"these files in versions/ declare no `revision = ...`: {sorted(missed)}."
        " If they are migrations, the declaration style has changed and the "
        "regexes in this file no longer match it — until they do, every check "
        "below is reading an incomplete chain. If they are not migrations, they "
        "are new: versions/ has held nothing but revision files so far (it is "
        "not even a package), so decide whether that is intended and skip them "
        "here if it is."
    )
    assert parsed, "no migrations found at all — is the versions/ path right?"


def test_no_revision_id_is_claimed_twice():
    """The failure alembic only warns about.

    Two files with the same id do not conflict in git when the filenames differ,
    so this is caught here or at container boot, and nowhere in between.
    """
    parsed = _parsed()
    counts = collections.Counter(rev for _, rev, _, _ in parsed)
    dupes = {
        rev: sorted(name for name, r, _, _ in parsed if r == rev)
        for rev, n in counts.items()
        if n > 1
    }
    assert not dupes, (
        f"two migrations declare the same revision id: {dupes}. Alembic only "
        "WARNS on this, resolves the id to one of them, and computes an upgrade "
        "path that skips the other — so `alembic upgrade head` exits 0 with a "
        "migration silently unapplied. Renumber the later one onto the other."
    )


def test_the_filename_prefix_matches_the_revision_id():
    """The only check here that looks at the filename at all.

    Everything else reads declared ids, so a file whose name disagrees with the
    id inside it leaves a chain that is genuinely valid — one head, no
    duplicates, every parent resolving — while the directory listing states the
    wrong number for it. That listing is the cheap signal a human coordinating
    merge order works from (CLAUDE.md: the chain is linear, so two PRs adding
    migrations in parallel have their order arranged by hand), and every
    renumbering here is a two-part edit: rename the file, change the id. This
    fires when only half of it happened, and it is the only test that does.

    It would *not* have caught this module's motivating collision. On
    `api/CF-109-posts` the posts migration's prefix and id have always agreed —
    `016_posts.py` / `"016"` today, `014_posts.py` / `"014"` when this file was
    written. A duplicate is visible only once both files share a tree, which is
    `test_no_revision_id_is_claimed_twice`'s job, not this one's.
    """
    mismatched = {
        name: rev
        for name, rev, _, _ in _parsed()
        if (prefix := name.split("_", 1)[0]) != rev and prefix.isdigit()
    }
    assert not mismatched, (
        f"filename prefix and revision id disagree: {mismatched}. Rename the "
        "file and the id together — the prefix is how a human spots a collision "
        "in a directory listing, so a file lying about its own id removes the "
        "only cheap way to see one coming."
    )


def test_every_down_revision_is_a_string_or_none():
    """A `down_revision` this file cannot read must say so, not become a root.

    Two shapes reach here, and both otherwise read as a legitimate root:

    *No line at all.* Alembic's template always emits `down_revision`, `None`
    for a root, so a file missing it is a hand-edit. Without this assertion the
    only failure is the head test's `expected one head, found [...]` — deleting
    007's line reports `['006', '014']` — which names neither the file nor the
    missing line, and points at renumbering.

    *A line in a shape the regexes do not accept* — the one that matters being
    alembic's merge revision, `("013", "014")`. Same misdiagnosis, and the
    actual answer is that this repo does not use merge revisions at all
    (CLAUDE.md: the chain is linear).
    """
    absent = sorted(name for name, _, _, raw in _parsed() if raw is None)
    assert not absent, (
        f"no `down_revision` declaration found in: {absent}. Alembic's own "
        "template always emits the line — `down_revision = None` for a root — "
        "so a file without one has been hand-edited. Add it back: `None` if "
        "this really is the first migration, otherwise the id of its parent."
    )
    unreadable = {
        name: raw
        for name, _, down, raw in _parsed()
        if raw is not None and down is None and raw != "None"
    }
    assert not unreadable, (
        f"down_revision is neither a quoted id nor None: {unreadable}. A tuple "
        "means a merge revision, and this repo keeps a linear chain (CLAUDE.md) "
        "— so rebase onto the current head instead of merging two. Anything "
        "else means the declaration style changed and the regexes in this file "
        "need updating; until then the chain below is being read wrong."
    )


def test_there_is_exactly_one_head():
    """No forks. Two heads means alembic cannot pick an upgrade path.

    Computed over the distinct ids so that a duplicate does not surface here as
    a confusing head count — `test_no_revision_id_is_claimed_twice` owns that
    failure and says something useful about it.
    """
    parsed = _parsed()
    revisions = {rev for _, rev, _, _ in parsed}
    parents = {down for _, _, down, _ in parsed if down is not None}
    heads = sorted(revisions - parents)
    assert len(heads) == 1, (
        f"expected one head, found {heads}. Two migrations share a parent — "
        "renumber one onto the other and coordinate the merge order."
    )


def test_every_down_revision_resolves():
    """A parent that is not in the directory is `Can't locate revision ...` at
    boot, which is the same outage as a duplicate by a different route."""
    parsed = _parsed()
    revisions = {rev for _, rev, _, _ in parsed}
    dangling = {
        name: down
        for name, _, down, _ in parsed
        if down is not None and down not in revisions
    }
    assert not dangling, (
        f"down_revision points at a revision that is not here: {dangling}. "
        "Either the file is missing, or it is still on another branch — in "
        "which case this branch cannot boot until that one merges."
    )
