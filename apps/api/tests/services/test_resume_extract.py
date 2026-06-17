"""Pure tests for the stdlib .docx text extractor (feat/resume-text-extract).

No DB. Builds a minimal in-memory .docx (a paragraph + a table cell + a tab) and
asserts the extractor pulls BOTH paragraph and table-cell text — the table case
is the one resume templates rely on. Also covers the error paths.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from job_assist.services.resume_extract import (
    ResumeExtractError,
    extract_docx_text,
    looks_like_docx,
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_bytes(*, paragraph: str, cell: str, after: str) -> bytes:
    """A minimal but structurally valid word/document.xml in a zip — a body
    paragraph, a one-cell table, and a paragraph with a tab between two runs."""
    document_xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{_W}"><w:body>'
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>"
        f"<w:tbl><w:tr><w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        f"<w:p><w:r><w:t>{after}</w:t><w:tab/><w:t>Tail</w:t></w:r></w:p>"
        f"</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def test_extracts_paragraph_and_table_cell_text() -> None:
    text = extract_docx_text(_docx_bytes(paragraph="Hello World", cell="Inside A Table", after="X"))
    # Both the body paragraph AND the table-cell paragraph are captured.
    assert "Hello World" in text
    assert "Inside A Table" in text
    # Document order: paragraph before the table cell.
    assert text.index("Hello World") < text.index("Inside A Table")


def test_tab_run_becomes_a_tab_character() -> None:
    text = extract_docx_text(_docx_bytes(paragraph="P", cell="C", after="Left"))
    assert "Left\tTail" in text


def test_bad_blob_raises_extract_error() -> None:
    with pytest.raises(ResumeExtractError):
        extract_docx_text(b"this is not a zip")


def test_zip_without_document_xml_raises() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("not-word/foo.xml", "<x/>")
    with pytest.raises(ResumeExtractError):
        extract_docx_text(buf.getvalue())


def test_looks_like_docx_gate() -> None:
    assert looks_like_docx(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", None
    )
    assert looks_like_docx(None, "Resume.DOCX")
    assert not looks_like_docx("application/pdf", "resume.pdf")
    assert not looks_like_docx(None, None)
