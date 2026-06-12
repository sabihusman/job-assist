"""Unit tests for Gmail header/body parsing.

We deliberately don't import GmailClient here — that touches
``googleapiclient.discovery.build`` at construction time, which hits the
network for the discovery doc. The parser is the only piece worth unit
testing in isolation; the API-fetch path is exercised end-to-end with a
mock in ``test_backfill.py``.

All fixture emails are synthetic — no real message IDs, no real From
addresses, no real bodies.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from job_assist.gmail.client import parse_message
from job_assist.gmail.models import RawEmail


def _b64(text: str) -> str:
    """URL-safe base64-encode the way Gmail does, stripping padding."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    *,
    msg_id: str = "msg_synthetic_001",
    headers: list[tuple[str, str]] | None = None,
    body_text: str | None = "Hello, this is a test body.",
    body_html: str | None = None,
    snippet: str = "preview text",
    labels: list[str] | None = None,
    internal_date_ms: int = 1715000000000,
) -> dict:
    """Build a synthetic Gmail messages.get payload."""
    hdrs = list(
        headers
        or [
            ("From", "Test Recruiter <recruiter@example-company.com>"),
            ("To", "applicant@example.com"),
            ("Subject", "Re: Your application"),
            ("Date", "Wed, 1 May 2024 12:00:00 +0000"),
        ]
    )

    parts: list[dict] = []
    if body_text is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": _b64(body_text)}})
    if body_html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": _b64(body_html)}})

    payload: dict = {"headers": [{"name": n, "value": v} for n, v in hdrs]}
    if len(parts) == 1:
        payload.update({"mimeType": parts[0]["mimeType"], "body": parts[0]["body"]})
    elif len(parts) > 1:
        payload["mimeType"] = "multipart/alternative"
        payload["parts"] = parts

    return {
        "id": msg_id,
        "threadId": f"thread_{msg_id}",
        "snippet": snippet,
        "internalDate": str(internal_date_ms),
        "labelIds": labels or ["INBOX"],
        "payload": payload,
    }


class TestParseMessage:
    def test_plain_text_body_extracted(self) -> None:
        email = parse_message(_msg(body_text="Thanks for applying to Acmecorp!"))
        assert "Thanks for applying to Acmecorp!" in email.body_text
        assert email.message_id == "msg_synthetic_001"
        assert email.from_address == "recruiter@example-company.com"
        assert email.from_domain == "example-company.com"
        assert email.from_name == "Test Recruiter"

    def test_html_fallback_when_no_plain(self) -> None:
        email = parse_message(_msg(body_text=None, body_html="<p>HTML only body</p>"))
        # parse_message returns the raw html in body_html; body_text is empty
        # because no text/plain part exists.
        assert email.body_text == ""
        assert email.body_html == "<p>HTML only body</p>"

    def test_subject_and_recipients(self) -> None:
        email = parse_message(
            _msg(
                headers=[
                    ("From", "noreply@anothercompany.com"),
                    ("To", "me@example.com, manager@example.com"),
                    ("Subject", "Interview invitation"),
                    ("Date", "Mon, 15 May 2026 10:00:00 -0700"),
                ]
            )
        )
        assert email.subject == "Interview invitation"
        assert set(email.to_addresses) == {"me@example.com", "manager@example.com"}
        # The "from" was a bare address with no display name.
        assert email.from_name is None
        assert email.from_domain == "anothercompany.com"

    def test_received_at_from_date_header(self) -> None:
        email = parse_message(
            _msg(
                headers=[
                    ("From", "x@y.com"),
                    ("To", "me@example.com"),
                    ("Subject", "x"),
                    ("Date", "Wed, 1 May 2024 12:00:00 +0000"),
                ]
            )
        )
        assert email.received_at.year == 2024
        assert email.received_at.month == 5

    def test_received_at_falls_back_to_internal_date(self) -> None:
        email = parse_message(
            _msg(
                headers=[("From", "x@y.com"), ("To", "me@example.com"), ("Subject", "x")],
                internal_date_ms=1_700_000_000_000,
            )
        )
        # ~ 2023-11-14
        assert email.received_at.year == 2023

    def test_quoted_reply_stripped_from_body(self) -> None:
        text = (
            "Thanks for the response — that works for me.\n"
            "\n"
            "On Mon, Dec 5, 2024 at 3:14 PM Recruiter <r@example.com> wrote:\n"
            "> Could you do Tuesday at 2pm PT for the screen?\n"
            "> Thanks!\n"
        )
        email = parse_message(_msg(body_text=text))
        # The reply prefix is in the head; the "On ... wrote:" preamble and
        # quoted lines are stripped.
        assert email.body_text.startswith("Thanks for the response")
        assert "Tuesday at 2pm" not in email.body_text
        assert "wrote:" not in email.body_text

    def test_snippet_preserved(self) -> None:
        email = parse_message(_msg(snippet="Auto-rejection notice"))
        assert email.snippet == "Auto-rejection notice"

    def test_labels_preserved(self) -> None:
        email = parse_message(_msg(labels=["INBOX", "CATEGORY_PERSONAL", "IMPORTANT"]))
        assert "INBOX" in email.labels
        assert "IMPORTANT" in email.labels

    def test_minimal_message_does_not_crash(self) -> None:
        """No body parts, no useful headers — parser must still return a RawEmail."""
        bare = {
            "id": "msg_bare",
            "threadId": None,
            "snippet": "",
            "internalDate": "1700000000000",
            "labelIds": [],
            "payload": {"headers": []},
        }
        email = parse_message(bare)
        assert isinstance(email, RawEmail)
        assert email.message_id == "msg_bare"
        assert email.from_address == ""
        assert email.subject == ""
        assert email.body_text == ""


