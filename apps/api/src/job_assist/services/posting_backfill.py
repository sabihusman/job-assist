"""One-shot backfills for ``job_posting`` columns added after the rows exist.

Currently exposes ``backfill_department_team`` (PR #28a). The function
reads each ATS's native shape out of ``posting_source.raw_payload`` and
writes the extracted values onto ``job_posting``. Idempotent — the
``WHERE department IS NULL AND team IS NULL`` guard skips rows that have
already been filled (whether by this backfill, by the daily-ingest
self-heal in ``ingestion.py``, or by a manual UPDATE).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.adapters.normalization import normalize_org_field
from job_assist.db.models import JobPosting, PostingSource

logger = logging.getLogger(__name__)


@dataclass
class BackfillReport:
    """Counters returned to the admin endpoint."""

    candidates: int = 0  # job_posting rows with department IS NULL AND team IS NULL
    updated: int = 0  # rows where at least one of (department, team) was set
    skipped_no_source: int = 0  # candidate with zero posting_source rows
    skipped_no_data: int = 0  # source(s) had no recognisable dept/team fields


# Per-ATS extractor — pure functions over a raw_payload dict. Each returns
# ``(department, team)`` strings (raw, pre-normalisation) or ``(None, None)``
# when the path isn't present.


def _extract_greenhouse(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Greenhouse exposes ``departments`` as an array of {id, name, ...}."""
    departments = payload.get("departments")
    if isinstance(departments, list) and departments:
        first = departments[0]
        if isinstance(first, dict):
            name = first.get("name")
            if isinstance(name, str):
                return name, None
    return None, None


def _extract_lever(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Lever nests department + team under ``categories``."""
    categories = payload.get("categories")
    if not isinstance(categories, dict):
        return None, None
    dept = categories.get("department")
    team = categories.get("team")
    return (
        dept if isinstance(dept, str) else None,
        team if isinstance(team, str) else None,
    )


def _extract_ashby(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Ashby exposes department + team as top-level siblings of ``title``."""
    dept = payload.get("department")
    team = payload.get("team")
    return (
        dept if isinstance(dept, str) else None,
        team if isinstance(team, str) else None,
    )


_EXTRACTORS = {
    "greenhouse": _extract_greenhouse,
    "lever": _extract_lever,
    "ashby": _extract_ashby,
}


def _pick_most_recent(sources: list[PostingSource]) -> PostingSource | None:
    """Return the source with the latest ``fetched_at`` — tiebreaker: any."""
    if not sources:
        return None
    return max(sources, key=lambda s: s.fetched_at)


async def backfill_department_team(session: AsyncSession) -> BackfillReport:
    """Promote department / team from ``raw_payload`` to typed columns.

    Sweep is sequential and small — at most a few thousand rows across the
    operator's whole target list. Per-row UPDATE keeps the SQL surface
    obvious; a single bulk UPDATE FROM would be faster but harder to test.
    """
    report = BackfillReport()

    # Candidate rows: both columns NULL. Either column being already set
    # means we leave the row alone (operator may have set it manually, or
    # the daily ingest's self-heal already filled it).
    candidate_rows = (
        (
            await session.execute(
                select(JobPosting).where(
                    JobPosting.department.is_(None),
                    JobPosting.team.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    report.candidates = len(candidate_rows)

    for posting in candidate_rows:
        # Pull every posting_source for this job_posting; prefer the most
        # recent one (whichever ATS scraped this row last has the freshest
        # raw_payload). Two sources are rare in practice but the schema
        # supports it.
        sources = (
            (
                await session.execute(
                    select(PostingSource).where(PostingSource.job_posting_id == posting.id)
                )
            )
            .scalars()
            .all()
        )
        chosen = _pick_most_recent(list(sources))
        if chosen is None:
            report.skipped_no_source += 1
            continue

        extractor = _EXTRACTORS.get(str(chosen.ats))
        if extractor is None:
            # Unsupported ATS (workday, etc.) — nothing to extract today.
            report.skipped_no_data += 1
            continue

        # raw_payload is typed as dict in the model but JSONB can be any
        # JSON value at the SQL layer; guard for the unexpected case.
        payload = chosen.raw_payload if isinstance(chosen.raw_payload, dict) else {}
        raw_dept, raw_team = extractor(payload)
        department = normalize_org_field(raw_dept)
        team = normalize_org_field(raw_team)

        if department is None and team is None:
            report.skipped_no_data += 1
            continue

        # Re-check the NULL guard at write time — defensive against a
        # concurrent ingest that may have populated the column between the
        # SELECT above and now.
        if posting.department is None and department is not None:
            posting.department = department
        if posting.team is None and team is not None:
            posting.team = team
        posting.last_seen_at = posting.last_seen_at or datetime.now(tz=UTC)
        report.updated += 1

    await session.commit()
    logger.info(
        "posting_backfill.department_team.complete",
        extra={
            "candidates": report.candidates,
            "updated": report.updated,
            "skipped_no_source": report.skipped_no_source,
            "skipped_no_data": report.skipped_no_data,
        },
    )
    return report
