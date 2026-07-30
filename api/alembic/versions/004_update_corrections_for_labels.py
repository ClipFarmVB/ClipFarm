"""Update corrections table to support up to 2 labels

Revision ID: 004
Revises: 003
Create Date: 2026-03-31
"""
from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename corrected_action to corrected_label_1, add corrected_label_2
    op.execute("""
        ALTER TABLE corrections
        RENAME COLUMN corrected_action TO corrected_label_1;
    """)
    op.execute("""
        ALTER TABLE corrections
        ALTER COLUMN corrected_label_1 TYPE VARCHAR(50);
    """)
    op.execute("""
        ALTER TABLE corrections
        ADD COLUMN corrected_label_2 VARCHAR(50) DEFAULT NULL;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE corrections DROP COLUMN corrected_label_2")
    op.execute("ALTER TABLE corrections RENAME COLUMN corrected_label_1 TO corrected_action")
