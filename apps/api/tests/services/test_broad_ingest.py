"""DB-gated tests for the broad-ingest runner (Slice 2).

Pins four contracts:

  1. **Shell-row creation** — a discovered handle with no matching
     ``target_company`` gets a thin shell (tier=NULL) created exactly
     once; a curated company with the same ats_handle is NEVER
     overwritten.
  2. **Title-filter applied on this path** — the runner calls
     ``ingest_source(..., apply_title_prefilter=True)``, so non-PM
     titles from a discovered handle are dropped while PM titles land.
  3. **Curated-30 path untouched** — the runner only sweeps
     ``discovered_handle`` rows; it never touches the curated
     ``target_company`` ingest path, which still defaults the flag to
     False.
  4. **Lifecycle write-back** — ``consecutive_empty_count`` increments
     on an empty handle and the row deactivates after the threshold.

The adapter is stubbed (no network) so the tests run offline. The stub
returns a controlled mix of PM and non-PM Greenhouse-shaped payloads.
"""

from __future__ import annotations

import os
import uuid
from types import TracebackType
from typing import Any

import pytest
from sqlalchemy import func, select

from job_assist.adapters.base import NormalizedPosting, RawPosting
from job_assist.db.models import DiscoveredHandle, JobPosting, TargetCompany
from job_assist.services import broad_ingest as bi
from job_assist.services.broad_ingest import run_broad_ingest, seed_discovered_handles

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Stub adapter ─────────────────────────────────────────────────────────────


class _StubAdapter:
    """Greenhouse-shaped stub. ``jobs`` is a list of title strings; each
    becomes a RawPosting whose ``peek_title`` reads ``title``. Honors the
    async-context-manager protocol like the real adapters."""

    ats = "greenhouse"
    parser_version = "stub-v1"

    def __init__(self, titles: list[str]) -> None:
        self._titles = titles

    async def __aenter__(self) -> _StubAdapter:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None

    async def fetch_postings(self, handle: str) -> list[RawPosting]:
        out: list[RawPosting] = []
        for i, title in enumerate(self._titles):
            out.append(
                RawPosting(
                    source_job_id=f"{handle}-{i}",
                    raw_payload={
                        "id": 1000 + i,
                        "title": title,
                        "content": f"<p>JD for {title} at {handle}</p>",
                        "location": {"name": "Remote"},
                        "absolute_url": f"https://boards.greenhouse.io/{handle}/{i}",
                    },
                )
            )
        return out

    def peek_title(self, raw: RawPosting) -> str:
        return str(raw.raw_payload.get("title") or "")

    def normalize(self, raw: RawPosting, canonical_company_name: str) -> NormalizedPosting:
        # Delegate to the real Greenhouse normalizer so content_hash etc.
        # are computed exactly as production would. The stub only controls
        # fetch + peek; normalization is the real thing.
        from job_assist.adapters.greenhouse import GreenhouseAdapter

        return GreenhouseAdapter().normalize(raw, canonical_company_name)


def _patch_adapter(monkeypatch: pytest.MonkeyPatch, titles: list[str]) -> None:
    """Make ``_build_adapter`` return our stub regardless of ATS."""
    monkeypatch.setattr(bi, "_build_adapter", lambda ats: _StubAdapter(titles))


# ── (1) Shell-row creation ──────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_shell_company_created_once(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    handle = f"newco{uuid.uuid4().hex[:6]}"
    await seed_discovered_handles(db_session, [("greenhouse", handle)])
    _patch_adapter(monkeypatch, ["Senior Product Manager"])

    await run_broad_ingest(db_session, limit=10)

    shells = (
        (await db_session.execute(select(TargetCompany).where(TargetCompany.ats_handle == handle)))
        .scalars()
        .all()
    )
    assert len(shells) == 1
    assert shells[0].tier is None, "broad-ingest shell must have tier=NULL"
    assert shells[0].ats_handle == handle


