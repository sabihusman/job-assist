"""Ingestion service — orchestrates adapter fetching and DB upserts.

Flow per handle
───────────────
1. Create IngestRun(status='running')
2. Resolve canonical_company_name from target_company.ats_handle
3. adapter.fetch_postings(handle) → list[RawPosting]
4. For each posting:
   a. adapter.normalize(raw, canonical_name)
   b. Upsert JobPosting by content_hash
   c. Upsert PostingSource by (ats, source_job_id)
5. Finalise IngestRun (status='success', counters, finished_at)
6. On exception: mark IngestRun failed, do not rollback partial work

Idempotency contract
────────────────────
Re-running ingest_source for the same handle with identical data
produces zero new rows and zero changed fields beyond the
per-run timestamps (last_seen_at, fetched_at).
"""

from __future__ import annotations

import traceback
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.adapters.base import Adapter
from job_assist.db.models.ingest_run import IngestRun
from job_assist.db.models.job_posting import JobPosting
from job_assist.db.models.posting_source import PostingSource
from job_assist.db.models.target_company import TargetCompany

logger = structlog.get_logger(__name__)


class IngestionService:
    """Stateless service — all state lives in the DB session."""

    async def ingest_source(
        self,
        adapter: Adapter,
        handle: str,
        session: AsyncSession,
    ) -> IngestRun:
        """Ingest all postings for *handle* using *adapter* into *session*."""
        run = IngestRun(
            source=adapter.ats,
            started_at=datetime.now(tz=UTC),
            status="running",
        )
        session.add(run)
        await session.flush()

        postings_fetched = 0
        postings_new = 0
        postings_updated = 0

        try:
            # ── Resolve canonical company name ────────────────────────────────
            tc_row = await session.execute(
                select(TargetCompany).where(TargetCompany.ats_handle == handle)
            )
            target_company = tc_row.scalar_one_or_none()
            canonical_name: str = (
                target_company.name if target_company else handle.replace("-", " ").title()
            )

            # ── Fetch ─────────────────────────────────────────────────────────
            raw_postings = await adapter.fetch_postings(handle)
            postings_fetched = len(raw_postings)

            for raw in raw_postings:
                norm = adapter.normalize(raw, canonical_name)

                # ── Upsert JobPosting by content_hash ─────────────────────────
                jp_row = await session.execute(
                    select(JobPosting).where(JobPosting.content_hash == norm.content_hash)
                )
                job_posting = jp_row.scalar_one_or_none()

                if job_posting is None:
                    job_posting = JobPosting(
                        canonical_company_name=norm.canonical_company_name,
                        normalized_title=norm.normalized_title,
                        raw_title=norm.raw_title,
                        location_raw=norm.location_raw,
                        locations_normalized=norm.locations_normalized,
                        remote_type=norm.remote_type,
                        salary_min=norm.salary_min,
                        salary_max=norm.salary_max,
                        salary_currency=norm.salary_currency,
                        salary_period=norm.salary_period,
                        seniority_level=norm.seniority_level,
                        role_family=norm.role_family,
                        department=norm.department,
                        team=norm.team,
                        jd_text=norm.jd_text,
                        jd_text_hash=norm.jd_text_hash,
                        content_hash=norm.content_hash,
                        posted_at=norm.posted_at,
                        first_seen_at=norm.first_seen_at,
                        last_seen_at=norm.last_seen_at,
                        should_embed=norm.should_embed,
                        target_company_id=(target_company.id if target_company else None),
                    )
                    session.add(job_posting)
                    await session.flush()
                    postings_new += 1
                else:
                    job_posting.last_seen_at = datetime.now(tz=UTC)
                    if job_posting.jd_text_hash != norm.jd_text_hash:
                        job_posting.jd_text = norm.jd_text
                        job_posting.jd_text_hash = norm.jd_text_hash
                    if target_company is not None and job_posting.target_company_id is None:
                        job_posting.target_company_id = target_company.id
                    # Self-heal the new department / team columns on re-ingest:
                    # only fill when the column is currently NULL so we don't
                    # overwrite a value the operator may have edited by hand.
                    if job_posting.department is None and norm.department is not None:
                        job_posting.department = norm.department
                    if job_posting.team is None and norm.team is not None:
                        job_posting.team = norm.team
                    postings_updated += 1

                # ── Upsert PostingSource by (ats, source_job_id) ──────────────
                ps_row = await session.execute(
                    select(PostingSource).where(
                        PostingSource.ats == norm.ats,
                        PostingSource.source_job_id == norm.source_job_id,
                    )
                )
                posting_source = ps_row.scalar_one_or_none()

                now = datetime.now(tz=UTC)
                if posting_source is None:
                    posting_source = PostingSource(
                        job_posting_id=job_posting.id,
                        ats=norm.ats,
                        source_job_id=norm.source_job_id,
                        source_url=norm.source_url,
                        apply_url=norm.apply_url,
                        raw_payload=norm.raw_payload,
                        parser_version=norm.parser_version,
                        fetch_status=norm.fetch_status,
                        fetched_at=now,
                    )
                    session.add(posting_source)
                else:
                    posting_source.raw_payload = norm.raw_payload
                    posting_source.fetched_at = now

                await session.flush()

            run.status = "success"  # type: ignore[assignment]
            run.finished_at = datetime.now(tz=UTC)
            run.postings_fetched = postings_fetched
            run.postings_new = postings_new
            run.postings_updated = postings_updated
            await session.commit()

            logger.info(
                "ingestion.complete",
                handle=handle,
                ats=adapter.ats,
                new=postings_new,
                updated=postings_updated,
                fetched=postings_fetched,
            )

        except Exception as exc:
            run.status = "failed"  # type: ignore[assignment]
            run.finished_at = datetime.now(tz=UTC)
            run.error_message = str(exc)
            run.error_traceback = traceback.format_exc()
            run.postings_fetched = postings_fetched
            run.postings_new = postings_new
            run.postings_updated = postings_updated
            await session.commit()
            logger.exception("ingestion.failed", handle=handle, ats=adapter.ats)

        return run
