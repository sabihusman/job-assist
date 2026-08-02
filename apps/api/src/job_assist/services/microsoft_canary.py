"""Microsoft Careers canary check (directive B, Phase 3).

One lightweight live call — the "product manager" search page, page 1 only —
per health cycle. Deliberately separate from the real paginated ingest
(``adapters/microsoft.py::MicrosoftCareersAdapter.fetch_postings``, which
pages four seed queries and can issue hundreds of requests) so the canary
stays fast and cheap regardless of how the real ingest is throttled.

Classifies the outcome into exactly one of the three failure modes the
directive calls out, persists a ``MicrosoftCanaryRun`` row, and never raises
— a broken canary must not crash its caller (the health endpoint / the cron
step that triggers it); the row itself is the signal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from job_assist.adapters.base import BROWSER_HEADERS
from job_assist.adapters.microsoft import _should_keep_msft_title
from job_assist.db.models import MicrosoftCanaryRun

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_CANARY_URL = "https://apply.careers.microsoft.com/api/pcsx/search"
_CANARY_QUERY = "product manager"
_CANARY_TIMEOUT_SECONDS = 15.0


def _classify_payload(payload: Any) -> tuple[str, str | None, int | None]:
    """Pin the expected ``data.positions[].{id,name}`` shape (Phase 1 live
    trace). Any deviation is schema drift, not a silent empty result."""
    if not isinstance(payload, dict):
        return "schema_drift", "top-level response is not a JSON object", None
    data = payload.get("data")
    if not isinstance(data, dict):
        return "schema_drift", "response.data is missing or not an object", None
    positions = data.get("positions")
    if not isinstance(positions, list):
        return "schema_drift", "response.data.positions is missing or not a list", None
    for pos in positions:
        if not isinstance(pos, dict) or "id" not in pos or not isinstance(pos.get("name"), str):
            return (
                "schema_drift",
                "a position row is missing 'id'/'name' or has the wrong type",
                None,
            )

    matched = sum(1 for pos in positions if _should_keep_msft_title(pos.get("name")))
    if matched == 0:
        return (
            "zero_results",
            f"0 of {len(positions)} rows on the canary page matched the title filter "
            f"(query={_CANARY_QUERY!r} normally returns many)",
            0,
        )
    return "ok", None, matched


async def run_microsoft_canary(session: AsyncSession) -> MicrosoftCanaryRun:
    """Perform one live canary call, classify it, persist + return the row."""
    started_at = datetime.now(tz=UTC)
    status: str
    detail: str | None = None
    matched_count: int | None = None

    try:
        async with httpx.AsyncClient(
            timeout=_CANARY_TIMEOUT_SECONDS, headers=BROWSER_HEADERS
        ) as client:
            resp = await client.get(
                _CANARY_URL,
                params={
                    "domain": "microsoft.com",
                    "query": _CANARY_QUERY,
                    "location": "",
                    "start": 0,
                },
            )
    except httpx.HTTPError as exc:
        status, detail = "endpoint_failure", f"request error: {exc}"
    else:
        if resp.status_code != 200:
            status, detail = "endpoint_failure", f"HTTP {resp.status_code}"
        else:
            try:
                payload: Any = resp.json()
            except ValueError as exc:
                status, detail = "schema_drift", f"response is not valid JSON: {exc}"
            else:
                status, detail, matched_count = _classify_payload(payload)

    run = MicrosoftCanaryRun(
        started_at=started_at,
        finished_at=datetime.now(tz=UTC),
        status=status,
        detail=detail,
        matched_count=matched_count,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


__all__ = ["run_microsoft_canary"]
