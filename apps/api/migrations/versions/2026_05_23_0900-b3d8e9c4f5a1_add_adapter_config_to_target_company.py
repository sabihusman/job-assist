"""add_adapter_config_to_target_company

Adds a nullable ``adapter_config JSONB`` column to ``target_company``
to hold per-tenant configuration that Greenhouse / Lever / Ashby
don't need but Workday (PR #33) does:

  {"wd_number": "wd5", "site": "External"}

Workday's public job-board API is per-tenant rather than centralized
— each customer has its own subdomain shard (``wd1``…``wd9``) and
career-site identifier. Encoding those two strings into ``ats_handle``
would conflate identity (the tenant) with config (the shard + site);
a typed JSONB cleanly separates them.

The column is nullable; existing Greenhouse / Lever / Ashby rows
leave it NULL. Future adapters (iCIMS, Taleo) can reuse the same
column without another migration.

Revision ID: b3d8e9c4f5a1
Revises: a9c7e1b4f2d6
Create Date: 2026-05-23 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d8e9c4f5a1"
down_revision: str | Sequence[str] | None = "a9c7e1b4f2d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "target_company",
        sa.Column(
            "adapter_config",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("target_company", "adapter_config")
