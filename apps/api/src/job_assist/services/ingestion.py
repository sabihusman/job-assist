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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

from job_assist.adapters.base import Adapter, HandleNotFoundError, NormalizedPosting
from job_assist.db.models.closed_channel import ClosedChannel
from job_assist.db.models.ingest_run import IngestRun
from job_assist.db.models.job_posting import JobPosting
from job_assist.db.models.operator_profile import OperatorProfile
from job_assist.db.models.posting_source import PostingSource
from job_assist.db.models.target_company import TargetCompany
from job_assist.triage.config import HardRuleConfig, hard_rule_config_from_profile

logger = structlog.get_logger(__name__)


class IngestionService:
    """Stateless service — all state lives in the DB session."""

    async def ingest_source(
        self,
        adapter: Adapter,
        handle: str,
        session: AsyncSession,
        *,
        apply_title_prefilter: bool = False,
        target_company: TargetCompany | None = None,
    ) -> IngestRun:
        """Ingest all postings for *handle* using *adapter* into *session*.

        Args:
          target_company: When provided, the company link is taken from THIS
            row instead of being resolved by ``ats_handle == handle``. The
            Fantastic.jobs/Apify path uses it because those employers are
            targeted by DOMAIN and may have a NULL ``ats_handle`` (Capital One,
            John Hancock) — resolving by handle would lose the tier/company link.
            Native adapters pass ``None`` → unchanged handle-based resolution.
          apply_title_prefilter: When True, postings whose
            ``adapter.peek_title(raw)`` fails the PM keep-list in
            ``adapters/title_filter.should_keep_title`` are dropped
            **before** ``normalize()`` runs, so non-PM rows never reach
            the DB. **OPT-IN per call** so the existing curated-30 cron
            (which deliberately ingests sales/eng/ops roles for its
            Companies/Stats surfaces) keeps full-corpus behaviour. The
            Slice 2 broad-ingest cron will pass ``True``.

            When False (default), this is a no-op — behavior is
            byte-identical to pre-PR ingestion.
        """
        # Lazy import — keeps the title_filter module out of the
        # service's import-time cost when the filter isn't used.
        from job_assist.adapters.title_filter import should_keep_title

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
        postings_skipped_title_filter = 0

        try:
            # Per-run setup, loaded ONCE so the per-posting loop stays O(1):
            # resolve the company link, then the operator profile + hard-rule
            # inputs (see each helper for the contract).
            target_company, canonical_name = await self._resolve_company_and_name(
                session, handle, target_company
            )
            op_row = await session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
            operator_profile = op_row.scalar_one_or_none()
            hard_rule_config, active_closed_channel = await self._load_hard_rule_inputs(
                session, operator_profile, target_company
            )

            # ── Fetch ─────────────────────────────────────────────────────────
            raw_postings = await adapter.fetch_postings(handle)
            postings_fetched = len(raw_postings)

            for raw in raw_postings:
                # ── Title pre-filter (Slice 1: opt-in for broad ingest) ─────
                # ``apply_title_prefilter=False`` keeps the curated-30 path
                # byte-identical — peek_title is not even called when the
                # flag is off, so an adapter that didn't get a peek_title
                # override would still ingest. The check is structured
                # this way (flag first) on purpose for that reason.
                if apply_title_prefilter and not should_keep_title(adapter.peek_title(raw)):
                    postings_skipped_title_filter += 1
                    continue

                norm = adapter.normalize(raw, canonical_name)

                # Each sub-step is an independently-testable helper; the
                # sequence (upsert → score → hard-rules → source → flush) and
                # its per-row flush boundary are unchanged from the inline
                # version.
                job_posting, is_new = await self._upsert_job_posting(session, norm, target_company)
                if is_new:
                    postings_new += 1
                else:
                    postings_updated += 1

                self._auto_score(job_posting, operator_profile, target_company)
                self._eval_hard_rules(
                    job_posting, target_company, active_closed_channel, hard_rule_config
                )
                await self._upsert_posting_source(session, norm, job_posting)

                await session.flush()

            run.status = "success"  # type: ignore[assignment]
            run.finished_at = datetime.now(tz=UTC)
            run.postings_fetched = postings_fetched
            run.postings_new = postings_new
            run.postings_updated = postings_updated
            # fix(audit per-pipeline health): stamp the sweep time on the
            # company row for EVERY adapter path, not just the Apify one —
            # the curated_fresh health check reads
            # MAX(last_swept_at WHERE source='curated'), so the free-adapter
            # daily cron must leave the same footprint fantastic_ingest does.
            # Never stamped on a generic failure (see the except below) so a
            # dead/erroring sweep still trips the freshness alarm.
            if target_company is not None:
                target_company.last_swept_at = datetime.now(tz=UTC)
            await session.commit()

            logger.info(
                "ingestion.complete",
                handle=handle,
                ats=adapter.ats,
                new=postings_new,
                updated=postings_updated,
                fetched=postings_fetched,
                # Surface the title-prefilter drop count to logs even
                # though it's not persisted on IngestRun. Useful for
                # tuning the keep-list when the broad-ingest cron lands;
                # zero when ``apply_title_prefilter=False`` (no overhead
                # for the curated-30 path).
                skipped_title_filter=postings_skipped_title_filter,
            )

        except HandleNotFoundError as exc:
            # Bestiary 5.9 — distinct status for "upstream 404 on listing"
            # so the operator can tell a stale ATS config from a generic
            # failure. The adapter raised this BEFORE returning any
            # postings, so postings_fetched is 0 and the run carries no
            # partial work to commit.
            run.status = "handle_not_found"  # type: ignore[assignment]
            run.finished_at = datetime.now(tz=UTC)
            run.error_message = str(exc)
            run.error_traceback = None  # not a stack-worthy failure
            run.postings_fetched = 0
            run.postings_new = 0
            run.postings_updated = 0
            # The sweep DID visit this employer — a stale board (404) is
            # surfaced via the run status, not via the stale-cohort alarm.
            # Same contract as fantastic_ingest's status != 'failed' guard.
            if target_company is not None:
                target_company.last_swept_at = datetime.now(tz=UTC)
            await session.commit()
            logger.warning(
                "ingestion.handle_not_found",
                handle=handle,
                ats=adapter.ats,
                url=exc.url,
            )

        except Exception as exc:
            run.status = "failed"  # type: ignore[assignment]
            run.finished_at = datetime.now(tz=UTC)
            run.error_message = str(exc)
            run.error_traceback = traceback.format_exc()
            run.postings_fetched = postings_fetched
            run.postings_new = postings_new
            run.postings_updated = postings_updated
            try:
                # Preserve the docstring contract where possible: partial work
                # from before the failure commits alongside the failed run.
                await session.commit()
            except Exception:
                # fix(audit): a DB-LEVEL failure (e.g. an IntegrityError mid-
                # flush) aborts the whole transaction — this commit then raised
                # PendingRollbackError, which propagated out, LOST the failed
                # IngestRun row, and aborted the rest of the fantastic/broad
                # batch. Roll back (discarding the unsalvageable partial work),
                # then re-record just the failed run on the now-clean session.
                await session.rollback()
                session.add(run)
                await session.commit()
            logger.exception("ingestion.failed", handle=handle, ats=adapter.ats)

        return run

    # ── Per-run setup helpers ────────────────────────────────────────────────

    async def _resolve_company_and_name(
        self,
        session: AsyncSession,
        handle: str,
        target_company: TargetCompany | None,
    ) -> tuple[TargetCompany | None, str]:
        """Resolve the company link + canonical name for this run.

        When the caller didn't hand us the company (the native path), resolve it
        by ``ats_handle == handle`` as before; the Fantastic.jobs/Apify path
        passes the row in. ``canonical_name`` falls back to a title-cased handle
        when no company row exists.
        """
        if target_company is None:
            tc_row = await session.execute(
                select(TargetCompany).where(TargetCompany.ats_handle == handle)
            )
            target_company = tc_row.scalar_one_or_none()
        canonical_name: str = (
            target_company.name if target_company else handle.replace("-", " ").title()
        )
        return target_company, canonical_name

    async def _load_hard_rule_inputs(
        self,
        session: AsyncSession,
        operator_profile: OperatorProfile | None,
        target_company: TargetCompany | None,
    ) -> tuple[HardRuleConfig | None, ClosedChannel | None]:
        """Build the HardRuleConfig + load the active ClosedChannel ONCE per run
        (PR C). Mirrors the operator_profile read so the per-posting eval stays
        O(1) — no N+1. ``None`` config when the profile is unseeded → the eval is
        skipped per-posting.
        """
        hard_rule_config = (
            hard_rule_config_from_profile(operator_profile)
            if operator_profile is not None
            else None
        )
        active_closed_channel = None
        if target_company is not None:
            cc_row = await session.execute(
                select(ClosedChannel)
                .where(ClosedChannel.target_company_id == target_company.id)
                .where(ClosedChannel.unsealed_at.is_(None))
            )
            active_closed_channel = cc_row.scalar_one_or_none()
        return hard_rule_config, active_closed_channel

    # ── Per-posting helpers ──────────────────────────────────────────────────

    async def _upsert_job_posting(
        self,
        session: AsyncSession,
        norm: NormalizedPosting,
        target_company: TargetCompany | None,
    ) -> tuple[JobPosting, bool]:
        """Upsert a JobPosting by ``content_hash``. Returns ``(job_posting,
        is_new)`` so the caller can keep the new/updated counters. The new branch
        flushes to populate ``job_posting.id`` (needed by PostingSource), exactly
        as before.
        """
        jp_row = await session.execute(
            select(JobPosting).where(JobPosting.content_hash == norm.content_hash)
        )
        job_posting = jp_row.scalar_one_or_none()

        if job_posting is None:
            job_posting = self._create_job_posting(norm, target_company)
            session.add(job_posting)
            await session.flush()
            return job_posting, True

        self._update_job_posting(job_posting, norm, target_company)
        return job_posting, False

    def _create_job_posting(
        self,
        norm: NormalizedPosting,
        target_company: TargetCompany | None,
    ) -> JobPosting:
        """Build a fresh JobPosting from a NormalizedPosting (new-row branch)."""
        return JobPosting(
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

    def _update_job_posting(
        self,
        job_posting: JobPosting,
        norm: NormalizedPosting,
        target_company: TargetCompany | None,
    ) -> None:
        """Refresh an existing JobPosting on re-ingest (update-row branch).

        Bumps ``last_seen_at``, re-opens a reappeared posting, refreshes JD text
        on change, and self-heals fill-if-NULL columns (company link, department,
        team, salary). Mutations are byte-identical to the inline version.
        """
        job_posting.last_seen_at = datetime.now(tz=UTC)
        # Reappearance: a posting that was marked stale (``closed_at`` set by the
        # mark-stale sweep) but now shows up again on the ATS is a live
        # reposting — re-open it. We just refreshed last_seen_at above, so clear
        # closed_at to match. Without this, a reposted role would stay hidden
        # forever.
        if job_posting.closed_at is not None:
            job_posting.closed_at = None
        if job_posting.jd_text_hash != norm.jd_text_hash:
            job_posting.jd_text = norm.jd_text
            job_posting.jd_text_hash = norm.jd_text_hash
        if target_company is not None and job_posting.target_company_id is None:
            job_posting.target_company_id = target_company.id
        # Self-heal the new department / team columns on re-ingest: only fill
        # when the column is currently NULL so we don't overwrite a value the
        # operator may have edited by hand.
        if job_posting.department is None and norm.department is not None:
            job_posting.department = norm.department
        if job_posting.team is None and norm.team is not None:
            job_posting.team = norm.team
        # Self-heal salary on re-ingest (PR: Greenhouse salary fix). Greenhouse
        # rows ingested before JD-body salary extraction have NULL salary; once
        # the adapter starts populating it, backfill on the next re-fetch.
        # Fill-if-NULL only — never overwrite an existing range (could be
        # operator-corrected or a prior good parse). Backfill the whole tuple
        # together so currency/period stay consistent with the numbers.
        if job_posting.salary_min is None and norm.salary_min is not None:
            job_posting.salary_min = norm.salary_min
            job_posting.salary_max = norm.salary_max
            job_posting.salary_currency = norm.salary_currency
            # ``salary_period`` is a str on NormalizedPosting but a SalaryPeriod
            # enum column; SQLAlchemy coerces on write. Same enum-assignment
            # pattern as the reclassify sweep.
            job_posting.salary_period = norm.salary_period  # type: ignore[assignment]

    def _auto_score(
        self,
        job_posting: JobPosting,
        operator_profile: OperatorProfile | None,
        target_company: TargetCompany | None,
    ) -> None:
        """Compute + write fit_score on a new/updated posting (PR #56).

        No-op when the profile is unseeded. Bestiary contract (PR #56 Decision
        E): a scoring failure must NEVER cascade to fail an ingest run — score is
        optional decoration, so log + swallow on any exception.
        """
        if operator_profile is None:
            return
        try:
            from job_assist.services.scoring import SCORER_VERSION, score_posting_decomposed

            _decomp = score_posting_decomposed(
                job_posting,
                operator_profile,
                tier=(target_company.tier if target_company else None),
            )
            job_posting.fit_score = _decomp.final
            job_posting.score_components = _decomp.to_dict()
            job_posting.scored_at = datetime.now(tz=UTC)
            job_posting.scorer_version = SCORER_VERSION
        except Exception as exc:
            logger.warning(
                "ingestion.scoring_failed",
                posting_id=str(job_posting.id) if job_posting.id else None,
                error=str(exc)[:300],
            )

    def _eval_hard_rules(
        self,
        job_posting: JobPosting,
        target_company: TargetCompany | None,
        active_closed_channel: ClosedChannel | None,
        hard_rule_config: HardRuleConfig | None,
    ) -> None:
        """Evaluate hard-rule eligibility + persist the failed RuleName (PR C).

        No-op when the config is unseeded. Persist NULL = passed so /postings can
        filter cheaply. Same Bestiary contract as scoring: a filter failure must
        NEVER cascade to fail the ingest run — log + swallow.
        """
        if hard_rule_config is None:
            return
        try:
            from job_assist.triage.hard_rules import apply_hard_rules

            verdict = apply_hard_rules(
                job_posting,
                target_company,
                active_closed_channel,
                hard_rule_config,
            )
            job_posting.hard_rule_failed = None if verdict.passed else verdict.failed_rule
            job_posting.hard_rules_evaluated_at = datetime.now(tz=UTC)
        except Exception as exc:
            logger.warning(
                "ingestion.hard_rules_failed",
                posting_id=str(job_posting.id) if job_posting.id else None,
                error=str(exc)[:300],
            )

    async def _upsert_posting_source(
        self,
        session: AsyncSession,
        norm: NormalizedPosting,
        job_posting: JobPosting,
    ) -> None:
        """Upsert the PostingSource by ``(ats, source_job_id)`` — insert with full
        provenance on first sight, else refresh ``raw_payload`` + ``fetched_at``.
        """
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
            # fix/ingest-lifecycle (audit HIGH #7): JobPosting is deduped by
            # content_hash (company+title+locations) but PostingSource by
            # (ats, source_job_id). When an ATS edits a live posting's title or
            # locations (e.g. adds a remote option), content_hash changes →
            # _upsert_job_posting inserts a NEW JobPosting, but THIS source row
            # still points at the OLD one. Re-point it: otherwise the new
            # posting has no PostingSource forever (no apply URL, invisible to
            # the ats filter) and this row's provenance points at a stale
            # posting. Also refresh the provenance URLs — a moved posting can
            # change them.
            if posting_source.job_posting_id != job_posting.id:
                posting_source.job_posting_id = job_posting.id
                # Refresh the apply/source links so the re-pointed (new) posting
                # has a correct apply URL; parser_version tracks the producing
                # parser. (fetch_status left as-is — not load-bearing here.)
                posting_source.source_url = norm.source_url
                posting_source.apply_url = norm.apply_url
                posting_source.parser_version = norm.parser_version
            posting_source.raw_payload = norm.raw_payload
            posting_source.fetched_at = now


# ── Stale-posting detection ────────────────────────────────────────────────────

# A posting not seen on its ATS board for this many days is treated as
# removed/filled and marked closed. Conservative: the daily ingest re-fetches
# every active board, so a still-live posting gets last_seen_at bumped daily.
# A 7-day floor tolerates a board being unreachable for a few consecutive
# days (transient ATS outage) without wrongly closing its postings — they
# only close after a full week of no sighting.
STALE_AFTER_DAYS = 7

# fix/ingest-lifecycle (audit HIGH #2): the warm-path cohort is swept WEEKLY,
# not daily, so a 7-day floor leaves it ~0 margin — one missed/late/failed
# Sunday sweep would wrongly close every warm-path posting. Give it a window
# that spans a full week plus grace (and aligns with the warm_path_fresh
# health window) so a single skipped sweep can't close the cohort.
WARM_PATH_STALE_AFTER_DAYS = 10


async def mark_stale_postings(
    session: AsyncSession,
    stale_after_days: int = STALE_AFTER_DAYS,
) -> int:
    """Mark postings stale: set ``closed_at=now()`` where the posting is
    still open but hasn't been seen on its ATS in ``stale_after_days``.

    Bestiary 5.18: ``closed_at`` existed since day one but nothing wrote it
    and no query read it, so removed-from-board postings showed as active
    forever. This is the writer; ``list_postings`` (default
    ``closed_at IS NULL``) is the reader.

    Idempotent: rows already closed (``closed_at IS NOT NULL``) are skipped,
    so re-running never re-stamps an existing closure timestamp. The
    reappearance path (ingest update branch clears ``closed_at`` when a
    posting is seen again) is the inverse of this.

    Returns the number of rows newly marked closed.
    """
    from sqlalchemy import or_

    now = datetime.now(tz=UTC)
    cutoff = now - timedelta(days=stale_after_days)
    warm_cutoff = now - timedelta(days=WARM_PATH_STALE_AFTER_DAYS)
    # Warm-path postings = those whose matched company has source='warm_path'.
    # Postings with no matched company (target_company_id NULL — the bulk) are
    # never warm-path and stay on the daily cutoff. When the cohort is empty
    # the warm UPDATE is a harmless no-op and the daily UPDATE behaves exactly
    # as before this change.
    warm_company_ids = (
        select(TargetCompany.id).where(TargetCompany.source == "warm_path").scalar_subquery()
    )

    daily_result = await session.execute(
        update(JobPosting)
        .where(JobPosting.closed_at.is_(None))
        .where(JobPosting.last_seen_at < cutoff)
        .where(
            or_(
                JobPosting.target_company_id.is_(None),
                JobPosting.target_company_id.not_in(warm_company_ids),
            )
        )
        .values(closed_at=now)
    )
    warm_result = await session.execute(
        update(JobPosting)
        .where(JobPosting.closed_at.is_(None))
        .where(JobPosting.last_seen_at < warm_cutoff)
        .where(JobPosting.target_company_id.in_(warm_company_ids))
        .values(closed_at=now)
    )
    await session.commit()
    # An UPDATE yields a CursorResult; .rowcount isn't on the base Result type.
    marked = (cast("CursorResult[Any]", daily_result).rowcount or 0) + (
        cast("CursorResult[Any]", warm_result).rowcount or 0
    )
    logger.info(
        "mark_stale_postings.done",
        marked=marked,
        stale_after_days=stale_after_days,
        warm_path_stale_after_days=WARM_PATH_STALE_AFTER_DAYS,
    )
    return marked
