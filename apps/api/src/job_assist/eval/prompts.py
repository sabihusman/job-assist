"""Independently-authored pre-labeling prompts + enum-exact JSON schemas.

These prompts are written from scratch for the OpenAI pre-labeler — deliberately
NOT copied from Gemini's classifier prompts — so the pre-labels are an
independent second opinion, not an echo of the model under test. What they DO
share with production is the enum vocabulary: the allowed values are pulled
straight from ``job_assist.db.enums`` so OpenAI's output is directly comparable
to Gemini's and can never drift out of the enum.

Bump the ``*_PROMPT_VERSION`` when a prompt changes; it is recorded in every
artifact for reproducibility.
"""

from __future__ import annotations

from typing import Any

from job_assist.db.enums import OutcomeType, RoleFamily, SeniorityLevel

JD_PROMPT_VERSION = "jd-prelabel-v1"
EMAIL_PROMPT_VERSION = "email-prelabel-v1"

ROLE_FAMILY_VALUES: list[str] = [m.value for m in RoleFamily]
SENIORITY_VALUES: list[str] = [m.value for m in SeniorityLevel]
OUTCOME_TYPE_VALUES: list[str] = [m.value for m in OutcomeType]


# ── JD classification (role_family + seniority_level) ────────────────────────

JD_SYSTEM_PROMPT = """\
You are an expert technical recruiter labeling job descriptions for an
evaluation dataset. Read the title and full job description and decide two
things, independently and carefully:

1. role_family — the functional discipline of the role.
2. seniority_level — the career altitude of the role.

Judge seniority from the ACTUAL scope, responsibilities, years of experience,
and leadership expectations described — NOT just the title. Titles routinely
under- or over-state altitude. In particular:
  - "Group", "Head of", "Director", "Lead", "Principal", "Staff" and people-
    management or org-ownership scope indicate SENIOR altitude even when the
    word "Manager" appears.
  - A high compensation floor for a nominally-junior title is a signal the role
    is actually mid/senior.
Be decisive but honest: if the description genuinely does not determine the
level, use "unknown" rather than guessing.

Return ONLY the two enum values, nothing else.
"""


def build_jd_user_prompt(title: str, jd_text: str) -> str:
    """Render the per-JD user message (independent of any production prompt)."""
    return (
        f"TITLE:\n{(title or '').strip()}\n\n"
        f"JOB DESCRIPTION:\n{(jd_text or '').strip()}\n\n"
        "Label role_family and seniority_level using the allowed enum values."
    )


def jd_response_schema() -> dict[str, Any]:
    """Structured-output schema constrained to the production enums."""
    return {
        "name": "jd_label",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "role_family": {"type": "string", "enum": ROLE_FAMILY_VALUES},
                "seniority_level": {"type": "string", "enum": SENIORITY_VALUES},
                "rationale": {
                    "type": "string",
                    "description": "One sentence justifying the seniority call.",
                },
            },
            "required": ["role_family", "seniority_level", "rationale"],
        },
        "strict": True,
    }


# ── Email outcome classification (outcome_type) ──────────────────────────────

EMAIL_SYSTEM_PROMPT = """\
You are labeling job-application-related emails for an evaluation dataset. Read
the email and assign the single outcome_type that best describes what the email
communicates about an application's lifecycle. Distinguish carefully between the
rejection stages (pre-screen vs post-screen vs post-interview) and the interview
stages (recruiter screen vs phone vs video vs onsite vs panel). If the email is
not about a specific job application at all, use "unrelated". If it is
application-related but none of the specific types fit, use "unclassified".

Return ONLY the enum value.
"""


def build_email_user_prompt(subject: str, body: str) -> str:
    """Render the per-email user message."""
    return (
        f"SUBJECT:\n{(subject or '').strip()}\n\n"
        f"BODY:\n{(body or '').strip()}\n\n"
        "Label outcome_type using the allowed enum values."
    )


def email_response_schema() -> dict[str, Any]:
    """Structured-output schema constrained to the production OutcomeType enum."""
    return {
        "name": "email_label",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "outcome_type": {"type": "string", "enum": OUTCOME_TYPE_VALUES},
                "rationale": {
                    "type": "string",
                    "description": "One short sentence justifying the call.",
                },
            },
            "required": ["outcome_type", "rationale"],
        },
        "strict": True,
    }
