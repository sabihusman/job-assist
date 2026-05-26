"""Pydantic schemas for the outreach_message endpoints (PR #52).

Manual logging from the Contacts page. PR #53 will add a
gmail-auto write path; the wire shapes here are designed so the
auto path can reuse :class:`OutreachMessageRead` without
modification.

Wire-shape note: the response field is named ``metadata`` (matching
the DB column), but the ORM attribute is ``message_metadata``
because SQLAlchemy reserves ``metadata`` on Base. The schema
aliases via :class:`Field(alias=)` so ``from_attributes=True``
picks up the ORM name on read and the JSON wire stays ``metadata``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from job_assist.db.enums import MessageChannel, MessageDirection

# ``MessageSource`` is the canonical vocabulary on the DB side (PR #52
# writes ``manual``; PR #53 will add ``gmail_auto``). The schema itself
# never accepts ``source`` from the client — the endpoint forces it.
# Kept out of imports here to avoid an unused-import lint while still
# being the single source of truth at the ORM/CHECK layer.

_VALID_DIRECTIONS = {d.value for d in MessageDirection}
_VALID_CHANNELS = {c.value for c in MessageChannel}


def _clean_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


class OutreachMessageCreate(BaseModel):
    """Body for ``POST /contacts/{contact_id}/outreach``.

    Operator-facing fields only. ``source`` is forced to ``'manual'``
    server-side; passing it in the body is rejected via
    ``extra='forbid'``. ``created_at`` is server-set.

    ``external_message_id`` is also rejected on manual writes — PR #53
    will populate it for ``gmail_auto`` rows via a separate internal
    code path that bypasses this schema.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    direction: str
    channel: str
    sent_at: datetime
    subject: str | None = None
    body: str | None = None
    posting_id: uuid.UUID | None = None
    message_metadata: dict[str, Any] | None = Field(default=None, alias="metadata")

    @field_validator("direction")
    @classmethod
    def _validate_direction(cls, value: str) -> str:
        trimmed = value.strip() if isinstance(value, str) else ""
        if trimmed not in _VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {sorted(_VALID_DIRECTIONS)}; got {trimmed!r}"
            )
        return trimmed

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, value: str) -> str:
        trimmed = value.strip() if isinstance(value, str) else ""
        if trimmed not in _VALID_CHANNELS:
            raise ValueError(f"channel must be one of {sorted(_VALID_CHANNELS)}; got {trimmed!r}")
        return trimmed

    @field_validator("subject", "body")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        return _clean_optional_str(value)

    @model_validator(mode="after")
    def _at_least_signal(self) -> OutreachMessageCreate:
        # An outreach row with neither subject nor body is operator
        # noise — they're saying "I reached out" without saying how.
        # If both are None, the row still validates: the operator
        # might just want to log "I sent the LinkedIn note" with no
        # body. Keep this permissive.
        return self


class OutreachMessageRead(BaseModel):
    """One row of an outreach_message response.

    Reads back EVERY field — the timeline UI needs all of them.
    ``source`` is included so PR #53's gmail_auto rows are visually
    distinguishable from manual ones in the timeline.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    direction: str
    channel: str
    subject: str | None
    body: str | None
    sent_at: datetime
    posting_id: uuid.UUID | None
    source: str
    external_message_id: str | None
    # ORM attribute is ``message_metadata`` (SQLAlchemy reserves
    # ``metadata`` on Base); wire JSON is ``metadata``.
    message_metadata: dict[str, Any] | None = Field(default=None, alias="metadata")
    created_at: datetime


class OutreachMessageListResponse(BaseModel):
    """Paginated envelope for ``GET /contacts/{contact_id}/outreach``."""

    total: int
    offset: int
    limit: int
    items: list[OutreachMessageRead]


class OutreachRecentItem(BaseModel):
    """Row shape for ``GET /outreach/recent``.

    Same message fields as :class:`OutreachMessageRead` plus a
    minimal contact summary (``contact_first_name``,
    ``contact_last_name``, ``contact_source_type``) so the
    cross-contact feed can render without a second round-trip.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    contact_first_name: str
    contact_last_name: str
    contact_source_type: str
    direction: str
    channel: str
    subject: str | None
    body: str | None
    sent_at: datetime
    posting_id: uuid.UUID | None
    source: str
    external_message_id: str | None
    message_metadata: dict[str, Any] | None = Field(default=None, alias="metadata")
    created_at: datetime


class OutreachRecentResponse(BaseModel):
    """Envelope for ``GET /outreach/recent``."""

    total: int
    offset: int
    limit: int
    items: list[OutreachRecentItem]
