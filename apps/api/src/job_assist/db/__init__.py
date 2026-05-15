"""Database package — ORM base, enums, and all model imports.

Importing this package registers every model with Base.metadata, which
is what Alembic's env.py needs for autogenerate and target_metadata.
"""

# Side-effect import: registers all models with Base.metadata.
import job_assist.db.models  # noqa: F401
from job_assist.db.base import Base

__all__ = ["Base"]
