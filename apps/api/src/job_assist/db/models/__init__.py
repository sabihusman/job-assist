"""ORM model package — importing this module registers all models with Base.metadata."""

from job_assist.db.models.application_state import ApplicationState
from job_assist.db.models.closed_channel import ClosedChannel
from job_assist.db.models.ingest_run import IngestRun
from job_assist.db.models.job_posting import JobPosting
from job_assist.db.models.operator_profile import OperatorProfile
from job_assist.db.models.outcome_event import OutcomeEvent
from job_assist.db.models.posting_source import PostingSource
from job_assist.db.models.target_company import TargetCompany
from job_assist.db.models.triage_result import TriageResult

__all__ = [
    "ApplicationState",
    "ClosedChannel",
    "IngestRun",
    "JobPosting",
    "OperatorProfile",
    "OutcomeEvent",
    "PostingSource",
    "TargetCompany",
    "TriageResult",
]
