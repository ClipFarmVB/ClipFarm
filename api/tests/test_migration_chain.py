"""Every migration file must declare an id nobody else claims (CF-243).

The api container runs `alembic upgrade head` on startup, and CF-189 does the
same locally whenever `DATABASE_URL` is local. So a broken revision graph is not
a failed test — it is a boot failure, and it takes every endpoint with it.

**Why a duplicate id is the dangerous shape.** Alembic does not refuse it. It
emits `UserWarning: Revision NNN is present more than once`, resolves the id to
one of the two files, and computes an upgrade path that silently omits the
other. `alembic upgrade head` then exits 0 having skipped a migration, so the
deploy reports success and the missing table surfaces later as a 500.

This is loaded and pointed at `main` right now. `main` carries
`014_upload_id_text.py` with `revision = "014"`; the CF-109 social stack carries
`014_posts.py` with `revision = "014"` and the same `down_revision`. Different
filenames, so git merges them with no conflict in `versions/` at all, and the
result is two files claiming `014`.

Parsed from the files rather than by building alembic's graph: no database, no
config, and — deliberately — no `pytest.importorskip`. A guard that can skip
itself into a green pass is the failure mode this file exists to prevent, so it
uses nothing outside the standard library.
"""
import collections
import pathlib
import re

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"

# Both spellings, because the two are a rebase apart: the annotated
# `revision: str = "014"` every current file uses, and the bare
# `revision = "014"` alembic's own template emits. A regex that matched only one
# would parse zero files and report a clean chain — which
# `test_every_migration_file_declares_a_revision` is what catches.
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
        f"no `revision = ...` found in: {sorted(missed)}. The declaration style "
        "changed and the regexes in this file no longer match it — until they "
        "do, every check below is reading an incomplete chain."
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
    """The cheapest tripwire of the three, and the earliest.

    A duplicate is only visible once both files are in one tree. This fires the
    moment someone renumbers the id without renaming the file, or vice versa —
    on their own branch, before any merge.
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

    The case that matters is alembic's merge revision, `("013", "014")`. It
    matches neither accepted shape, so without this it parses as `None`, reads
    as a second root, and surfaces as `expected one head, found [...]` — which
    tells you to renumber something, when the actual answer is that this repo
    does not use merge revisions at all (CLAUDE.md: the chain is linear).
    """
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
