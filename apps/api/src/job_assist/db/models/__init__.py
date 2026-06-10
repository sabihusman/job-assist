"""ORM model package — importing this module registers all models with Base.metadata."""

from job_assist.db.models.application_resume import ApplicationResume
from job_assist.db.models.application_state import ApplicationState
from job_assist.db.models.closed_channel import ClosedChannel
from job_assist.db.models.contact import Contact
from job_assist.db.models.discovered_handle import DiscoveredHandle
from job_assist.db.models.division import Division
from job_assist.db.models.gmail_sweep_run import GmailSweepRun
from job_assist.db.models.ingest_run import IngestRun
from job_assist.db.models.job_posting import JobPosting
from job_assist.db.models.operator_profile import OperatorProfile
from job_assist.db.models.outcome_event import OutcomeEvent
from job_assist.db.models.outreach_message import OutreachMessage
from job_assist.db.models.posting_action import PostingAction
from job_assist.db.models.posting_source import PostingSource
from job_assist.db.models.resume_version import ResumeVersion
from job_assist.db.models.target_company import TargetCompany
from job_assist.db.models.triage_result import TriageResult

__all__ = [
    "ApplicationResume",
    "ApplicationState",
    "ClosedChannel",
    "Contact",
    "DiscoveredHandle",
    "Division",
    "GmailSweepRun",
    "IngestRun",
    "JobPosting",
    "OperatorProfile",
    "OutcomeEvent",
    "OutreachMessage",
    "PostingAction",
    "PostingSource",
    "ResumeVersion",
    "TargetCompany",
    "TriageResult",
]
