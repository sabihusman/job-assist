"""resolve_des_moines_employer_ats

feat/dm-employer-ingestion. Resolves the ATS + tenant config for five Des
Moines / Iowa employers so they actually enter the ingest plan (which filters
to ``ats IN supported AND ats_handle IS NOT NULL``). Board URLs were verified
by web search, NOT guessed (a wrong Workday shard = a silent dead board).

Data-only — no schema change. The seed loader can't do this: it matches by
``name`` and never overwrites existing non-NULL values, and Principal/Wellmark
already exist at ``ats='unknown'``. So an idempotent ``ON CONFLICT (name) DO
UPDATE`` upsert is the apply mechanism (updates the 2 existing, inserts the 3
new). Re-runnable; safe on a fresh DB too.

Resolved configs (verified):
  Principal Financial Group  iCIMS   handle=principal   (default URL; no config)
                             + DROP role_filter='non_pm_only' (it filtered OUT
                               the PM/PO roles the operator wants)
  Wellmark BCBS              iCIMS   handle=wellmark     careers_url=jobs-wellmark.icims.com
  Athene                     Workday handle=athene       wd5 / athene_careers
  Voya                       Workday handle=godirect     wd5 / voya_jobs  (tenant is godirect, NOT voya)
  EMC Insurance              Workday handle=emcins       wd5 / EMC_Careers

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-06-14 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: str | Sequence[str] | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (name, ats, ats_handle, adapter_config_json_or_None, role_filter, domain, tier)
_EMPLOYERS = [
    ("Principal Financial Group", "icims", "principal", None, None, "principal.com", 2),
    (
        "Wellmark Blue Cross Blue Shield",
        "icims",
        "wellmark",
        '{"careers_url": "https://jobs-wellmark.icims.com"}',
        None,
        "wellmark.com",
        2,
    ),
    (
        "Athene",
        "workday",
        "athene",
        '{"wd_number": "wd5", "site": "athene_careers"}',
        None,
        "athene.com",
        2,
    ),
    (
        "Voya",
        "workday",
        "godirect",
        '{"wd_number": "wd5", "site": "voya_jobs"}',
        None,
        "voya.com",
        2,
    ),
    (
        "EMC Insurance",
        "workday",
        "emcins",
        '{"wd_number": "wd5", "site": "EMC_Careers"}',
        None,
        "emcins.com",
        2,
    ),
]


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import text

    for name, ats, handle, cfg, role_filter, domain, tier in _EMPLOYERS:
        conn.execute(
            text(
                """
                INSERT INTO target_company
                    (id, name, tier, ats, ats_handle, adapter_config, role_filter, domain, source)
                VALUES
                    (gen_random_uuid(), :name, :tier, CAST(:ats AS ats_type), :handle,
                     CAST(:cfg AS jsonb), :role_filter, :domain, 'curated')
                ON CONFLICT (name) DO UPDATE SET
                    ats            = EXCLUDED.ats,
                    ats_handle     = EXCLUDED.ats_handle,
                    adapter_config = EXCLUDED.adapter_config,
                    role_filter    = EXCLUDED.role_filter
                """
            ),
            {
                "name": name,
                "tier": tier,
                "ats": ats,
                "handle": handle,
                "cfg": cfg,
                "role_filter": role_filter,
                "domain": domain,
            },
        )


def downgrade() -> None:
    # Revert the two pre-existing curated rows to the unresolved state and
    # drop the three this migration introduced. Best-effort — curated config
    # is operator data, not load-bearing schema.
    conn = op.get_bind()
    from sqlalchemy import text

    conn.execute(
        text(
            """
            UPDATE target_company
               SET ats = 'unknown', ats_handle = NULL, adapter_config = NULL
             WHERE name IN ('Principal Financial Group', 'Wellmark Blue Cross Blue Shield')
            """
        )
    )
    conn.execute(
        text("DELETE FROM target_company WHERE name IN ('Athene', 'Voya', 'EMC Insurance')")
    )
