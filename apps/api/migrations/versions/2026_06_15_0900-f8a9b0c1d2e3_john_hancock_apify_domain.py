"""john_hancock_apify_domain

feat/jh-apify-domain-override. John Hancock's jobs are indexed in the
Fantastic.jobs/Apify DB under the parent ``manulife.com``, not
``johnhancock.com`` — the filtered ingest returned 0 because the Apify path
targeted ``domain`` (johnhancock.com), which returns nothing even unfiltered.

Set an ``apify_domain`` override in ``adapter_config`` that ONLY the Apify path
reads (services/fantastic_ingest.apify_domain_for). ``domain`` stays
``johnhancock.com`` so Gmail outcome-matching is unaffected. After this,
John Hancock sources its 2 PM/PO roles (Global Digital Product Manager, GRC
Technical Product Owner) via Apify.

Data-only; merges the key so any other adapter_config keys survive. Idempotent.

Revision ID: f8a9b0c1d2e3
Revises: d6e7f8a9b0c1
Create Date: 2026-06-15 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: str | Sequence[str] | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "John Hancock / Manulife US"


def upgrade() -> None:
    from sqlalchemy import text

    op.get_bind().execute(
        text(
            """
            UPDATE target_company
               SET adapter_config = COALESCE(adapter_config, '{}'::jsonb)
                                    || '{"apify_domain": "manulife.com"}'::jsonb
             WHERE name = :name
            """
        ),
        {"name": _NAME},
    )


def downgrade() -> None:
    from sqlalchemy import text

    # Remove just the apify_domain key; null the column if it leaves {}.
    op.get_bind().execute(
        text(
            """
            UPDATE target_company
               SET adapter_config = NULLIF(adapter_config - 'apify_domain', '{}'::jsonb)
             WHERE name = :name
            """
        ),
        {"name": _NAME},
    )
