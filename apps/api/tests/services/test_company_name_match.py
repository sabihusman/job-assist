"""Pure unit tests for company-name extraction + normalization + ambiguity
(feat/company-app-awareness). No DB."""

from __future__ import annotations

import pytest

from job_assist.services.company_name_match import (
    ambiguous_keys,
    company_from_subject,
    normalize_company_name,
)


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Thank you for applying to Stripe", "Stripe"),
        ("Thanks for applying at Wealthsimple", "Wealthsimple"),
        ("Your application to Ramp - Senior PM", "Ramp"),
        ("Covr Financial Technologies - Jr. Product Manager", None),  # no apply verb
        ("Acme's Recruiting Team", "Acme"),
        ("Update on Your Application", None),
        ("", None),
        (None, None),
    ],
)
def test_company_from_subject(subject: str | None, expected: str | None) -> None:
    assert company_from_subject(subject) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Stripe, Inc.", "stripe"),
        ("STRIPE", "stripe"),
        ("stripe", "stripe"),
        ("Acme LLC", "acme"),
        ("Covr Financial Technologies", "covr financial technologies"),  # not over-stripped
        ("greenhouse.io", None),  # vendor
        ("myworkday", None),  # vendor
        ("Inc", None),  # all-suffix
        ("", None),
        (None, None),
    ],
)
def test_normalize_company_name(name: str | None, expected: str | None) -> None:
    assert normalize_company_name(name) == expected


def test_ambiguous_keys_subset_collision() -> None:
    keys = {"john hancock", "manulife john hancock", "stripe"}
    amb = ambiguous_keys(keys)
    assert amb == {"john hancock", "manulife john hancock"}
    assert "stripe" not in amb


def test_ambiguous_keys_no_collision() -> None:
    assert ambiguous_keys({"stripe", "ramp", "plaid"}) == set()
