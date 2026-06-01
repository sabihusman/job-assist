"""Resume-version outcome analytics (feat/resume-version-tracking).

Correlates which tailored resume variant was sent to which application
against company-level outcomes. Two cuts:

  1. by_version  — per resume_version: # applications, # companies that
     rejected, # companies that confirmed (rejection-rate signal).
  2. funnel      — per (resume_version, outcome_type): how deep the
     pipeline went (pre-screen vs post-screen vs interview vs offer).

HONEST CONSTRAINT (carried from the Read-First): outcomes link at
COMPANY level — ``outcome_event.job_posting_id`` is uniformly NULL (the
per-posting linker was never built), so the join is
``posting_action → job_posting.target_company_id →
outcome_event.target_company_id``. Attribution is therefore per-company,
not per-application: if the operator sent two different resume versions
to two roles at the SAME company and that company sent one rejection,
the rejection attributes to BOTH versions. The ``ambiguous_companies``
field flags companies where >1 distinct resume version was sent, so the
operator reads those with caution. Useful at company granularity; not
posting-exact until the deferred linker lands.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.db.models import JobPosting, OutcomeEvent, PostingAction, ResumeVersion

_REJECTION_TYPES = (
    "rejection_pre_screen",
    "rejection_post_screen",
    "rejection_post_interview",
)


async def resume_analytics(session: AsyncSession) -> dict[str, Any]:
    """Compute the resume→outcome analytics. All aggregation in SQL."""
    # ── Applications tagged with a resume version, joined to their company ──
    # One row per (resume_version, applied posting, company). Distinct
    # postings/companies are counted from this base.
    applied = (
        select(
            PostingAction.resume_version_id.label("rv_id"),
            PostingAction.job_posting_id.label("posting_id"),
            JobPosting.target_company_id.label("company_id"),
        )
        .join(JobPosting, JobPosting.id == PostingAction.job_posting_id)
        .where(PostingAction.action_type == "applied")
        .where(PostingAction.resume_version_id.is_not(None))
    ).subquery()

    # ── (1) by_version ──────────────────────────────────────────────────────
    rej_exists = (
        select(OutcomeEvent.id)
        .where(OutcomeEvent.target_company_id == applied.c.company_id)
        .where(OutcomeEvent.outcome_type.in_(_REJECTION_TYPES))
        .exists()
    )
    conf_exists = (
        select(OutcomeEvent.id)
        .where(OutcomeEvent.target_company_id == applied.c.company_id)
        .where(OutcomeEvent.outcome_type == "application_confirmation")
        .exists()
    )
    by_version_stmt = (
        select(
            ResumeVersion.id,
            ResumeVersion.label,
            ResumeVersion.angle,
            func.count(func.distinct(applied.c.posting_id)).label("applications"),
            func.count(func.distinct(applied.c.company_id)).label("companies"),
            # COUNT(DISTINCT CASE WHEN <exists> THEN company_id END): the
            # portable idiom for a conditional distinct count. NULLs (rows
            # failing the predicate) are not counted. Avoids the aggregate
            # FILTER clause, which asyncpg rejected when nested inside
            # count(distinct(...)).
            func.count(func.distinct(case((rej_exists, applied.c.company_id), else_=None))).label(
                "companies_rejected"
            ),
            func.count(func.distinct(case((conf_exists, applied.c.company_id), else_=None))).label(
                "companies_confirmed"
            ),
        )
        .select_from(ResumeVersion)
        .join(applied, applied.c.rv_id == ResumeVersion.id)
        .group_by(ResumeVersion.id, ResumeVersion.label, ResumeVersion.angle)
        .order_by(func.count(func.distinct(applied.c.posting_id)).desc())
    )
    by_version = [
        {
            "resume_version_id": str(r.id),
            "label": r.label,
            "angle": r.angle,
            "applications": int(r.applications),
            "companies": int(r.companies),
            "companies_rejected": int(r.companies_rejected),
            "companies_confirmed": int(r.companies_confirmed),
        }
        for r in (await session.execute(by_version_stmt)).all()
    ]

    # ── (2) funnel: (resume_version, outcome_type) → distinct companies ──────
    funnel_stmt = (
        select(
            ResumeVersion.label,
            OutcomeEvent.outcome_type,
            func.count(func.distinct(applied.c.company_id)).label("companies"),
        )
        .select_from(ResumeVersion)
        .join(applied, applied.c.rv_id == ResumeVersion.id)
        .join(OutcomeEvent, OutcomeEvent.target_company_id == applied.c.company_id)
        .group_by(ResumeVersion.label, OutcomeEvent.outcome_type)
        .order_by(ResumeVersion.label, OutcomeEvent.outcome_type)
    )
    funnel = [
        {
            "label": r.label,
            "outcome_type": str(
                r.outcome_type.value if hasattr(r.outcome_type, "value") else r.outcome_type
            ),
            "companies": int(r.companies),
        }
        for r in (await session.execute(funnel_stmt)).all()
    ]

    # ── Ambiguity flag: companies that received >1 distinct resume version ──
    # Their outcomes can't be cleanly attributed to one version (company-
    # level linkage). Surface so the operator discounts them.
    ambig_stmt = (
        select(
            applied.c.company_id,
            func.count(func.distinct(applied.c.rv_id)).label("n_versions"),
        )
        .group_by(applied.c.company_id)
        .having(func.count(func.distinct(applied.c.rv_id)) > 1)
    )
    ambiguous = [
        {"company_id": str(r.company_id), "distinct_resume_versions": int(r.n_versions)}
        for r in (await session.execute(ambig_stmt)).all()
    ]

    return {
        "by_version": by_version,
        "funnel": funnel,
        "ambiguous_companies": ambiguous,
        "attribution_note": (
            "Outcomes link at COMPANY level (outcome_event.job_posting_id is "
            "NULL). Attribution is per-company, not per-application. Companies "
            "in 'ambiguous_companies' received >1 resume version — read their "
            "outcome contribution with caution."
        ),
    }


__all__ = ["resume_analytics"]
