"""SQLAlchemy declarative base.

All ORM models inherit from Base defined here.  Models are added in
Week 1 (job_posting, target_company, application_state, etc.).  The
module is imported by Alembic's env.py so that target_metadata is
populated correctly for autogenerate.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
