"""Unit tests for services/postings_query.py (feat/triage-export-xlsx).

The helper is pure (no DB). These tests pin the shape of
``PostingsQueryParts`` for a few representative specs so the export
endpoint and the list endpoint can rely on the same building blocks.
"""

from __future__ import annotations

import uuid

from job_assist.services.postings_query import (
    PostingsViewSpec,
    build_view_parts,
)


def test_default_spec_emits_two_default_where_clauses() -> None:
    """Defaults: hide closed AND hide hard-rule-failed."""
    parts = build_view_parts(PostingsViewSpec())
    # closed_at IS NULL + hard_rule_failed IS NULL.
    assert len(parts.where_clauses) == 2


def test_include_closed_and_filtered_drops_default_clauses() -> None:
    parts = build_view_parts(PostingsViewSpec(include_closed=True, include_filtered=True))
    assert parts.where_clauses == []


def test_state_filter_flips_needs_state_lateral() -> None:
    no_state = build_view_parts(PostingsViewSpec())
    with_state = build_view_parts(PostingsViewSpec(state=("triage",)))
    assert no_state.needs_state_lateral is False
    assert with_state.needs_state_lateral is True


def test_per_company_cap_zero_skips_capped_ids() -> None:
    parts = build_view_parts(PostingsViewSpec(per_company_cap=0))
    assert parts.capped_ids is None


def test_per_company_cap_positive_builds_capped_ids() -> None:
    parts = build_view_parts(PostingsViewSpec(per_company_cap=3))
    assert parts.capped_ids is not None


def test_sort_default_is_newest_by_first_seen_desc() -> None:
    parts = build_view_parts(PostingsViewSpec())
    # Two clauses: primary sort + id ASC tiebreaker.
    assert len(parts.order_clauses) == 2


def test_each_sort_key_produces_clauses() -> None:
    """Every SortKey maps to a non-empty ORDER BY — guards the exhaustive
    if/elif chain in build_view_parts from silently dropping a key."""
    for sort in (
        "newest",
        "oldest",
        "salary_high_to_low",
        "tier",
        "recently_posted",
        "best_fit",
        "best_fit_semantic",
    ):
        parts = build_view_parts(PostingsViewSpec(sort=sort))  # type: ignore[arg-type]
        assert len(parts.order_clauses) >= 1


def test_from_validated_coerces_lists_to_tuples() -> None:
    """Tuples keep the spec hashable; lists from FastAPI must be normalised."""
    spec = PostingsViewSpec.from_validated(
        tier=[1, 2],
        ats=["greenhouse"],
        remote_type=None,
        role_family=["product_management"],
        state=None,
        include_snoozed_past_only=False,
        target_company_id=None,
        sort="newest",
        per_company_cap=3,
        include_closed=False,
        include_filtered=False,
    )
    assert spec.tier == (1, 2)
    assert spec.ats == ("greenhouse",)
    assert spec.remote_type == ()
    assert spec.role_family == ("product_management",)
    assert spec.state == ()
    # Spec is hashable (frozen + tuples) — safe for use as a cache key.
    assert hash(spec) is not None


def test_target_company_id_added_to_where_clauses() -> None:
    """Setting target_company_id adds one more WHERE clause."""
    base = build_view_parts(PostingsViewSpec())
    with_company = build_view_parts(PostingsViewSpec(target_company_id=uuid.uuid4()))
    assert len(with_company.where_clauses) == len(base.where_clauses) + 1


def test_ats_filter_adds_one_exists_clause() -> None:
    base = build_view_parts(PostingsViewSpec())
    with_ats = build_view_parts(PostingsViewSpec(ats=("greenhouse",)))
    assert len(with_ats.where_clauses) == len(base.where_clauses) + 1
