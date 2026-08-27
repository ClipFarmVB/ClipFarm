"""The alembic chain must be linear, contiguous, and single-headed.

The api container runs `alembic upgrade head` on startup, and alembic resolves
the *whole* chain before running anything. A `down_revision` pointing at a
revision that isn't in the directory raises
`Can't locate revision identified by 'NNN'` — and because that happens at boot,
the failure isn't scoped to the new feature. **Every endpoint is down.**

CF-109's posts migration has been renumbered twice by collisions on main, and
each time its parent lived on a still-open PR. That dependency used to be a
sentence in a docstring, which is exactly the kind of thing that survives a
rebase, a cherry-pick, or a reviewer skimming the body. PENDING_UPSTREAM turns
it into a list someone has to consciously edit. Numbers are deliberately not
repeated in this prose — they move, and stale prose about migration order is
what these tests exist to replace.

Parsed from the files rather than by booting alembic: no database, no config,
and it runs in the pre-commit hook's `pytest` step.
"""
import pathlib
import re


VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"

# Revisions this branch's chain depends on that live on another branch, with the
# PR that brings them. An entry here is a **merge-order dependency**: merging
# this branch first boots the api into a broken chain.
#
# Delete the entry once that PR has merged and the file is present — the last
# test in this module fails if you don't, so the list can't rot into a
# permanent exemption.
PENDING_UPSTREAM: dict[str, tuple[str, str]] = {
    # revision: (its down_revision, the PR that brings it)
    #
    # 015 is CF-226 (#229), a one-line widening of games.error_message. It was
    # going to take the next number after this stack; that gated a P1 behind two
    # feature PRs, so the dependency was inverted and it takes 015 while posts
    # moved to 016. Merge #229 first.
    "015": ("014", "#229 — CF-226 widen games.error_message"),
}


def _chain_including_pending() -> dict[str, str | None]:
    """The chain as it will look once the upstream PRs have merged.

    Recording the *edge* rather than just the id matters: without 012's own
    parent, 011 looks like a second head simply because nothing present claims
    it. It also pins the expected parent — if #185 renumbers, this stops
    describing reality and `test_every_down_revision_resolves` says so.
    """
    chain = _revisions()
    for rev, (down, _) in PENDING_UPSTREAM.items():
        chain.setdefault(rev, down)
    return chain


def _revisions() -> dict[str, str | None]:
    """revision id → down_revision, read from the migration files."""
    chain: dict[str, str | None] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision:?\s*(?::\s*str\s*)?=\s*"([^"]+)"', text, re.M)
        down = re.search(
            r"^down_revision:?\s*(?::[^=]+)?=\s*(?:\"([^\"]+)\"|None)", text, re.M
        )
        if rev:
            chain[rev.group(1)] = down.group(1) if down and down.group(1) else None
    return chain


def test_the_versions_directory_parses():
    chain = _revisions()
    assert len(chain) >= 10, f"only found {len(chain)} revisions — did the format change?"


def test_every_down_revision_resolves():
    """A dangling parent means the api will not boot."""
    chain = _revisions()
    dangling = {
        rev: down
        for rev, down in chain.items()
        if down is not None and down not in chain and down not in PENDING_UPSTREAM
    }
    assert not dangling, (
        f"down_revision points at a revision that does not exist: {dangling}. "
        "Either the file is missing, or it lives on another branch — in which "
        "case add it to PENDING_UPSTREAM with the PR that brings it."
    )


def test_there_is_exactly_one_head():
    """Two heads is the collision CLAUDE.md warns about: two PRs each adding a
    migration off the same parent. Alembic refuses to upgrade, and again that is
    at container start.

    A revision waiting upstream is counted as present, since that is the state
    the chain will be in once its PR merges — which is the state that has to be
    single-headed. `test_every_down_revision_resolves` is what covers the gap
    until then.
    """
    chain = _chain_including_pending()
    parents = {down for down in chain.values() if down is not None}
    heads = sorted(set(chain) - parents)
    assert len(heads) == 1, (
        f"expected one head, found {heads}. Two migrations share a parent — "
        "renumber one and coordinate the merge order."
    )


def test_the_chain_is_a_single_line():
    """No revision may be the parent of two others."""
    chain = _chain_including_pending()
    seen: dict[str, str] = {}
    for rev, down in chain.items():
        if down is None:
            continue
        assert down not in seen, f"{down} is the parent of both {seen[down]} and {rev}"
        seen[down] = rev


def test_pending_upstream_entries_are_still_pending():
    """Forces the list to be cleaned up.

    Once the upstream PR merges and its file lands here, the exemption is no
    longer describing reality — and a stale exemption is how a genuinely
    dangling parent slips through later.
    """
    chain = _revisions()
    landed = {rev: why for rev, (_, why) in PENDING_UPSTREAM.items() if rev in chain}
    assert not landed, (
        f"these have landed and no longer need an exemption: {landed}. "
        "Remove them from PENDING_UPSTREAM."
    )


def _revision_files() -> list[tuple[str, str]]:
    """(filename, revision_id) for every migration, keeping duplicates.

    `_revisions()` returns a dict keyed on revision id, so two files claiming
    the same id collapse into one entry — and every chain test below then passes
    in exactly the state that breaks a deploy. This keeps the list.
    """
    out: list[tuple[str, str]] = []
    for path in sorted(VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision:?\s*(?::\s*str\s*)?=\s*"([^"]+)"', text, re.M)
        if rev:
            out.append((path.name, rev.group(1)))
    return out


def test_no_revision_id_is_claimed_twice():
    """The blind spot the dict-based parse could not see (CF-243).

    Alembic does not error on a duplicate revision id — it emits a UserWarning,
    resolves the id to one of the two, and computes an upgrade path containing
    only that one. So `alembic upgrade head` exits 0 having silently skipped the
    other migration, and the table it should have created is simply absent. The
    deploy reports success and every route touching that table 500s.

    This branch hit it for real: `014_posts.py` here and `014_upload_id_text.py`
    from CF-217 on main both declared revision "014", and all five chain tests
    passed while `posts` would never have been created.
    """
    import collections

    files = _revision_files()
    counts = collections.Counter(rev for _, rev in files)
    dupes = {
        rev: [name for name, r in files if r == rev]
        for rev, count in counts.items()
        if count > 1
    }
    assert not dupes, (
        f"two migrations claim the same revision id: {dupes}. Alembic only "
        "WARNS on this and then resolves the id to one of them, so "
        "`alembic upgrade head` silently skips the other — renumber one."
    )


def test_the_parsed_map_accounts_for_every_file():
    """Guards the guard.

    If `_revisions()` ever silently drops a file again — a duplicate id, a
    format it cannot parse — the count diverges here rather than the chain
    tests going quietly green.
    """
    assert len(_revisions()) == len(_revision_files()), (
        "the revision map has fewer entries than there are migration files, "
        "which means at least one was collapsed or unparsed"
    )
