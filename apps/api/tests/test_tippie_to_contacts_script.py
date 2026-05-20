"""Tests for ``scripts/tippie_to_contacts_json.py`` (PR #39).

Sync, no DB. Most tests feed synthetic 25-element tuples directly to
``row_to_contact`` — that keeps the test suite independent of
openpyxl's file format quirks. One round-trip test through an
in-memory xlsx exercises ``convert_workbook`` so the openpyxl path
is covered too.

All test data is synthetic. The real Tippie alumni directory contains
PII for ~388 people and never lands in the test suite.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

# Load the script via importlib because ``scripts/`` is not a package.
_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "tippie_to_contacts_json.py"
_spec = importlib.util.spec_from_file_location("tippie_script", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
tippie_script: Any = importlib.util.module_from_spec(_spec)
sys.modules["tippie_script"] = tippie_script
_spec.loader.exec_module(tippie_script)


def _row(**overrides: object) -> tuple[Any, ...]:
    """Build a 25-element tuple matching the Tippie xlsx column layout.

    Defaults give a valid, non-skipped row so individual tests can
    blank a single column to test skip behavior.
    """
    cells: list[Any] = [None] * 25
    cells[0] = 1234  # form ID
    cells[3] = "primary@example.com"  # col 4 email
    cells[4] = "Jane Doe"  # col 5 full name
    cells[5] = "Janie"  # col 6 preferred
    cells[6] = "Doe"  # col 7 last
    cells[7] = "jane@uiowa.edu"  # col 8 uiowa
    cells[8] = "secondary@example.com"  # col 9 personal
    cells[9] = "https://linkedin.com/in/janedoe"  # col 10 linkedin
    cells[10] = "Iowa City"  # col 11 city
    cells[11] = "Iowa"  # col 12 state
    cells[12] = "USA"  # col 13 country
    cells[13] = "Cedar Rapids"  # col 14 metro
    cells[14] = "MBA;BBA"  # col 15 programs
    cells[15] = "Fall 2022"  # col 16
    cells[16] = "Spring 2024"  # col 17
    cells[17] = "Acme Corp"  # col 18
    cells[18] = "PM"  # col 19
    cells[19] = "Product;Strategy"  # col 20
    cells[20] = "Tech;Finance"  # col 21
    cells[21] = "Iowa State"  # col 22
    cells[22] = "U of Iowa"  # col 23
    cells[23] = "Cycling"  # col 24
    cells[24] = ""  # col 25 topics (empty → opt-out)

    for col_1based, value in overrides.items():
        idx = int(str(col_1based).removeprefix("col"))
        cells[idx - 1] = value
    return tuple(cells)


# ── 23 ────────────────────────────────────────────────────────────────────────
def test_script_maps_basic_row() -> None:
    out = tippie_script.row_to_contact(_row())
    assert out is not None
    assert out["first_name"] == "Janie"  # preferred wins
    assert out["last_name"] == "Doe"
    assert out["email_primary"] == "primary@example.com"
    assert out["email_secondary"] == "secondary@example.com"
    assert out["current_employer"] == "Acme Corp"
    assert out["current_position"] == "PM"
    assert out["location_city"] == "Iowa City"
    assert out["location_metro"] == "Cedar Rapids"
    assert out["source_type"] == "tippie_alumni"


# ── 24 ────────────────────────────────────────────────────────────────────────
def test_script_normalizes_linkedin_url() -> None:
    # Script does no normalisation itself — passes the raw URL through
    # so the Pydantic validator on the API side is the single source of
    # truth. Confirm we don't accidentally mutate the field.
    out = tippie_script.row_to_contact(_row(col10="https://www.linkedin.com/in/foo/"))
    assert out is not None
    assert out["linkedin_url"] == "https://www.linkedin.com/in/foo/"


# ── 25 ────────────────────────────────────────────────────────────────────────
def test_script_skips_rows_without_name() -> None:
    # All name-bearing fields blanked.
    assert tippie_script.row_to_contact(_row(col5="", col6="", col7="")) is None


# ── 26 ────────────────────────────────────────────────────────────────────────
def test_script_skips_rows_without_contact_channel() -> None:
    """No email and no linkedin → row is dropped."""
    out = tippie_script.row_to_contact(_row(col4="", col8="", col9="", col10=""))
    assert out is None


# ── 27 ────────────────────────────────────────────────────────────────────────
def test_script_parses_semicolon_delimited_lists() -> None:
    out = tippie_script.row_to_contact(_row(col20="Product;  Strategy ;;Design", col21=""))
    assert out is not None
    assert out["job_functions_of_interest"] == ["Product", "Strategy", "Design"]
    # Empty col 21 → None, not an empty list.
    assert out["industries_of_interest"] is None


# ── 28 ────────────────────────────────────────────────────────────────────────
def test_script_detects_opt_in_from_topics_column() -> None:
    out_off = tippie_script.row_to_contact(_row(col25=""))
    assert out_off is not None
    assert out_off["contact_opt_in"] is False
    assert out_off["contact_opt_in_topics"] is None

    out_on = tippie_script.row_to_contact(
        _row(col25="Career advice; Coffee chats; Hiring referrals")
    )
    assert out_on is not None
    assert out_on["contact_opt_in"] is True
    assert out_on["contact_opt_in_topics"] == [
        "Career advice",
        "Coffee chats",
        "Hiring referrals",
    ]


# ── 29 ────────────────────────────────────────────────────────────────────────
def test_script_populates_source_metadata_from_tippie_columns() -> None:
    out = tippie_script.row_to_contact(_row())
    assert out is not None
    md = out["source_metadata"]
    assert md["tippie_programs"] == ["MBA", "BBA"]
    assert md["semester_start"] == "Fall 2022"
    assert md["graduation_semester"] == "Spring 2024"
    assert md["previous_undergrad"] == "Iowa State"
    assert md["previous_graduate"] == "U of Iowa"
    assert md["hobbies"] == "Cycling"
    assert md["form_id"] == 1234


# ── round-trip through openpyxl ───────────────────────────────────────────────
def test_convert_workbook_reads_xlsx(tmp_path: Path) -> None:
    """End-to-end: write a synthetic 2-row xlsx and convert it back.

    Confirms the openpyxl read path works on a real file. The fixture is
    built in-memory so we don't commit any xlsx to the repo.
    """
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    # Header row.
    ws.append([f"col{i}" for i in range(1, 26)])
    # Two data rows: one valid, one with no contact channel (should skip).
    valid = list(_row())
    valid[4] = "Alice Apple"  # col 5
    valid[5] = "Ally"  # col 6 preferred
    valid[6] = "Apple"  # col 7 last
    valid[3] = "alice@example.com"  # col 4 email
    ws.append(valid)

    no_channel = list(_row())
    no_channel[3] = no_channel[7] = no_channel[8] = no_channel[9] = ""
    no_channel[4] = "Bob Banana"
    no_channel[6] = "Banana"
    ws.append(no_channel)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    path = tmp_path / "alumni.xlsx"
    path.write_bytes(buf.getvalue())

    rows, counts = tippie_script.convert_workbook(path)
    assert len(rows) == 1
    assert rows[0]["email_primary"] == "alice@example.com"
    assert counts["skipped_no_channel"] == 1


def test_script_output_is_valid_json(tmp_path: Path) -> None:
    """``json.dumps(rows)`` round-trips back to a list of dicts."""
    out = tippie_script.row_to_contact(_row())
    text = json.dumps([out], indent=2, default=str)
    parsed = json.loads(text)
    assert isinstance(parsed, list)
    assert parsed[0]["email_primary"] == "primary@example.com"
