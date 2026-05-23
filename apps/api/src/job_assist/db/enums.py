"""Shared Python enum types for all ORM models.

Each enum here maps 1:1 to a PostgreSQL native enum type created in
the Alembic migration.  Keeping them in one module avoids circular
imports and lets Alembic detect a single create/drop site per type.
"""

from __future__ import annotations

import enum


class ATS(enum.StrEnum):
    """ATS platform identifier — shared across target_company, posting_source, ingest_run."""

    greenhouse = "greenhouse"
    lever = "lever"
    ashby = "ashby"
    workday = "workday"
    # PR #55: iCIMS HTML+JSON-LD scraper. Enum extended via the
    # ``c6d7e8f9a0b1`` migration. The Python value must match the PG
    # enum value exactly — a typo here would silently fail the
    # SAEnum-validated cast on read.
    icims = "icims"
    other = "other"
    unknown = "unknown"


class RemoteType(enum.StrEnum):
    onsite = "onsite"
    hybrid = "hybrid"
    remote = "remote"
    unknown = "unknown"


class SalaryPeriod(enum.StrEnum):
    hourly = "hourly"
    annual = "annual"
    unknown = "unknown"


class SeniorityLevel(enum.StrEnum):
    intern = "intern"
    apm = "apm"
    pm = "pm"
    senior_pm = "senior_pm"
    lead_pm = "lead_pm"
    principal_pm = "principal_pm"
    unknown = "unknown"


class RoleFamily(enum.StrEnum):
    product_management = "product_management"
    product_owner = "product_owner"
    product_marketing = "product_marketing"
    program_management = "program_management"
    other = "other"


class FetchStatus(enum.StrEnum):
    ok = "ok"
    partial = "partial"
    failed = "failed"


class IngestRunStatus(enum.StrEnum):
    running = "running"
    success = "success"
    partial = "partial"
    failed = "failed"


class ApplicationStatus(enum.StrEnum):
    not_reviewed = "not_reviewed"
    interested = "interested"
    not_interested = "not_interested"
    applied = "applied"
    snoozed = "snoozed"


class OutcomeType(enum.StrEnum):
    application_confirmation = "application_confirmation"
    recruiter_screen_invite = "recruiter_screen_invite"
    phone_interview_invite = "phone_interview_invite"
    video_interview_invite = "video_interview_invite"
    onsite_interview_invite = "onsite_interview_invite"
    panel_interview_invite = "panel_interview_invite"
    offer = "offer"
    rejection_pre_screen = "rejection_pre_screen"
    rejection_post_screen = "rejection_post_screen"
    rejection_post_interview = "rejection_post_interview"
    withdrawn = "withdrawn"
    unrelated = "unrelated"
    unclassified = "unclassified"


class ActionType(enum.StrEnum):
    """Operator's action on a job posting (PR #31).

    Stored as plain TEXT in posting_action.action_type (CHECK-constrained),
    not a PG enum — keeps the vocabulary evolvable without schema migrations.
    """

    interested = "interested"
    not_interested = "not_interested"
    applied = "applied"
    snoozed = "snoozed"
    reset = "reset"


class ActionReason(enum.StrEnum):
    """Operator's reason for ``not_interested`` (PR #31).

    Required when ``action_type = not_interested``; forbidden otherwise.
    """

    wrong_role = "wrong_role"
    wrong_location = "wrong_location"
    comp_too_low = "comp_too_low"
    wrong_industry = "wrong_industry"
    wrong_stage = "wrong_stage"
    already_rejected_here = "already_rejected_here"
    just_not_feeling_it = "just_not_feeling_it"
    # PR #43: seniority-band reasons. The hard-rule filter now drops
    # postings outside the operator's selected seniority levels, but
    # these reasons capture the cases where a posting slipped through
    # (NULL seniority_level on the posting) and the operator wants the
    # calibration card to show why.
    too_senior = "too_senior"
    too_junior = "too_junior"


class ClosedChannelReason(enum.StrEnum):
    multiple_rejections = "multiple_rejections"
    culture_concern = "culture_concern"
    compensation_low = "compensation_low"
    recruiter_unprofessional = "recruiter_unprofessional"
    other = "other"
