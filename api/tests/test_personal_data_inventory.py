"""The personal-data inventory must match the schema (CF-75 / CF-88).

`docs/privacy/data-inventory.md` is the engineering input to a privacy policy:
what personal data this system stores, and what happens to it when an account
goes away. A policy written against a stale inventory makes promises the code
does not keep, and that is the failure mode worth a test — a document nobody
re-reads drifts silently, and the drift is invisible until someone acts on it.

So the inventory is not prose alone. Every column on `users` and every table
that references it is classified here, and a new one fails this file until it
is classified and the document updated. Deliberately narrow: it asserts that
personal data is DESCRIBED, never that a particular design is right. Those are
decisions for a human, and several of them are open (see the module docstring
of the deletion test below).
"""
import pytest

pytest.importorskip("sqlalchemy")

from app.database import Base  # noqa: E402
import app.models  # noqa: E402,F401  (registers every model on the metadata)

# What each column on `users` is, for someone deciding how it must be handled.
#
# "identifier" reaches a real person; "credential" authenticates them;
# "profile" is what they chose to publish; "operational" exists to run the
# service and describes the account rather than the person.
USER_COLUMN_CLASSES = {
    "id": "operational",
    "email": "identifier",
    "hashed_password": "credential",
    "created_at": "operational",
    "username": "identifier",          # public handle; also how others @ them
    "display_name": "profile",
    "bio": "profile",
    "avatar_url": "profile",
    "is_private": "operational",
    "username_changed_at": "operational",
    "username_is_generated": "operational",
}

VALID_CLASSES = {"identifier", "credential", "profile", "operational"}

# Every table referencing users.id, and what the SCHEMA does to its rows when
# the user row is deleted. Read from the live metadata below, so this map is a
# claim about the database rather than a note about it.
#
# None means no ON DELETE clause, which in PostgreSQL is NO ACTION: the delete
# is REFUSED while any row here points at the user. That is the safe failure
# (nothing is silently orphaned) but it does mean a user who owns any game,
# team or collection cannot be deleted at all — see the document.
USER_REFERENCING_TABLES = {
    "collections": None,
    "corrections": "CASCADE",
    "games": None,
    "posts": "CASCADE",
    "teams": None,
    "upload_events": "CASCADE",
}


def _user_foreign_keys():
    """(table name, column name, ondelete) for every FK pointing at users.id."""
    found = []
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == "users":
                found.append((table.name, fk.parent.name, fk.ondelete))
    return sorted(found)


def test_every_user_column_is_classified():
    """A new column on `users` is new personal data until someone says otherwise."""
    actual = {c.name for c in Base.metadata.tables["users"].columns}
    documented = set(USER_COLUMN_CLASSES)
    assert actual == documented, (
        "users table and the inventory disagree.\n"
        f"  in the schema, not classified: {sorted(actual - documented)}\n"
        f"  classified, not in the schema: {sorted(documented - actual)}\n"
        "Classify it here and update docs/privacy/data-inventory.md in the same "
        "PR. A policy written against a stale inventory promises what the code "
        "does not do."
    )


def test_the_classes_are_the_documented_ones():
    unknown = {c for c in USER_COLUMN_CLASSES.values() if c not in VALID_CLASSES}
    assert not unknown, f"unknown data classes {sorted(unknown)}; document them first"


def test_every_table_referencing_users_is_documented():
    actual = {table for table, _, _ in _user_foreign_keys()}
    documented = set(USER_REFERENCING_TABLES)
    assert actual == documented, (
        "tables referencing users.id and the inventory disagree.\n"
        f"  referencing users, undocumented: {sorted(actual - documented)}\n"
        f"  documented, no longer referencing: {sorted(documented - actual)}\n"
        "Add it to the inventory with what happens to its rows when the account "
        "is deleted."
    )


def delete_behaviour_mismatches(foreign_keys, documented):
    """Rule as a callable, so the self-tests below exercise it rather than
    restate it. A self-test that re-implements the comparison passes against a
    loosened comparison, which is the failure mode this file is about.
    """
    out = []
    for table, column, ondelete in foreign_keys:
        if table not in documented:
            out.append(f"{table}.{column} is undocumented")
        elif ondelete != documented[table]:
            out.append(
                f"{table}.{column} -> users.id is ON DELETE {ondelete!r}, "
                f"inventory says {documented[table]!r}"
            )
    return out


def test_the_mismatch_rule_spots_a_changed_cascade():
    assert delete_behaviour_mismatches([("games", "owner_id", "CASCADE")],
                                       {"games": None}) != []


def test_the_mismatch_rule_spots_a_removed_cascade():
    assert delete_behaviour_mismatches([("posts", "author_id", None)],
                                       {"posts": "CASCADE"}) != []


def test_the_mismatch_rule_spots_an_undocumented_table():
    assert delete_behaviour_mismatches([("invoices", "user_id", "CASCADE")], {}) != []


def test_the_mismatch_rule_is_quiet_when_they_agree():
    assert delete_behaviour_mismatches([("games", "owner_id", None)],
                                       {"games": None}) == []


def test_the_identifiers_and_the_credential_are_not_reclassified():
    """These three are not judgement calls, and downgrading one is how an
    inventory stops describing the thing that matters. The rest of the
    classification is deliberately left open to revision."""
    assert USER_COLUMN_CLASSES["email"] == "identifier"
    assert USER_COLUMN_CLASSES["username"] == "identifier"
    assert USER_COLUMN_CLASSES["hashed_password"] == "credential"


def test_the_recorded_delete_behaviour_matches_the_schema():
    """The map is a claim about the database, so a migration that changes a
    cascade must change it here too.

    This is the half that would otherwise rot: `ondelete` lives in a migration,
    nothing reads it back, and flipping one is a one-word diff that changes
    whether a deletion is possible at all.
    """
    problems = delete_behaviour_mismatches(_user_foreign_keys(), USER_REFERENCING_TABLES)
    assert not problems, (
        "; ".join(problems)
        + ". Update the inventory and re-check what it means for account deletion."
    )


def test_account_deletion_is_still_blocked_by_at_least_one_table():
    """Records the state the document describes, and fails when it changes.

    Today three tables (`games`, `teams`, `collections`) reference users.id with
    no ON DELETE clause, so PostgreSQL refuses to delete a user who owns any of
    them — which is every real user. There is no account-deletion endpoint
    either, so nothing exercises this yet.

    This is NOT asserting that the current design is correct; it is asserting
    that the document is accurate. When erasure is implemented, this test fails
    and the document gets rewritten alongside it — which is the point.
    """
    blocking = sorted(t for t, _, od in _user_foreign_keys() if od is None)
    assert blocking, (
        "no table blocks user deletion any more. If account deletion is now "
        "possible, docs/privacy/data-inventory.md needs rewriting — it currently "
        "states that erasure cannot be performed."
    )
    assert blocking == ["collections", "games", "teams"], (
        f"the set of tables blocking account deletion changed to {blocking}; "
        "update the inventory's deletion section"
    )
