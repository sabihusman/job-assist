"""Response schemas for ``GET /admin/outcomes/stats`` (feat/admin-outcomes-stats).

A read-only diagnostic surface for two questions:

  1. Of every ``outcome_event`` row Gmail-classified so far, what's the
     fill rate of ``target_company_id`` — broken down by ``outcome_type``?
     Surfaces "the 131 application_confirmation rows are sitting with no
     company link" the moment it happens.
  2. For one specific ``target_company_id``, how many outcome_events do
     we have, broken down by ``outcome_type``? Lets the operator answer
     "did Gmail catch my MeridianLink application?" without a DB session.

Counts are computed entirely in SQL (GROUP BY) — never pull the
underlying rows into Python.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from job_assist.db.enums import OutcomeType


class OutcomeTypeFill(BaseModel):
    """Per-``outcome_type`` row counts split by whether
    ``target_company_id`` is populated."""

    outcome_type: OutcomeType
    linked_to_company: int = Field(..., description="Rows where ``target_company_id IS NOT NULL``.")
    unlinked: int = Field(..., description="Rows where ``target_company_id IS NULL``.")

    @property
    def total(self) -> int:
        return self.linked_to_company + self.unlinked


class OutcomesOverallStats(BaseModel):
    """Corpus-wide outcome_event breakdown — the ``target_company_id``-fill
    diagnostic. Returned when no ``target_company_id`` query param is given.
    """

    total_rows: int
    total_linked_to_company: int
    total_linked_to_posting: int = Field(
        ...,
        description=(
            "Rows where ``job_posting_id IS NOT NULL``. This is the "
            "deferred-by-design link from ``gmail/backfill.py:9-14``; "
            "the value will be 0 until the application↔posting linker "
            "ships."
        ),
    )
    by_outcome_type: list[OutcomeTypeFill]


class CompanyOutcomeBreakdown(BaseModel):
    """Per-``outcome_type`` count for a single ``target_company_id``."""

    outcome_type: OutcomeType
    count: int


class OutcomesForCompanyStats(BaseModel):
    """outcome_event rows for one ``target_company_id``, grouped by
    ``outcome_type``. Returned when the query param is supplied.
    """

    target_company_id: uuid.UUID
    total_rows: int
    by_outcome_type: list[CompanyOutcomeBreakdown]
