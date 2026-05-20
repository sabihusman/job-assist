"""Contact seed service — idempotent batch insert for ``/admin/seed/contacts``.

Separated from ``seed.py`` (which handles target_company) because the
contact path has its own validation layer (pydantic
``ContactSeedRow``), its own dedup keys (email_primary, linkedin_url),
and its own response shape (four skip-reason counters).

Logging discipline: **count-only**. No names, emails, or LinkedIn URLs
are ever emitted. The Tippie alumni directory is real PII (388 people).
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.db.models.contact import Contact
from job_assist.schemas.contact import ContactSeedResponse, ContactSeedRow

logger = structlog.get_logger(__name__)


async def seed_contacts_from_rows(
    session: AsyncSession,
    rows: list[dict[str, Any]],
) -> ContactSeedResponse:
    """Validate + dedupe + insert each row.

    Per-row exceptions (pydantic validation) are counted as
    ``skipped_invalid`` and do NOT abort the batch — partial seed data
    is better than no seed data. Unexpected DB errors propagate.

    Dedup is case-insensitive via ``LOWER(...)``; the partial unique
    indexes (``uq_contact_email_primary``, ``uq_contact_linkedin_url``)
    are the source of truth and would catch a race even if our explicit
    check missed it.
    """
    inserted = 0
    skipped_invalid = 0
    skipped_duplicate_email = 0
    skipped_duplicate_linkedin = 0

    for raw in rows:
        try:
            row = ContactSeedRow.model_validate(raw)
        except ValidationError:
            skipped_invalid += 1
            continue

        if row.email_primary is not None:
            existing = (
                await session.execute(
                    select(Contact.id).where(
                        func.lower(Contact.email_primary) == row.email_primary.lower()
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped_duplicate_email += 1
                continue

        if row.linkedin_url is not None:
            existing = (
                await session.execute(
                    select(Contact.id).where(
                        func.lower(Contact.linkedin_url) == row.linkedin_url.lower()
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped_duplicate_linkedin += 1
                continue

        # ``model_dump`` rather than spreading attributes — pydantic v2
        # gives us a clean dict that drops unset fields.
        session.add(Contact(**row.model_dump()))
        inserted += 1

    await session.commit()

    total = inserted + skipped_duplicate_email + skipped_duplicate_linkedin + skipped_invalid
    # NB: no PII in this log line — counts only.
    logger.info(
        "contact_seed.completed",
        inserted=inserted,
        skipped_duplicate_email=skipped_duplicate_email,
        skipped_duplicate_linkedin=skipped_duplicate_linkedin,
        skipped_invalid=skipped_invalid,
        total=total,
    )

    return ContactSeedResponse(
        inserted=inserted,
        skipped_duplicate_email=skipped_duplicate_email,
        skipped_duplicate_linkedin=skipped_duplicate_linkedin,
        skipped_invalid=skipped_invalid,
        total=total,
    )
