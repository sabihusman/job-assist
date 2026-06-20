"""The pre-labeler's allowed values must equal the production enums exactly.

If these drift, OpenAI's labels stop being comparable to Gemini's output. The
prompts/schemas derive their value lists from ``db.enums`` at import time, so
this is really a guard against someone hardcoding a divergent list later.
"""

from __future__ import annotations

from job_assist.db.enums import OutcomeType, RoleFamily, SeniorityLevel
from job_assist.eval.prompts import (
    email_response_schema,
    jd_response_schema,
)


def test_jd_schema_enums_match_production() -> None:
    schema = jd_response_schema()["schema"]["properties"]
    assert schema["role_family"]["enum"] == [m.value for m in RoleFamily]
    assert schema["seniority_level"]["enum"] == [m.value for m in SeniorityLevel]


def test_email_schema_enum_matches_production() -> None:
    schema = email_response_schema()["schema"]["properties"]
    assert schema["outcome_type"]["enum"] == [m.value for m in OutcomeType]