def test_received_at_uses_utc_when_offset_zero() -> None:
    """sanity: Date header with +0000 round-trips to a UTC datetime."""
    email = parse_message(
        _msg(
            headers=[
                ("From", "a@b.com"),
                ("To", "me@example.com"),
                ("Subject", "x"),
                ("Date", "Wed, 1 May 2024 12:00:00 +0000"),
            ]
        )
    )
    assert email.received_at == datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC)


# ── fix/gmail-watermark: future-dated Date header must not poison the watermark


def test_received_at_clamped_when_date_header_is_in_the_future() -> None:
    """A forged/skewed future Date header is clamped so MAX(received_at) (the
    poll watermark) can never be pushed into the future."""
    from datetime import UTC, datetime

    future = "Wed, 1 May 2099 12:00:00 +0000"
    internal_ms = 1715000000000  # 2024-05-06, a sane real receipt time
    email = parse_message(
        _msg(
            headers=[
                ("From", "spam@x.com"),
                ("To", "me@example.com"),
                ("Subject", "You won"),
                ("Date", future),
            ],
            internal_date_ms=internal_ms,
        )
    )
    now = datetime.now(tz=UTC)
    assert email.received_at <= now, "future Date header must be clamped to <= now"
    # Clamps to internalDate when that is in the past (Gmail's own clock).
    assert email.received_at == datetime.fromtimestamp(internal_ms / 1000, tz=UTC)


def test_internaldate_fallback_is_utc_aware_not_naive_local() -> None:
    """When the Date header is missing, the internalDate fallback must be a
    tz-aware UTC datetime (not naive local time on a non-UTC host)."""
    from datetime import UTC, datetime

    msg = _msg(headers=[("From", "a@b.com"), ("Subject", "x")], internal_date_ms=1715000000000)
    # Drop the Date header so the fallback path runs.
    msg["payload"]["headers"] = [
        h for h in msg["payload"]["headers"] if h["name"].lower() != "date"
    ]
    email = parse_message(msg)
    assert email.received_at.tzinfo is not None
    assert email.received_at == datetime.fromtimestamp(1715000000000 / 1000, tz=UTC)
