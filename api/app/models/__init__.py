"""Every model module must be imported here.

`alembic/env.py` builds `target_metadata` from `import app.models`, so a model
that isn't imported is absent from `Base.metadata` even though its table exists
in the database — and the next `alembic revision --autogenerate` emits a
`drop_table` for it. `tests/test_models_registered.py` pins this.
"""
from app.models.user import User
from app.models.team import Team
from app.models.player import Player
from app.models.game import Game
from app.models.clip import Clip
from app.models.collection import Collection, CollectionClip
from app.models.correction import Correction
from app.models.post import Post
from app.models.upload_event import UploadEvent

__all__ = [
    "User",
    "Team",
    "Player",
    "Game",
    "Clip",
    "Collection",
    "CollectionClip",
    "Correction",
    "Post",
    "UploadEvent",
]
