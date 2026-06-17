"""Plain-text extraction from uploaded ``.docx`` resume blobs (Phase 2 backfill).

A ``.docx`` is a ZIP of WordprocessingML XML. This extractor uses ONLY the
standard library (``zipfile`` + ``xml.etree.ElementTree``) — no ``python-docx``
/ ``lxml`` dependency — to pull readable text out of ``word/document.xml``:

  * iterate every ``<w:p>`` (paragraph) in document order;
  * within each, concatenate ``<w:t>`` text runs, mapping ``<w:tab>``→tab and
    ``<w:br>``/``<w:cr>``→newline;
  * join paragraphs with newlines.

Table cell text is captured for free: table cells are ``<w:p>``/``<w:t>`` nested
inside ``<w:tbl>``, so the document-order paragraph walk picks them up. In-body
text boxes (``<w:txbxContent>``) are also under ``document.xml`` and are caught.

KNOWN LIMIT: content in HEADERS / FOOTERS lives in SEPARATE parts
(``word/header*.xml`` / ``word/footer*.xml``) and is NOT read here — resumes
sometimes put the name/contact block in a header. This is the deliberate
"first attempt"; if a dry-run shows missing header content, switch to a richer
extractor (e.g. mammoth) rather than silently shipping partial text.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile

# WordprocessingML main namespace.
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _qn(tag: str) -> str:
    return f"{{{_W}}}{tag}"


class ResumeExtractError(Exception):
    """The blob isn't a readable Word ``.docx`` (bad zip, missing part, etc.)."""


def looks_like_docx(content_type: str | None, file_name: str | None) -> bool:
    """Cheap gate so we only try to unzip actual Word docs (skip PDFs/blanks)."""
    ct = (content_type or "").lower()
    fn = (file_name or "").lower()
    return "wordprocessingml.document" in ct or ct == "application/msword" or fn.endswith(".docx")


def extract_docx_text(blob: bytes) -> str:
    """Extract readable text from a ``.docx`` byte blob (body paragraphs + tables).

    Raises :class:`ResumeExtractError` when the blob can't be read as a Word doc.
    Returns the joined text (paragraphs separated by newlines), trimmed.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ResumeExtractError("not a valid zip / .docx blob") from exc

    try:
        document_xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise ResumeExtractError("no word/document.xml — not a Word .docx") from exc

    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as exc:
        raise ResumeExtractError(f"document.xml parse error: {exc}") from exc

    paragraphs: list[str] = []
    t_tag, tab_tag, br_tag, cr_tag = _qn("t"), _qn("tab"), _qn("br"), _qn("cr")
    for paragraph in root.iter(_qn("p")):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == t_tag:
                parts.append(node.text or "")
            elif node.tag == tab_tag:
                parts.append("\t")
            elif node.tag in (br_tag, cr_tag):
                parts.append("\n")
        paragraphs.append("".join(parts))

    text = "\n".join(paragraphs)
    # Tidy: strip trailing whitespace per line; collapse 3+ blank lines to one.
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


__all__ = ["ResumeExtractError", "extract_docx_text", "looks_like_docx"]
