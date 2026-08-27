"""Every migration file must declare an id nobody else claims (CF-243).

`alembic upgrade head` runs on the production deploy, as Render's
`preDeployCommand` (DEPLOY_RENDER.md), and in the api container ahead of
uvicorn — behind `&&`, and only when `DATABASE_URL` is local
(`api/scripts/auto_migrate.py`, CF-189). So a broken revision graph is not a
failed test. It is a failed deploy on one path and a container that never
serves on the other, and it takes every endpoint with it.

**Why a duplicate id is the dangerous shape — and it is not that it is quiet.**
The card said alembic resolves the id to one file, skips the other, and exits 0
with a migration silently unapplied. That is wrong, and this file said it too
until CF-243's review. Checked against the pinned alembic (1.14.0) on this
repo's own `versions/` plus CF-109's historical `014_posts.py`, in a scratch
directory with no database: alembic warns
`Revision 014 is present more than once`, and then
`RevisionMap._revision_map` puts *both* `Revision` objects into
`heads`, while `map_[id]` keeps only the file read last — `014_upload_id_text.py`
here, with `014_posts.py` dropped from the map and left in `heads`. Nothing
takes it out again: the only removal is `heads.discard(map_[downrev])`, which
reaches a revision through the map *by its parent's id*, and with both `014`
files at the tip the `downrev` in hand is `013` — so no duplicate is pruned at
all. `Revision` defines neither `__eq__` nor `__hash__`, so the two objects do
not collapse into one either. The tree ends with two heads printing the same
string: `alembic heads` emits `014 (head)` twice, and `alembic upgrade head`
resolves `head` through `get_current_head()`, raises `MultipleHeads`, prints
`FAILED: Multiple head revisions are present for given argument 'head'` and
exits 255 with nothing applied. Both shapes do this — the CF-109 pair sharing a
`down_revision`, and a pair with different parents alike.

So it is loud. What it is not is recognisable: the message says *multiple
heads*, which reads as a branch somebody forked on purpose, while the two heads
print as one id and `versions/` contains no fork at all. It lands on the
command above, at deploy time, pointing at the wrong thing. This file exists to
name the collision, in the words that fix it, at merge time instead.

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

What that costs is cycle detection, and six green here does not mean the chain
is sound. Alembic builds the graph and raises `CycleDetected`; this file only
reads declared ids, and a loop among them can leave every check below happy.
Measured on this repo's `versions/` with `001`'s `down_revision` set to `"014"`
and `003`'s to `"001"`: one head, no duplicates, every parent resolving, all six
tests pass — and alembic 1.14 refuses the same directory with
`Cycle is detected in revisions (001, 002, ..., 014)`. See the merge note below.
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
# Theirs also has test_the_chain_is_a_single_line — no revision may be the
# parent of two others — and that is not redundant with the head count here: a
# fork paired with a cycle still leaves exactly one head, so all six tests below
# pass on a tree alembic rejects outright (module docstring). Keep it too.
#
# This one parses into a LIST — see `_parsed()`. Theirs parses into
# `dict[revision_id, down_revision]`, where two files claiming one id collapse
# into a single entry and every check reading it sees a chain that looks fine;
# it works around that with a second, list-shaped parser (`_revision_files()`)
# used by its duplicate check and its parse-accounting test, while its chain
# tests still read the dict. Folding both onto one list parser is the
# load-bearing half of the resolution and the half that is easy to drop,
# because the tests stay green either way.
#
# Also here and not there: the filename-prefix check and the down_revision
# shape check (`test_every_down_revision_is_a_string_or_none`).
# ---------------------------------------------------------------------------

# Both spellings: the annotated `revision: str = "014"` that every file here
# uses and that alembic 1.14's own `script.py.mako` emits, and the bare
# `revision = "014"` that older alembic templates emitted and that a hand-written
# file may still carry. A regex that matched only one would parse zero files and
# report a clean chain — which is what
# `test_every_migration_file_declares_a_revision` catches.
#
# The `(?P=q)` backreference is load-bearing: `["\']...["\']` would accept
# `"014'`, which is not a Python string at all — the file would not import, and
# alembic would never get an id out of it. Reading `014` from it instead lets a
# file that cannot be loaded look like a well-formed revision. Requiring the
# quotes to match makes it a miss, which is what
# test_every_migration_file_declares_a_revision names.
_REVISION = re.compile(
    r'^revision\s*(?::[^=]+)?=\s*(?P<q>["\'])(?P<id>[^"\']+)(?P=q)', re.M
)
# Captures whatever is on the right of the `=` rather than only the two shapes
# we accept. The two regexes are deliberately asymmetric in what a miss means:
# a missing `revision` is caught by test_every_migration_file_declares_a_revision,
# but `None` is a *legal* down_revision meaning "root" — so a down_revision this
# file could not read would otherwise be indistinguishable from a root, and would
# surface as a phantom extra head rather than as a parse failure. Keeping the raw
# text is what lets test_every_down_revision_is_a_string_or_none say so directly.
_DOWN_RAW = re.compile(r"^down_revision\s*(?::[^=]+)?=\s*(.+?)\s*(?:#.*)?$", re.M)
# Same matched-quote requirement, and the same reason: `"013'` is a syntax
# error, not a parent id. Read as `013` it would pass every check below while
# the file it came from could not be imported.
_QUOTED = re.compile(r'^(?P<q>["\'])(?P<id>[^"\']+)(?P=q)$')


# Alembic's own filter on `versions/`, copied from `_only_source_rev_file` in
# alembic/script/base.py (1.14.0): a leading `__init__` or `.#` disqualifies a
# filename from being a revision file at all. So `versions/__init__.py` and an
# editor's `.#014_x.py` lock file are invisible to alembic, and a test that
# demanded a `revision = ...` from them would be failing on something that
# cannot break a deploy. Every other `*.py` here alembic *does* try to read.
#
# It is a byte-for-byte copy of a *private* constant, not an import, so nothing
# warns if the two drift apart. When the pinned alembic moves, re-read
# `_only_source_rev_file` in `alembic/script/base.py` and make this match it —
# if alembic starts excluding more names, this file fails on something alembic
# never reads; if it excludes fewer, this file stops checking a file that can.
_ALEMBIC_REV_FILE = re.compile(r"(?!\.\#|__init__)(.*\.py)$")


def _files():
    return sorted(p for p in VERSIONS.glob("*.py") if _ALEMBIC_REV_FILE.match(p.name))


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
      - `raw_down` set but not a matched-quote string literal — a line this
        file cannot read, such as alembic's merge form `("013", "014")`, a bare
        name, or mismatched quotes.

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
            down = quoted.group("id")
        out.append((path.name, rev.group("id"), down, raw))
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
        "below is reading an incomplete chain. If they are not migrations, "
        "move them: alembic reads every `*.py` in versions/ except the "
        "`__init__`/`.#` names it excludes by rule, and dies on one it cannot "
        "get an id from — `Could not determine revision id from filename "
        "<name>`. There is no exemption to add here; versions/ holds revision "
        "files only."
    )
    assert parsed, "no migrations found at all — is the versions/ path right?"


def test_no_revision_id_is_claimed_twice():
    """The failure that arrives under the wrong name.

    Two files with the same id do not conflict in git when the filenames differ,
    so this is caught here or at deploy time, and nowhere in between — and at
    deploy time it is reported as `Multiple head revisions are present`, which
    describes a fork that is not there. See the module docstring for the
    measurement.
    """
    parsed = _parsed()
    counts = collections.Counter(rev for _, rev, _, _ in parsed)
    dupes = {
        rev: sorted(name for name, r, _, _ in parsed if r == rev)
        for rev, n in counts.items()
        if n > 1
    }
    assert not dupes, (
        f"two migrations declare the same revision id: {dupes}. Alembic warns "
        "`Revision NNN is present more than once` and then keeps both in its "
        "head set, so the tree has two heads printing the same id: `alembic "
        "upgrade head` fails with `Multiple head revisions are present for "
        "given argument 'head'` and exits 255, applying nothing. That message "
        "names a fork, not a duplicate, which is why this is worth failing on "
        "here. Renumber the later one onto the other."
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
    # Split the *stem*, not the filename: `name.split("_")[0]` on a file with no
    # underscore yields `015.py`, which is not `isdigit()`, so the whole check
    # skipped exactly the names most likely to have been renumbered by hand.
    # The `isdigit()` guard itself stays: it scopes this check to the numbered
    # scheme this repo uses, so a file named some other way is not reported as a
    # mismatch against a prefix that was never meant to be a revision id.
    mismatched = {
        name: rev
        for name, rev, _, _ in _parsed()
        if (prefix := pathlib.Path(name).stem.split("_", 1)[0]) != rev
        and prefix.isdigit()
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
    for a root, so a file missing it is a hand-edit. Deleting 007's line makes
    the head test fail too, at `006` and `014` — but that test can only report
    the two ids, and this one names the file and the missing line.

    *A line in a shape the regexes do not accept* — the merge revision
    `("013", "014")` being the one that matters, since this repo does not use
    merge revisions at all (CLAUDE.md: the chain is linear), and mismatched
    quotes such as `"013'` being the one most likely to be a typo.

    Both are the actionable half of a failure the head test can only count.
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
        "— so rebase onto the current head instead of merging two. Mismatched "
        "quotes (`\"013'`) mean the file will not import at all. Anything else "
        "means the declaration style changed and the regexes in this file need "
        "updating; until then the chain below is being read wrong."
    )


def test_there_is_exactly_one_head():
    """No forks. Two heads means alembic cannot pick an upgrade path.

    Computed over the distinct ids so that a duplicate does not surface here as
    a confusing head count — `test_no_revision_id_is_claimed_twice` owns that
    failure and says something useful about it.

    This test knows one thing: which ids nothing else names as a parent. It
    cannot tell *why* there is more than one, because several unrelated faults
    land on the same count — a genuine fork, a `down_revision` pointing outside
    `versions/`, one this file could not read or that is missing entirely, and a
    `revision` line the regexes missed, which drops a file out of the chain and
    strands its parent. Each has its own test, and each of those says which file
    and which line. So this message reports what it computed and lists the
    states that produce it, rather than naming a cause it has not established;
    it used to assert "two migrations share a parent", which is wrong for all
    but the first.
    """
    parsed = _parsed()
    revisions = {rev for _, rev, _, _ in parsed}
    parents = {down for _, _, down, _ in parsed if down is not None}
    heads = sorted(revisions - parents)

    def describe(head: str) -> str:
        """What the files themselves say about a head — no inference."""
        for name, rev, down, raw in parsed:
            if rev != head:
                continue
            if raw is None:
                return f"{head} ({name}: no down_revision line at all)"
            if down is not None:
                return f"{head} ({name}: down_revision {down!r})"
            if raw == "None":
                return f"{head} ({name}: down_revision None, a declared root)"
            return f"{head} ({name}: down_revision {raw}, unreadable)"
        return f"{head} (named as a parent, but no file declares it)"

    assert len(heads) == 1, (
        f"expected one head, found {len(heads)}: "
        f"{'; '.join(describe(h) for h in heads)}. A head is an id no file "
        "names as its down_revision, and that is all this check knows — it "
        "cannot tell which fault produced these. The states that do: two files "
        "declaring different ids on the same parent (a real fork — renumber one "
        "onto the other and coordinate the merge order); a down_revision "
        "pointing at a revision not in versions/, which leaves its own parent "
        "looking like a head; a down_revision missing or unreadable, which "
        "reads as a second root; and a `revision` line this file could not "
        "parse, which drops that file out of the chain and strands its parent. "
        "If test_every_down_revision_resolves, "
        "test_every_down_revision_is_a_string_or_none or "
        "test_every_migration_file_declares_a_revision also failed, fix that "
        "first and name the file — this count is downstream of it."
    )


def test_every_down_revision_resolves():
    """A parent that is not in the directory is the same outage as a duplicate
    by a different route, and reported even less helpfully.

    Alembic 1.14 warns `Revision 099 referenced from ... is not present` and
    then, in the statement immediately after the warning, indexes the map with
    that same id (`down_revision = map_[downrev]`) — so the
    command dies with a bare `KeyError: '099'` traceback out of
    `RevisionMap._revision_map`, not with a message about migrations at all.
    (`Can't locate revision identified by ...` is a different path: resolving
    an identifier, such as the version the database is stamped with.)
    """
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
