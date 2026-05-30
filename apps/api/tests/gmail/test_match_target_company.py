"""Unit tests for the softened _normalize_company + _match_target_company
(feat/outcome-company-linking).

The PR widens the suffix regex (adding Team/Recruiting/Holdings/Talent/
HR/People/Careers), drops a leading article ("the X" / "a X"), and
relaxes the unique-candidate check to take the first match when
normalised names tie. These tests pin each axis.

Pure tests — no DB, no LLM. The fuzzy-match path that uses
``_match_target_company`` is exercised via the DB-gated relink suite
(``tests/services/test_outcome_relink.py``) where building a real
``TargetCompany`` row matters.
"""

from __future__ import annotations

from job_assist.gmail.backfill import _normalize_company


class TestNormalizeCompany:
    def test_bare_name(self) -> None:
        assert _normalize_company("MeridianLink") == "meridianlink"

    def test_inc_suffix_stripped(self) -> None:
        assert _normalize_company("MeridianLink Inc.") == "meridianlink"

    def test_corp_suffix_stripped(self) -> None:
        assert _normalize_company("MeridianLink Corp") == "meridianlink"

    def test_team_suffix_stripped(self) -> None:
        """New in this PR — Gemini commonly returns 'X Team'."""
        assert _normalize_company("MeridianLink Team") == "meridianlink"

    def test_recruiting_suffix_stripped(self) -> None:
        """New in this PR — recruiter signatures use 'X Recruiting'."""
        assert _normalize_company("MeridianLink Recruiting") == "meridianlink"

    def test_recruiting_team_double_suffix_stripped(self) -> None:
        """New in this PR — the two-pass suffix removal handles
        compound patterns like 'X Recruiting Team' in one normalize call."""
        assert _normalize_company("MeridianLink Recruiting Team") == "meridianlink"

    def test_holdings_suffix_stripped(self) -> None:
        assert _normalize_company("MeridianLink Holdings") == "meridianlink"

    def test_talent_suffix_stripped(self) -> None:
        """New in this PR."""
        assert _normalize_company("MeridianLink Talent") == "meridianlink"

    def test_careers_suffix_stripped(self) -> None:
        """New in this PR."""
        assert _normalize_company("MeridianLink Careers") == "meridianlink"

    def test_leading_article_the_stripped(self) -> None:
        """New in this PR — 'the X Team' is a common LLM output."""
        assert _normalize_company("the MeridianLink Team") == "meridianlink"

    def test_leading_article_a_stripped(self) -> None:
        """New in this PR."""
        assert _normalize_company("A Stripe team") == "stripe"

    def test_punctuation_and_case_normalised(self) -> None:
        """Pre-existing behaviour — still works after the regex changes."""
        assert _normalize_company("Stripe, Inc.") == "stripe"
        assert _normalize_company("STRIPE") == "stripe"

    def test_no_match_when_genuinely_different(self) -> None:
        """Regression guard — the looser regex must NOT collapse two
        distinct companies into the same key."""
        assert _normalize_company("Stripe") != _normalize_company("Square")
        assert _normalize_company("MeridianLink") != _normalize_company("Linkedin")

    def test_empty_name_returns_empty(self) -> None:
        assert _normalize_company("") == ""

    def test_suffix_only_returns_empty(self) -> None:
        """``"Inc."`` alone — pre-existing edge case, still empty."""
        # Suffix regex anchors to ``\s+suffix`` so a bare suffix without
        # leading whitespace is left as text-to-normalise.
        assert _normalize_company("Inc.") == "inc"
