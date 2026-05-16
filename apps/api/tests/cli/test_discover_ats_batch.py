"""Integration tests for the discover-ats batch flow.

These tests pin the two-phase contract of ``discover_target_companies``
(the function the CLI ``--all`` flag and the FastAPI admin endpoint share):

  * Dry-run (``commit=False``) probes every row with ``ats='unknown'`` and
    returns the summary, but performs no DB writes.
  * Commit (``commit=True``) does the same plus persists detected
    ``(ats, ats_handle)`` back onto the matched rows.
  * Rows where ``ats`` is already set (e.g. seeded Workday companies) are
    filtered out by the WHERE clause and never probed.

HTTP probes are mocked via respx so the tests don't touch the network.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from job_assist.cli import discover_target_companies
from job_assist.db.models.target_company import TargetCompany

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _seed_companies(rows: list[dict[str, Any]]) -> list[TargetCompany]:
    return [
        TargetCompany(
            name=r["name"],
            ats=r.get("ats", "unknown"),
            ats_handle=r.get("ats_handle"),
            tier=r.get("tier", 3),
        )
        for r in rows
    ]


def _mock_probes(
    *,
    greenhouse_200: set[str] | None = None,
    lever_200: set[str] | None = None,
    ashby_200: set[str] | None = None,
    greenhouse_jobs: dict[str, list[dict[str, Any]]] | None = None,
    lever_jobs: dict[str, list[dict[str, Any]]] | None = None,
    ashby_jobs: dict[str, list[dict[str, Any]]] | None = None,
) -> respx.MockRouter:
    """Build a respx router that returns 200 for the named handle sets and 404 otherwise.

    Each handle in ``*_200`` returns a minimally well-formed response shape
    (jobs array for Greenhouse/Ashby, top-level array for Lever). Specific
    job counts can be set via ``*_jobs``; missing keys default to ``[]``.
    """
    greenhouse_200 = greenhouse_200 or set()
    lever_200 = lever_200 or set()
    ashby_200 = ashby_200 or set()
    greenhouse_jobs = greenhouse_jobs or {}
    lever_jobs = lever_jobs or {}
    ashby_jobs = ashby_jobs or {}

    router = respx.mock(assert_all_called=False)

    def gh_handler(request: httpx.Request) -> httpx.Response:
        handle = request.url.path.rsplit("/", 2)[-2]
        if handle in greenhouse_200:
            return httpx.Response(200, json={"jobs": greenhouse_jobs.get(handle, [])})
        return httpx.Response(404)

    def lever_handler(request: httpx.Request) -> httpx.Response:
        handle = request.url.path.rsplit("/", 1)[-1]
        if handle in lever_200:
            return httpx.Response(200, json=lever_jobs.get(handle, []))
        return httpx.Response(404)

    def ashby_handler(request: httpx.Request) -> httpx.Response:
        handle = request.url.path.rsplit("/", 1)[-1]
        if handle in ashby_200:
            return httpx.Response(200, json={"jobs": ashby_jobs.get(handle, [])})
        return httpx.Response(404)

    router.get(url__regex=r"https://boards-api\.greenhouse\.io/v1/boards/[^/]+/jobs").mock(
        side_effect=gh_handler
    )
    router.get(url__regex=r"https://api\.lever\.co/v0/postings/[^?]+").mock(
        side_effect=lever_handler
    )
    router.get(url__regex=r"https://api\.ashbyhq\.com/posting-api/job-board/[^?]+").mock(
        side_effect=ashby_handler
    )
    return router


# ── Tests ──────────────────────────────────────────────────────────────────────


@_NEEDS_DB
async def test_dry_run_returns_summary_without_writes(db_session: Any) -> None:
    """`commit=False` must yield matched/unmatched lists but leave the DB untouched."""
    companies = _seed_companies(
        [
            {"name": "Stripe", "tier": 1},
            {"name": "Notion", "tier": 2},
            {"name": "Vanta", "tier": 3},
        ]
    )
    db_session.add_all(companies)
    await db_session.commit()

    with _mock_probes(
        greenhouse_200={"stripe"},
        ashby_200={"notion"},
        greenhouse_jobs={"stripe": [{"id": 1}, {"id": 2}, {"id": 3}]},
        ashby_jobs={"notion": [{}] * 140},
    ):
        matched, unmatched = await discover_target_companies(db_session, commit=False)

    matched_names = {m["name"] for m in matched}
    assert matched_names == {"Stripe", "Notion"}
    assert unmatched == ["Vanta"]

    # Find the Stripe row in the summary and verify its detected fields.
    stripe = next(m for m in matched if m["name"] == "Stripe")
    assert stripe["ats"] == "greenhouse"
    assert stripe["handle"] == "stripe"
    assert stripe["job_count"] == 3

    notion = next(m for m in matched if m["name"] == "Notion")
    assert notion["ats"] == "ashby"
    assert notion["handle"] == "notion"
    assert notion["job_count"] == 140

    # DB rows must still show ats='unknown'.
    rows = (
        (
            await db_session.execute(
                select(TargetCompany).where(TargetCompany.name.in_(["Stripe", "Notion", "Vanta"]))
            )
        )
        .scalars()
        .all()
    )
    for r in rows:
        assert r.ats == "unknown", f"Dry-run unexpectedly wrote {r.name}: ats={r.ats!r}"
        assert r.ats_handle is None


@_NEEDS_DB
async def test_commit_writes_matched_rows(db_session: Any) -> None:
    """`commit=True` persists detected (ats, ats_handle) onto matched rows only."""
    companies = _seed_companies(
        [
            {"name": "Stripe", "tier": 1},
            {"name": "Spotify", "tier": 3},
            {"name": "Anthropic", "tier": 3},
        ]
    )
    db_session.add_all(companies)
    await db_session.commit()

    with _mock_probes(
        greenhouse_200={"stripe"},
        lever_200={"spotify"},
        greenhouse_jobs={"stripe": [{"id": 1}]},
        lever_jobs={"spotify": [{"id": "a"}, {"id": "b"}]},
    ):
        matched, unmatched = await discover_target_companies(db_session, commit=True)

    assert {m["name"] for m in matched} == {"Stripe", "Spotify"}
    assert unmatched == ["Anthropic"]

    by_name = {
        r.name: r
        for r in (
            await db_session.execute(
                select(TargetCompany).where(
                    TargetCompany.name.in_(["Stripe", "Spotify", "Anthropic"])
                )
            )
        )
        .scalars()
        .all()
    }
    assert by_name["Stripe"].ats == "greenhouse"
    assert by_name["Stripe"].ats_handle == "stripe"
    assert by_name["Spotify"].ats == "lever"
    assert by_name["Spotify"].ats_handle == "spotify"
    # Unmatched row stays untouched.
    assert by_name["Anthropic"].ats == "unknown"
    assert by_name["Anthropic"].ats_handle is None


@_NEEDS_DB
async def test_workday_rows_are_skipped(db_session: Any) -> None:
    """Rows where ats != 'unknown' (seeded Workday companies) must not be probed."""
    companies = _seed_companies(
        [
            {"name": "Capital One", "ats": "workday", "ats_handle": "capitalone", "tier": 2},
            {
                "name": "John Hancock / Manulife US",
                "ats": "workday",
                "ats_handle": "manulife",
                "tier": 3,
            },
            {"name": "Stripe", "tier": 1},
        ]
    )
    db_session.add_all(companies)
    await db_session.commit()

    # If the Workday rows were probed, respx would log a call against the
    # capitalone / manulife handles — assert_all_called is off, so we instead
    # inspect router.calls after the run.
    router = _mock_probes(greenhouse_200={"stripe"}, greenhouse_jobs={"stripe": [{}]})
    with router:
        matched, unmatched = await discover_target_companies(db_session, commit=True)

    assert {m["name"] for m in matched} == {"Stripe"}
    assert unmatched == []  # only Stripe was in the unknown set

    probed_handles = {call.request.url.path.rstrip("/").rsplit("/", 1)[-1] for call in router.calls}
    assert "capitalone" not in probed_handles, "Workday row should not have been probed"
    assert "manulife" not in probed_handles, "Workday row should not have been probed"

    # Existing Workday rows must remain unchanged.
    cap = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == "Capital One"))
    ).scalar_one()
    assert cap.ats == "workday"
    assert cap.ats_handle == "capitalone"


@_NEEDS_DB
async def test_empty_unknown_set_is_noop(db_session: Any) -> None:
    """When no rows have ats='unknown', the function returns ([], []) without HTTP."""
    db_session.add_all(_seed_companies([{"name": "Stripe", "ats": "greenhouse", "tier": 1}]))
    await db_session.commit()

    with _mock_probes() as router:
        matched, unmatched = await discover_target_companies(db_session, commit=True)

    assert matched == []
    assert unmatched == []
    assert router.calls.call_count == 0


@_NEEDS_DB
async def test_progress_callback_is_invoked(db_session: Any) -> None:
    """on_progress receives (done, total) for every completed probe."""
    db_session.add_all(
        _seed_companies(
            [
                {"name": "Stripe", "tier": 1},
                {"name": "Notion", "tier": 2},
                {"name": "Vanta", "tier": 3},
            ]
        )
    )
    await db_session.commit()

    events: list[tuple[int, int]] = []

    def record(done: int, total: int) -> None:
        events.append((done, total))

    with _mock_probes(greenhouse_200={"stripe"}, ashby_200={"notion"}):
        await discover_target_companies(db_session, commit=False, on_progress=record)

    assert len(events) == 3
    assert {total for _, total in events} == {3}
    assert [done for done, _ in events] == [1, 2, 3]
