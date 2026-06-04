"""resolve_des_moines_employer_ats

feat/dm-employer-ingestion. Resolves the ATS + tenant config for four Des
Moines / Iowa employers so they actually enter the ingest plan (which filters
to ``ats IN supported AND ats_handle IS NOT NULL``). Every board below was
fetched LIVE before landing (a wrong Workday shard = a silent dead board):
  Athene  wd5/athene_careers → 47 jobs (Iowa-dense)
  Voya    godirect/voya_jobs → 75 jobs (tenant verified as godirect, NOT voya)
  EMC     wd5/EMC_Careers     → 49 jobs
  Principal careers-principal.icims.com → live, instance self-identifies as
            Principal (iCIMS renders rows via JS, so row counts confirm on the
            operator's post-merge ingest run, not via raw HTTP).

Wellmark is deliberately EXCLUDED: its careers site
(``careers.smartrecruiters.com/WellmarkInc``) is SmartRecruiters, NOT iCIMS,
and SmartRecruiters is not in ``_INGESTABLE_ATS``. Every iCIMS subdomain
(``jobs-wellmark``, ``careers-wellmark``, ``wellmark``) 404s. Setting it to
iCIMS would be the exact silent-dead-board failure this PR exists to prevent;
it stays at ``ats='unknown'`` until a SmartRecruiters adapter exists.

Data-only — no schema change. The seed loader can't do this: it matches by
``name`` and never overwrites existing non-NULL values, and Principal already
exists at ``ats='unknown'``. So an idempotent ``ON CONFLICT (name) DO UPDATE``
upsert is the apply mechanism (updates Principal in prod, inserts the 3
Workday employers). Re-runnable; safe on a fresh DB too.

Resolved configs (verified):
  Principal Financial Group  iCIMS   handle=principal   (default URL; no config)
                             + DROP role_filter='non_pm_only' (it filtered OUT
                               the PM/PO roles the operator wants)
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
    # Inverse on the migration chain: DELETE every row this migration touched.
    # On a fresh chain these rows are absent before this revision (the earlier
    # remove_target_company_seed_data migration wiped the original seed,
    # including Principal), so the chain-correct inverse is removal — NOT an
    # UPDATE-to-unknown, which would leave a Principal row that collides with
    # remove_target_company_seed_data's own re-insert on full downgrade.
    # Migrations run forward-only in prod; a real prod downgrade would drop the
    # runtime-seeded Principal row, which is acceptable for a reversed revision.
    conn = op.get_bind()
    from sqlalchemy import text

    names = [name for (name, *_rest) in _EMPLOYERS]
    conn.execute(
        text("DELETE FROM target_company WHERE name = ANY(:names)"),
        {"names": names},
    )
