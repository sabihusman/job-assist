"""Pydantic ContactSeedRow validation tests (PR #39).

Sync, no DB. Synthetic data only — never real PII from the Tippie
directory.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_assist.schemas.contact import ContactSeedRow


def _base(**overrides: object) -> dict[str, object]:
    """Minimum viable row; tests override one field at a time."""
    row: dict[str, object] = {
        "first_name": "Test",
        "last_name": "User",
        "email_primary": "test@example.com",
        "source_type": "tippie_alumni",
    }
    row.update(overrides)
    return row


# ── 1 ─────────────────────────────────────────────────────────────────────────
def test_contact_seed_row_requires_name() -> None:
    """Empty first_name or last_name → ValidationError."""
    with pytest.raises(ValidationError):
        ContactSeedRow.model_validate(_base(first_name=""))
    with pytest.raises(ValidationError):
        ContactSeedRow.model_validate(_base(last_name="  "))


# ── 2 ─────────────────────────────────────────────────────────────────────────
def test_contact_seed_row_requires_email_or_linkedin() -> None:
    """Neither channel set → model_validator rejects."""
    with pytest.raises(ValidationError) as exc:
        ContactSeedRow.model_validate(
            _base(email_primary=None, linkedin_url=None),
        )
    assert "at least one" in str(exc.value).lower()

    # Either alone is fine.
    ContactSeedRow.model_validate(_base(email_primary=None, linkedin_url="linkedin.com/in/x"))
    ContactSeedRow.model_validate(_base(email_primary="a@b.co", linkedin_url=None))


# ── 3 ─────────────────────────────────────────────────────────────────────────
def test_contact_seed_row_rejects_unknown_source_type() -> None:
    with pytest.raises(ValidationError):
        ContactSeedRow.model_validate(_base(source_type="cold_email"))
    # All four known values pass.
    for st in (
        "tippie_alumni",
        "linkedin_outreach",
        "recruiter_inbound",
        "warm_intro",
    ):
        ContactSeedRow.model_validate(_base(source_type=st))


# ── 4 ─────────────────────────────────────────────────────────────────────────
def test_contact_seed_row_normalizes_linkedin_url() -> None:
    """Strips www., trailing slash, query params; lowercases scheme/host."""
    variants = [
        "https://www.linkedin.com/in/foo/",
        "https://linkedin.com/in/foo",
        "http://www.linkedin.com/in/foo/",
        "  https://www.linkedin.com/in/foo/?utm=share  ",
        "linkedin.com/in/foo",
    ]
    for raw in variants:
        row = ContactSeedRow.model_validate(_base(linkedin_url=raw))
        assert row.linkedin_url == "https://linkedin.com/in/foo"

    with pytest.raises(ValidationError):
        ContactSeedRow.model_validate(_base(linkedin_url="https://example.com/in/foo"))


# ── 5 ─────────────────────────────────────────────────────────────────────────
def test_contact_seed_row_lowercases_email() -> None:
    row = ContactSeedRow.model_validate(_base(email_primary="  Foo.Bar@EXAMPLE.com  "))
    assert row.email_primary == "foo.bar@example.com"

    row2 = ContactSeedRow.model_validate(_base(email_secondary="Other@Example.COM"))
    assert row2.email_secondary == "other@example.com"


# ── 6 ─────────────────────────────────────────────────────────────────────────
def test_contact_seed_row_strips_list_field_whitespace() -> None:
    row = ContactSeedRow.model_validate(_base(industries_of_interest=["  Finance ", "Tech", "   "]))
    assert row.industries_of_interest == ["Finance", "Tech"]


# ── 7 ─────────────────────────────────────────────────────────────────────────
def test_contact_seed_row_dedupes_list_fields() -> None:
    row = ContactSeedRow.model_validate(
        _base(job_functions_of_interest=["Sales", "sales", "Sales", "Marketing"])
    )
    # Note: dedup is case-sensitive (matches operator_profile behavior);
    # "Sales" and "sales" are distinct list entries but the literal dup
    # is folded.
    assert row.job_functions_of_interest == ["Sales", "sales", "Marketing"]


# ── 8 ─────────────────────────────────────────────────────────────────────────
def test_contact_seed_row_accepts_null_list_fields() -> None:
    row = ContactSeedRow.model_validate(
        _base(
            job_functions_of_interest=None,
            industries_of_interest=None,
            contact_opt_in_topics=None,
        )
    )
    assert row.job_functions_of_interest is None
    assert row.industries_of_interest is None
    assert row.contact_opt_in_topics is None
