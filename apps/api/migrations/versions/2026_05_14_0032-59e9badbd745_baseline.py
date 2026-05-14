"""baseline

Revision ID: 59e9badbd745
Revises:
Create Date: 2026-05-14 00:32:31.392690

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "59e9badbd745"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""


def downgrade() -> None:
    """Downgrade schema."""