@_NEEDS_DB
@pytest.mark.asyncio
async def test_shell_does_not_overwrite_curated_company(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A curated company sharing the ats_handle must be left untouched —
    name + tier preserved, no duplicate row."""
    handle = f"curated{uuid.uuid4().hex[:6]}"
    curated = TargetCompany(
        name="Curated Co Original Name", ats="greenhouse", ats_handle=handle, tier=1
    )
    db_session.add(curated)
    await db_session.flush()
    await seed_discovered_handles(db_session, [("greenhouse", handle)])
    _patch_adapter(monkeypatch, ["Senior Product Manager"])

    report = await run_broad_ingest(db_session, limit=10)

    rows = (
        (await db_session.execute(select(TargetCompany).where(TargetCompany.ats_handle == handle)))
        .scalars()
        .all()
    )
    assert len(rows) == 1, "must not create a duplicate shell for a curated company"
    assert rows[0].name == "Curated Co Original Name"
    assert rows[0].tier == 1
    assert report.shells_created == 0


# ── (2) Title-filter applied on this path ───────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_title_filter_applied_drops_non_pm(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = f"mixco{uuid.uuid4().hex[:6]}"
    await seed_discovered_handles(db_session, [("greenhouse", handle)])
    _patch_adapter(
        monkeypatch,
        [
            "Senior Product Manager",  # keep
            "Product Owner",  # keep
            "Staff Software Engineer",  # drop
            "Account Executive",  # drop
            "Product Designer",  # drop (exclusion)
        ],
    )

    report = await run_broad_ingest(db_session, limit=10)

    # 5 fetched, 2 PM-cluster kept.
    assert report.total_postings_fetched == 5
    assert report.total_postings_kept == 2

    titles_in_db = sorted(
        (await db_session.execute(select(JobPosting.normalized_title))).scalars().all()
    )
    assert titles_in_db == ["product owner", "senior product manager"]


# ── (3) Curated-30 path untouched ───────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_runner_only_sweeps_discovered_handles(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A curated company with NO discovered_handle row is never ingested
    by the broad runner — the runner's scope is discovered_handle only."""
    curated_handle = f"curatedonly{uuid.uuid4().hex[:6]}"
    db_session.add(
        TargetCompany(name="CuratedOnly", ats="greenhouse", ats_handle=curated_handle, tier=1)
    )
    # A separate discovered handle that WILL be swept.
    disc_handle = f"disc{uuid.uuid4().hex[:6]}"
    await seed_discovered_handles(db_session, [("greenhouse", disc_handle)])
    _patch_adapter(monkeypatch, ["Senior Product Manager"])

    report = await run_broad_ingest(db_session, limit=10)

    # Only the discovered handle was considered.
    assert report.handles_considered == 1
    swept = {r.handle for r in report.per_handle}
    assert swept == {disc_handle}
    assert curated_handle not in swept


# ── (4) Lifecycle write-back ────────────────────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_empty_handle_increments_and_deactivates(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty pull bumps consecutive_empty_count; after the threshold
    the handle deactivates so the runner stops pulling it."""
    handle = f"emptyco{uuid.uuid4().hex[:6]}"
    await seed_discovered_handles(db_session, [("greenhouse", handle)])
    # Adapter returns only non-PM titles → title filter drops all → kept=0.
    _patch_adapter(monkeypatch, ["Staff Software Engineer", "Account Executive"])

    # Run the threshold number of times; each is an "empty" (kept=0) pull.
    for _ in range(bi._DEACTIVATE_AFTER_EMPTY):
        await run_broad_ingest(db_session, limit=10)

    dh = (
        await db_session.execute(select(DiscoveredHandle).where(DiscoveredHandle.handle == handle))
    ).scalar_one()
    assert dh.consecutive_empty_count == bi._DEACTIVATE_AFTER_EMPTY
    assert dh.active is False, "handle should deactivate after the empty threshold"

    # A subsequent run must NOT consider the now-inactive handle.
    report = await run_broad_ingest(db_session, limit=10)
    assert handle not in {r.handle for r in report.per_handle}


@_NEEDS_DB
@pytest.mark.asyncio
async def test_nonempty_pull_resets_empty_counter(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pull that keeps ≥1 row resets consecutive_empty_count to 0."""
    handle = f"resetco{uuid.uuid4().hex[:6]}"
    await seed_discovered_handles(db_session, [("greenhouse", handle)])

    # First run: empty (non-PM only) → counter = 1.
    _patch_adapter(monkeypatch, ["Account Executive"])
    await run_broad_ingest(db_session, limit=10)
    dh = (
        await db_session.execute(select(DiscoveredHandle).where(DiscoveredHandle.handle == handle))
    ).scalar_one()
    assert dh.consecutive_empty_count == 1

    # Second run: a PM role lands → counter resets to 0.
    _patch_adapter(monkeypatch, ["Senior Product Manager"])
    await run_broad_ingest(db_session, limit=10)
    await db_session.refresh(dh)
    assert dh.consecutive_empty_count == 0
    assert dh.active is True


# ── Seed idempotency (pure-ish, DB-gated) ───────────────────────────────────


@_NEEDS_DB
@pytest.mark.asyncio
async def test_seed_is_idempotent(db_session: Any) -> None:
    handle = f"seedco{uuid.uuid4().hex[:6]}"
    ins1, skip1 = await seed_discovered_handles(db_session, [("greenhouse", handle)])
    ins2, skip2 = await seed_discovered_handles(db_session, [("greenhouse", handle)])
    assert (ins1, skip1) == (1, 0)
    assert (ins2, skip2) == (0, 1)
    count = (
        await db_session.execute(
            select(func.count())
            .select_from(DiscoveredHandle)
            .where(DiscoveredHandle.handle == handle)
        )
    ).scalar_one()
    assert count == 1
