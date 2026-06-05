"""operator_profile_boston_geo

chore/operator-profile-boston-geo. Add ``boston`` to the singleton operator
profile's ``geo_whitelist`` (id=1) so Boston roles surface in the default
triage queue. The live hard-rule config is built from this DB row
(triage.config.hard_rule_config_from_profile), NOT the HardRuleConfig
dataclass default — so the whitelist must change HERE to affect filtering.

``boston`` substring-matches "Boston, Massachusetts", so e.g. John Hancock's
"Global Digital Product Manager" (Boston + Toronto) now passes geo_whitelist.
Toronto is deliberately NOT added (operator excludes Canada), so Toronto-only
roles ("GRC Technical Product Owner") stay correctly filtered.

NOTE: this changes geo-filtering for ALL employers, not just John Hancock —
Boston roles from any source now reach the default queue.

Data-only; idempotent (the ``@>`` guard skips if already present). Append
preserves the operator's existing ordering and entries.

Revision ID: b7c8d9e0f1a2
Revises: f8a9b0c1d2e3
Create Date: 2026-06-15 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from sqlalchemy import text

    # Append only when absent so a re-run (or a profile already carrying it) is
    # a no-op rather than a duplicate.
    op.get_bind().execute(
        text(
            """
            UPDATE operator_profile
               SET geo_whitelist = geo_whitelist || '["boston"]'::jsonb
             WHERE id = 1
               AND NOT (geo_whitelist @> '["boston"]'::jsonb)
            """
        )
    )


def downgrade() -> None:
    from sqlalchemy import text

    # Strip every "boston" element, preserving order of the rest.
    op.get_bind().execute(
        text(
            """
            UPDATE operator_profile
               SET geo_whitelist = COALESCE(
                   (
                       SELECT jsonb_agg(elem ORDER BY ord)
                         FROM jsonb_array_elements(geo_whitelist)
                              WITH ORDINALITY AS t(elem, ord)
                        WHERE elem <> '"boston"'::jsonb
                   ),
                   '[]'::jsonb
               )
             WHERE id = 1
               AND geo_whitelist @> '["boston"]'::jsonb
            """
        )
    )
