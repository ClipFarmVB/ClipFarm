"""Every model module must reach `Base.metadata`.

`alembic/env.py` builds `target_metadata` from `import app.models`. A model that
`app/models/__init__.py` never imports is therefore absent from the metadata
while its table sits in the database — and the next
`alembic revision --autogenerate` writes `op.drop_table(...)` for it, plus drops
of all its indexes. One unreviewed autogenerate on a later card and the rows are
gone on the next deploy.

CF-109 hit this with `posts`; `corrections` had been quietly in the same state.
Both are cheap to forget again on CF-110 and CF-113, so it's asserted rather
than remembered.
"""
import pathlib
import re

import pytest

pytest.importorskip("sqlalchemy")

import app.models  # noqa: E402,F401  (the import under test)
from app.database import Base  # noqa: E402

MODELS_DIR = pathlib.Path(app.models.__file__).parent


def _declared_tables() -> dict[str, str]:
    """`__tablename__` → module, read from source rather than by importing.

    Importing every module would register it in the metadata and make this test
    pass by doing the very thing it is checking for.
    """
    found = {}
    for path in sorted(MODELS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for name in re.findall(r'__tablename__\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8")):
            found[name] = path.name
    return found


def test_every_model_module_is_imported_by_the_package():
    declared = _declared_tables()
    assert declared, "no models found — the glob or the layout changed"

    missing = {t: m for t, m in declared.items() if t not in Base.metadata.tables}
    assert not missing, (
        f"tables declared but absent from Base.metadata: {missing}. "
        "Add the import to app/models/__init__.py — otherwise alembic "
        "autogenerate will emit a drop_table for them."
    )


def test_the_package_exports_what_it_imports():
    """`__all__` drifting from the imports is how the next one gets dropped —
    the import is what matters, but a stale `__all__` is the tell."""
    for name in app.models.__all__:
        assert hasattr(app.models, name), f"{name} in __all__ but not imported"
