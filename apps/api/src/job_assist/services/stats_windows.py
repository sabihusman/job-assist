"""Time-window helpers for the /stats endpoints (PR #30b).

Default window is "this week" — Monday 00:00 UTC of the current week
through ``now()``. The frontend can override with ``?since=...&until=...``
ISO-8601 timestamps.

``_now()`` is a module-level seam so tests can inject a frozen clock
without pulling in freezegun. Production callers always get
``datetime.now(tz=UTC)``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

# ── Clock seam (test-only override) ──────────────────────────────────────────


def _default_clock() -> datetime:
    return datetime.now(tz=UTC)


_clock: Callable[[], datetime] = _default_clock


def _now() -> datetime:
    return _clock()


def set_clock(clock: Callable[[], datetime] | None) -> None:
    """Replace the module clock. Pass ``None`` to restore the default.

    Tests use this to freeze time so the default-window test asserts a
    deterministic Monday-of-the-week boundary. Not a public API — only
    referenced from the test module.
    """
    global _clock
    _clock = clock if clock is not None else _default_clock


# ── Defaults & validation ────────────────────────────────────────────────────


_MAX_LOOKBACK_DAYS = 365


def default_window() -> tuple[datetime, datetime]:
    """Return ``(monday_00_00_utc_this_week, now_utc)``.

    PG's ``date_trunc('week', ...)`` returns Monday too (ISO week), so
    the boundary stays consistent if the SQL is ever rewritten to do
    the bucketing server-side.
    """
    now = _now()
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday, now


def validate_window(
    since: datetime | None,
    until: datetime | None,
) -> tuple[datetime, datetime]:
    """Resolve ``since``/``until`` to a validated UTC pair.

    Defaults applied per ``default_window`` when both are missing. Mixed
    cases (only one supplied) fill the other from the default. Raises
    HTTPException(422) on:
      * since > until
      * since/until beyond now (future-dated)
      * since older than 365 days

    Naive datetimes are rejected only if Pydantic somehow lets them
    through — by this point both should already be tz-aware (FastAPI's
    ``datetime`` parser keeps the offset). Defensive ``replace(tzinfo=UTC)``
    on naive values keeps the comparisons safe.
    """
    now = _now()

    if since is None and until is None:
        return default_window()

    default_since, default_until = default_window()
    s = since if since is not None else default_since
    u = until if until is not None else default_until

    # Coerce naive to UTC so the comparisons below don't blow up.
    if s.tzinfo is None:
        s = s.replace(tzinfo=UTC)
    if u.tzinfo is None:
        u = u.replace(tzinfo=UTC)

    if s > u:
        raise HTTPException(
            status_code=422,
            detail=f"since={s.isoformat()} is after until={u.isoformat()}",
        )
    if s > now:
        raise HTTPException(
            status_code=422,
            detail=f"since={s.isoformat()} is in the future",
        )
    if u > now:
        raise HTTPException(
            status_code=422,
            detail=f"until={u.isoformat()} is in the future",
        )
    if (now - s) > timedelta(days=_MAX_LOOKBACK_DAYS):
        raise HTTPException(
            status_code=422,
            detail=f"since is more than {_MAX_LOOKBACK_DAYS} days in the past",
        )

    return s, u
