"""Unit tests for the Microsoft Careers canary (directive B, Phase 3).

No DB, no network — ``_classify_payload`` is pure, and ``run_microsoft_canary``
is exercised with a mocked httpx client + a mocked AsyncSession. The DB-backed
end-to-end path (canary row → GET /admin/ingest/health) is covered by
``tests/test_ingest_health.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from job_assist.services.microsoft_canary import _classify_payload, run_microsoft_canary


def _valid_payload(names: list[str]) -> dict[str, Any]:
    return {
        "status": 200,
        "data": {
            "positions": [{"id": i, "name": name} for i, name in enumerate(names)],
            "count": len(names),
        },
    }


class TestClassifyPayload:
    def test_ok_when_positions_match_title_filter(self) -> None:
        status, detail, matched = _classify_payload(
            _valid_payload(["Principal Product Manager", "Senior Program Manager"])
        )
        assert status == "ok"
        assert detail is None
        assert matched == 2

    def test_zero_results_when_no_position_matches(self) -> None:
        status, detail, matched = _classify_payload(
            _valid_payload(["Software Engineer", "Data Center Program Manager"])
        )
        assert status == "zero_results"
        assert matched == 0
        assert detail is not None

    def test_zero_results_when_positions_list_is_empty(self) -> None:
        status, _detail, matched = _classify_payload(_valid_payload([]))
        assert status == "zero_results"
        assert matched == 0

    def test_schema_drift_when_top_level_not_object(self) -> None:
        status, detail, matched = _classify_payload(["not", "a", "dict"])
        assert status == "schema_drift"
        assert matched is None
        assert detail is not None

    def test_schema_drift_when_data_missing(self) -> None:
        status, _detail, _matched = _classify_payload({"status": 200})
        assert status == "schema_drift"

    def test_schema_drift_when_positions_not_a_list(self) -> None:
        status, _detail, _matched = _classify_payload({"data": {"positions": "oops"}})
        assert status == "schema_drift"

    def test_schema_drift_when_a_row_missing_name(self) -> None:
        status, _detail, _matched = _classify_payload({"data": {"positions": [{"id": 1}]}})
        assert status == "schema_drift"

    def test_schema_drift_when_a_row_name_wrong_type(self) -> None:
        status, _detail, _matched = _classify_payload(
            {"data": {"positions": [{"id": 1, "name": 42}]}}
        )
        assert status == "schema_drift"


def _mock_client(mock_resp: MagicMock | None = None, *, get_side_effect: Any = None) -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = (
        AsyncMock(side_effect=get_side_effect)
        if get_side_effect
        else AsyncMock(return_value=mock_resp)
    )
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _mock_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.refresh = AsyncMock()
    return session


class TestRunMicrosoftCanary:
    async def test_ok_response_persists_ok_run(self) -> None:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = _valid_payload(["Principal Product Manager"])
        session = _mock_session()

        with patch(
            "job_assist.services.microsoft_canary.httpx.AsyncClient",
            return_value=_mock_client(resp),
        ):
            run = await run_microsoft_canary(session)

        session.commit.assert_awaited_once()
        assert run.status == "ok"
        assert run.matched_count == 1

    async def test_non_200_is_endpoint_failure(self) -> None:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 503
        resp.json.return_value = {}
        session = _mock_session()

        with patch(
            "job_assist.services.microsoft_canary.httpx.AsyncClient",
            return_value=_mock_client(resp),
        ):
            run = await run_microsoft_canary(session)

        assert run.status == "endpoint_failure"
        assert "503" in (run.detail or "")

    async def test_request_exception_is_endpoint_failure(self) -> None:
        session = _mock_session()

        with patch(
            "job_assist.services.microsoft_canary.httpx.AsyncClient",
            return_value=_mock_client(get_side_effect=httpx.ConnectError("dns failure")),
        ):
            run = await run_microsoft_canary(session)

        assert run.status == "endpoint_failure"
        assert "dns failure" in (run.detail or "")

    async def test_schema_drift_response_is_persisted(self) -> None:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = {"data": {"positions": "oops"}}
        session = _mock_session()

        with patch(
            "job_assist.services.microsoft_canary.httpx.AsyncClient",
            return_value=_mock_client(resp),
        ):
            run = await run_microsoft_canary(session)

        assert run.status == "schema_drift"
        assert run.matched_count is None

    async def test_zero_results_response_is_persisted(self) -> None:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = _valid_payload(["Software Engineer"])
        session = _mock_session()

        with patch(
            "job_assist.services.microsoft_canary.httpx.AsyncClient",
            return_value=_mock_client(resp),
        ):
            run = await run_microsoft_canary(session)

        assert run.status == "zero_results"
        assert run.matched_count == 0
