"""Application-resume endpoint tests (feat/application-resume Phase 1).

Exercises the upsert + download THROUGH the endpoints against a real DB:
  POST /postings/{id}/resume   — file upload (raw body) + paste (JSON), upsert
  GET  /postings/{id}/resume   — stream the blob with Content-Disposition
  GET  /postings/{id}          — detail now carries the `resume` meta block
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models import JobPosting, PostingAction

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)

_DOCX_CT = "application/octet-stream"  # the browser/raw upload content-type
_FAKE_DOCX = b"PK\x03\x04 fake docx bytes for the test " + b"x" * 64


async def _client(db_session: Any) -> AsyncClient:
    from job_assist.db.session import get_db
    from job_assist.main import app

    async def _override() -> Any:
        yield db_session

    app.dependency_overrides[get_db] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _drop_override() -> None:
    from job_assist.db.session import get_db
    from job_assist.main import app

    app.dependency_overrides.pop(get_db, None)


async def _make_posting(db_session: Any, *, applied: bool = False) -> uuid.UUID:
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:10]
    jp = JobPosting(
        canonical_company_name="ResumeCo",
        normalized_title="senior product manager",
        raw_title="Senior Product Manager",
        remote_type="remote",
        role_family="product_management",
        seniority_level="senior_pm",
        jd_text="JD.",
        jd_text_hash="0" * 64,
        content_hash=f"hash-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
    )
    db_session.add(jp)
    await db_session.flush()
    if applied:
        db_session.add(PostingAction(job_posting_id=jp.id, action_type="applied"))
    await db_session.commit()
    return jp.id


@_NEEDS_DB
async def test_upload_docx_then_download_round_trips(db_session: Any) -> None:
    pid = await _make_posting(db_session, applied=True)
    ac = await _client(db_session)
    try:
        async with ac:
            up = await ac.post(
                f"/postings/{pid}/resume?filename=betterment-resume.docx&angle=trust+compliance",
                content=_FAKE_DOCX,
                headers={"content-type": _DOCX_CT},
            )
            assert up.status_code == 200, up.text
            meta = up.json()
            assert meta["has_file"] is True
            assert meta["file_name"] == "betterment-resume.docx"
            assert meta["angle"] == "trust compliance"
            assert meta["content_type"].endswith("wordprocessingml.document")

            dl = await ac.get(f"/postings/{pid}/resume")
            assert dl.status_code == 200
            assert dl.content == _FAKE_DOCX
            assert "attachment" in dl.headers["content-disposition"]
            assert "betterment-resume.docx" in dl.headers["content-disposition"]
    finally:
        await _drop_override()


@_NEEDS_DB
async def test_upsert_replaces_not_duplicates(db_session: Any) -> None:
    pid = await _make_posting(db_session, applied=True)
    ac = await _client(db_session)
    try:
        async with ac:
            await ac.post(
                f"/postings/{pid}/resume?filename=v1.docx",
                content=b"FIRST" + b"y" * 40,
                headers={"content-type": _DOCX_CT},
            )
            await ac.post(
                f"/postings/{pid}/resume?filename=v2.pdf",
                content=b"SECOND" + b"z" * 40,
                headers={"content-type": _DOCX_CT},
            )
            dl = await ac.get(f"/postings/{pid}/resume")
            assert dl.content.startswith(b"SECOND")
            assert "v2.pdf" in dl.headers["content-disposition"]
            assert dl.headers["content-type"].startswith("application/pdf")
    finally:
        await _drop_override()

    # Exactly one row for the posting (UPSERT, not stacked).
    from sqlalchemy import func, select

    from job_assist.db.models import ApplicationResume

    count = (
        await db_session.execute(
            select(func.count())
            .select_from(ApplicationResume)
            .where(ApplicationResume.job_posting_id == pid)
        )
    ).scalar_one()
    assert count == 1


@_NEEDS_DB
async def test_paste_only_resume_text(db_session: Any) -> None:
    pid = await _make_posting(db_session, applied=True)
    ac = await _client(db_session)
    try:
        async with ac:
            up = await ac.post(
                f"/postings/{pid}/resume",
                json={"resume_text": "pasted resume body", "label": "v1"},
            )
            assert up.status_code == 200, up.text
            meta = up.json()
            assert meta["has_file"] is False
            assert meta["resume_text"] == "pasted resume body"
            assert meta["label"] == "v1"

            # No file blob → download is 404.
            dl = await ac.get(f"/postings/{pid}/resume")
            assert dl.status_code == 404
    finally:
        await _drop_override()


@_NEEDS_DB
async def test_retroactive_attach_to_already_applied(db_session: Any) -> None:
    # Mirrors Betterment/Justworks: applied first, attach resume after.
    pid = await _make_posting(db_session, applied=True)
    ac = await _client(db_session)
    try:
        async with ac:
            up = await ac.post(
                f"/postings/{pid}/resume?filename=after.docx",
                content=_FAKE_DOCX,
                headers={"content-type": _DOCX_CT},
            )
            assert up.status_code == 200
            # Detail surfaces the attached resume meta.
            detail = await ac.get(f"/postings/{pid}")
            assert detail.status_code == 200
            resume = detail.json()["resume"]
            assert resume is not None
            assert resume["has_file"] is True
            assert resume["file_name"] == "after.docx"
    finally:
        await _drop_override()


@_NEEDS_DB
async def test_detail_resume_is_null_when_none(db_session: Any) -> None:
    pid = await _make_posting(db_session)
    ac = await _client(db_session)
    try:
        async with ac:
            detail = await ac.get(f"/postings/{pid}")
            assert detail.status_code == 200
            assert detail.json()["resume"] is None
    finally:
        await _drop_override()


@_NEEDS_DB
async def test_rejects_unknown_posting_and_bad_type(db_session: Any) -> None:
    ac = await _client(db_session)
    try:
        async with ac:
            # Unknown posting → 404.
            missing = await ac.post(
                f"/postings/{uuid.uuid4()}/resume?filename=x.docx",
                content=_FAKE_DOCX,
                headers={"content-type": _DOCX_CT},
            )
            assert missing.status_code == 404

            pid = await _make_posting(db_session, applied=True)
            # Disallowed extension → 422.
            bad = await ac.post(
                f"/postings/{pid}/resume?filename=resume.txt",
                content=_FAKE_DOCX,
                headers={"content-type": _DOCX_CT},
            )
            assert bad.status_code == 422
            # File upload without a filename → 422.
            nofn = await ac.post(
                f"/postings/{pid}/resume",
                content=_FAKE_DOCX,
                headers={"content-type": _DOCX_CT},
            )
            assert nofn.status_code == 422
    finally:
        await _drop_override()
