"""fix draft_status enum casing: SQLAlchemy's Enum(PythonEnum) writes the member's
.name, not .value, so the original enum stores 'PENDING'/'GENERATING'/'READY'/'FAILED'
(uppercase). Two earlier migrations (l5e6f7g8h9i0, p9i0j1k2l3m4) wrongly added the
lowercase 'sent'/'approved' instead of 'SENT'/'APPROVED', so writes of the real values
failed with "invalid input value for enum draft_status". This adds the correctly-cased
values; the earlier lowercase ones are harmless orphaned labels (Postgres can't drop
enum values, so they're left in place, just unused).

Revision ID: s2l3m4n5o6p7
Revises: r1k2l3m4n5o6
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op

revision: str = "s2l3m4n5o6p7"
down_revision: Union[str, None] = "r1k2l3m4n5o6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE draft_status ADD VALUE IF NOT EXISTS 'SENT'")
        op.execute("ALTER TYPE draft_status ADD VALUE IF NOT EXISTS 'APPROVED'")


def downgrade() -> None:
    pass
