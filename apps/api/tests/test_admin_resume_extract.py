"""DB-gated tests for the resume-text backfill endpoints (feat/resume-text-extract).

Covers POST /admin/resumes/extract-text (dry-run preview vs write, already-has-text
skip, non-docx skip) and GET /admin/resumes/{id}/text (readback + 404). Verifies
ONLY resume_text is populated and the idempotency/skip guarantees hold.
"""

from __future__ import annotations

import io
import os
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from job_assist.db.models import ApplicationResume, JobPosting, TargetCompany

_NEEDS_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_bytes(*, paragraph: str, cell: str) -> bytes:
    document_xml = (
        f'<?xml version="1.0"?><w:document xmlns:w="{_W}"><w:body>'
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>"
        f"<w:tbl><w:tr><w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        f"</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


async def _posting(db: Any) -> uuid.UUID:
    tc = TargetCompany(
        name=f"Co-{uuid.uuid4().hex[:8]}", tier=1, ats="greenhouse", ats_handle=uuid.uuid4().hex[:6]
    )
    db.add(tc)
    await db.flush()
    now = datetime.now(tz=UTC)
    suffix = uuid.uuid4().hex[:8]
    jp = JobPosting(
        canonical_company_name="TestCo",
        target_company_id=tc.id,
        normalized_title="product manager",
        raw_title="Product Manager",
        jd_text="x",
        jd_text_hash=f"jd-{suffix}",
        content_hash=f"ch-{suffix}",
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(jp)
    await db.flush()
    return jp.id


def _resume(
    *,
    job_posting_id: uuid.UUID,
    blob: bytes | None,
    file_name: str | None,
    content_type: str | None,
    resume_text: str | None = None,
) -> ApplicationResume:
    return ApplicationResume(
        job_posting_id=job_posting_id,
        file_blob=blob,
        file_name=file_name,
        content_type=content_type,
        resume_text=resume_text,
    )


_DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@_NEEDS_DB
@pytest.mark.asyncio
async def test_dry_run_previews_without_writing(db_session: Any) -> None:
    from job_assist.main import app

    pid = await _posting(db_session)
    ar = _resume(
        job_posting_id=pid,
        blob=_docx_bytes(paragraph="Jane Candidate", cell="Senior PM at Acme"),
        file_name="resume.docx",
        content_type=_DOCX_CT,
    )
    db_session.add(ar)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/resumes/extract-text")  # dry_run defaults True

    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert "DRY RUN" in body["message"]
    assert body["extracted"] == 1
    assert "written" not in body
    row = next(r for r in body["per_row"] if r["id"] == str(ar.id))
    assert "Jane Candidate" in row["preview"]
    assert "Senior PM at Acme" in row["preview"]
    # Nothing persisted.
    await db_session.refresh(ar)
    assert ar.resume_text is None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_write_populates_only_resume_text(db_session: Any) -> None:
    from job_assist.main import app

    pid = await _posting(db_session)
    ar = _resume(
        job_posting_id=pid,
        blob=_docx_bytes(paragraph="Jane Candidate", cell="Built X"),
        file_name="resume.docx",
        content_type=_DOCX_CT,
    )
    db_session.add(ar)
    await db_session.commit()
    blob_before, name_before, ct_before = ar.file_blob, ar.file_name, ar.content_type

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/resumes/extract-text?dry_run=false")

    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is False
    assert body["written"] == 1

    await db_session.refresh(ar)
    assert ar.resume_text is not None and "Jane Candidate" in ar.resume_text
    # Untouched columns.
    assert ar.file_blob == blob_before
    assert ar.file_name == name_before
    assert ar.content_type == ct_before


@_NEEDS_DB
@pytest.mark.asyncio
async def test_rows_with_text_are_skipped_idempotent(db_session: Any) -> None:
    from job_assist.main import app

    pid = await _posting(db_session)
    ar = _resume(
        job_posting_id=pid,
        blob=_docx_bytes(paragraph="Should Not Overwrite", cell="x"),
        file_name="resume.docx",
        content_type=_DOCX_CT,
        resume_text="ALREADY EXTRACTED",
    )
    db_session.add(ar)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/resumes/extract-text?dry_run=false")

    assert resp.status_code == 200
    assert all(r["id"] != str(ar.id) for r in resp.json()["per_row"])  # not scanned
    await db_session.refresh(ar)
    assert ar.resume_text == "ALREADY EXTRACTED"  # untouched


@_NEEDS_DB
@pytest.mark.asyncio
async def test_non_docx_blob_is_skipped_not_crashed(db_session: Any) -> None:
    from job_assist.main import app

    pid = await _posting(db_session)
    ar = _resume(
        job_posting_id=pid,
        blob=b"%PDF-1.4 not a docx",
        file_name="resume.pdf",
        content_type="application/pdf",
    )
    db_session.add(ar)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/resumes/extract-text?dry_run=false")

    assert resp.status_code == 200
    body = resp.json()
    assert body["skipped_non_docx"] >= 1
    assert any(f["id"] == str(ar.id) for f in body["failed"])
    await db_session.refresh(ar)
    assert ar.resume_text is None


@_NEEDS_DB
@pytest.mark.asyncio
async def test_readback_returns_text_and_404s_on_unknown(db_session: Any) -> None:
    from job_assist.main import app

    pid = await _posting(db_session)
    ar = _resume(
        job_posting_id=pid,
        blob=None,
        file_name=None,
        content_type=None,
        resume_text="Stored resume text body",
    )
    db_session.add(ar)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get(f"/admin/resumes/{ar.id}/text")
        missing = await client.get(f"/admin/resumes/{uuid.uuid4()}/text")

    assert ok.status_code == 200
    d = ok.json()
    assert d["resume_text"] == "Stored resume text body"
    assert d["char_count"] == len("Stored resume text body")
    assert d["has_file_blob"] is False
    assert missing.status_code == 404
