"""Tests that pin the public-repo data-discipline contract.

Two concerns covered here:

  1. Gitignore behaviour — ``apps/api/seeds/*.json`` is gitignored except
     for ``*.example.json`` templates. If someone reverts the ignore rule
     or renames the example file, these tests fail loudly.

  2. Seed script idempotency — running the seed against the same payload
     twice must not create duplicate rows.

Test (1) is fast and runs everywhere. Test (2) is DB-gated and runs in CI
against the postgres service.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


# ── Gitignore guards ──────────────────────────────────────────────────────────


def _git_check_ignore(rel_path: str) -> int:
    """Return ``git check-ignore`` exit code for *rel_path* under the repo root.

    Exit 0  → path IS ignored.
    Exit 1  → path is NOT ignored.
    Exit 128 → git error (we surface this so a misconfigured CI is obvious).
    """
    result = subprocess.run(
        ["git", "check-ignore", "--", rel_path],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode


class TestGitignore:
    def test_private_seed_json_is_ignored(self) -> None:
        """The real seed file must never enter git."""
        assert _git_check_ignore("apps/api/seeds/target_companies.json") == 0, (
            "seeds/target_companies.json must be gitignored"
        )

    def test_example_seed_is_tracked(self) -> None:
        """The public schema template must remain commit-able."""
        assert _git_check_ignore("apps/api/seeds/target_companies.example.json") == 1, (
            "seeds/target_companies.example.json must NOT be gitignored"
        )

    def test_arbitrary_private_seed_is_ignored(self) -> None:
        """Any new private *.json under seeds/ inherits the ignore rule."""
        assert _git_check_ignore("apps/api/seeds/personal_overrides.json") == 0, (
            "Arbitrary private seed JSON must be gitignored"
        )

    def test_arbitrary_example_is_tracked(self) -> None:
        """Any new *.example.json template stays committable."""
        assert _git_check_ignore("apps/api/seeds/personal_overrides.example.json") == 1, (
            "Any *.example.json file must NOT be gitignored"
        )


# ── Seed-script idempotency ───────────────────────────────────────────────────


_SEED_PAYLOAD: list[dict[str, Any]] = [
    {
        "name": "TestCo Alpha",
        "tier": 1,
        "ats": "greenhouse",
        "ats_handle": "testco-alpha",
        "role_filter": None,
        "domain": None,
        "notes": None,
    },
    {
        "name": "TestCo Beta",
        "tier": 2,
        "ats": "unknown",
        "ats_handle": None,
        "role_filter": "non_pm_only",
        "domain": None,
        "notes": None,
    },
]


@_NEEDS_DB
async def test_seed_first_run_inserts_all(db_session: Any) -> None:
    from sqlalchemy import func, select

    from job_assist.db.models import TargetCompany
    from job_assist.seed import seed_from_rows

    inserted, skipped = await seed_from_rows(db_session, _SEED_PAYLOAD)
    assert inserted == len(_SEED_PAYLOAD)
    assert skipped == 0

    total = (await db_session.execute(select(func.count()).select_from(TargetCompany))).scalar_one()
    assert total == len(_SEED_PAYLOAD)


@_NEEDS_DB
async def test_seed_is_idempotent(db_session: Any) -> None:
    """Running the same seed twice must not create duplicate rows."""
    from sqlalchemy import func, select

    from job_assist.db.models import TargetCompany
    from job_assist.seed import seed_from_rows

    inserted_1, skipped_1 = await seed_from_rows(db_session, _SEED_PAYLOAD)
    inserted_2, skipped_2 = await seed_from_rows(db_session, _SEED_PAYLOAD)

    assert inserted_1 == len(_SEED_PAYLOAD)
    assert skipped_1 == 0
    # Second run must be all skips, zero inserts.
    assert inserted_2 == 0
    assert skipped_2 == len(_SEED_PAYLOAD)

    total = (await db_session.execute(select(func.count()).select_from(TargetCompany))).scalar_one()
    assert total == len(_SEED_PAYLOAD)


@_NEEDS_DB
async def test_seed_rejects_row_missing_required_field(db_session: Any) -> None:
    """A row without name or tier must raise ValueError, not silently drop."""
    from job_assist.seed import seed_from_rows

    bad_payload: list[dict[str, Any]] = [{"ats": "greenhouse"}]  # no name, no tier
    with pytest.raises(ValueError, match="missing required name/tier"):
        await seed_from_rows(db_session, bad_payload)


@_NEEDS_DB
async def test_seed_drops_unknown_fields(db_session: Any) -> None:
    """Unknown JSON keys are silently dropped — caller can't tamper with id/timestamps."""
    from sqlalchemy import select

    from job_assist.db.models import TargetCompany
    from job_assist.seed import seed_from_rows

    payload: list[dict[str, Any]] = [
        {
            "name": "ScrubbedCo",
            "tier": 1,
            "id": "some-fake-uuid-the-caller-tried-to-set",
            "created_at": "1970-01-01T00:00:00Z",
        }
    ]
    inserted, skipped = await seed_from_rows(db_session, payload)
    assert inserted == 1
    assert skipped == 0

    row = (
        await db_session.execute(select(TargetCompany).where(TargetCompany.name == "ScrubbedCo"))
    ).scalar_one()
    # The id is a fresh UUID, not the "some-fake-uuid..." string.
    assert str(row.id) != "some-fake-uuid-the-caller-tried-to-set"
