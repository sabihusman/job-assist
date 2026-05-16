"""Gmail API client backed by env-string credentials.

The OAuth flow itself (consent screen, code exchange) is operator-run once
locally; the resulting ``credentials.json`` + ``refresh_token`` get uploaded
to Railway as environment variables. This module never touches the disk —
all auth state comes from the two strings the caller hands in.

Sync google-api-python-client calls are wrapped with ``asyncio.to_thread``
so the orchestrator stays async-friendly. We use HTTP/1.1 against a single
Resource instance per client; the underlying library is thread-safe enough
for the per-message access pattern here.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from job_assist.gmail.models import RawEmail

logger = logging.getLogger(__name__)

_GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_GMAIL_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Quoted-reply marker patterns used to truncate the body before we send it
# to the classifier. We don't need the prior thread context — the most
# recent message is what we're classifying.
_QUOTED_REPLY_PATTERNS = [
    re.compile(r"\nOn .+ wrote:\n", re.DOTALL),  # "On <date>, <name> wrote:"
    re.compile(r"\n-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"\nFrom:\s.+\nSent:\s", re.IGNORECASE),  # Outlook-style headers
    re.compile(r"\n>+\s"),  # >-prefixed quoted lines
]


def _credentials_from_env(credentials_json: str, refresh_token: str) -> Credentials:
    """Build a ``google.oauth2.credentials.Credentials`` from raw env strings.

    ``credentials_json`` is the full contents of the OAuth client JSON file
    Google Cloud Console gives you for a Desktop application. We pull the
    client_id + client_secret out of it and bind them with the long-lived
    refresh token so the library can mint access tokens on demand.
    """
    cfg = json.loads(credentials_json)
    # The wrapper key is either "installed" (Desktop) or "web". Accept both.
    inner = cfg.get("installed") or cfg.get("web") or cfg
    return Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=refresh_token,
        token_uri=inner.get("token_uri", _GMAIL_TOKEN_URI),
        client_id=inner["client_id"],
        client_secret=inner["client_secret"],
        scopes=_GMAIL_SCOPES,
    )


def _strip_quoted_replies(text: str) -> str:
    """Truncate at the first quoted-reply marker; return the stripped head."""
    earliest = len(text)
    for pat in _QUOTED_REPLY_PATTERNS:
        m = pat.search(text)
        if m and m.start() < earliest:
            earliest = m.start()
    return text[:earliest].strip()


def _decode_body(data: str) -> str:
    """Decode a Gmail-API base64url-encoded body string."""
    if not data:
        return ""
    # Gmail uses URL-safe base64 without padding; pad manually.
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode((data + padding).encode("ascii")).decode(
            "utf-8", errors="replace"
        )
    except Exception:  # pragma: no cover — defensive
        return ""


def _walk_parts(payload: dict[str, Any]) -> tuple[str, str]:
    """Return (text, html) extracted from a Gmail message payload tree."""
    text = ""
    html = ""

    def walk(node: dict[str, Any]) -> None:
        nonlocal text, html
        mime = node.get("mimeType", "")
        body = (node.get("body") or {}).get("data") or ""
        if mime == "text/plain" and body and not text:
            text = _decode_body(body)
        elif mime == "text/html" and body and not html:
            html = _decode_body(body)
        for part in node.get("parts") or []:
            walk(part)

    walk(payload)
    return text, html


def _headers_dict(headers: list[dict[str, str]]) -> dict[str, str]:
    """Lowercase-keyed header lookup."""
    return {h["name"].lower(): h["value"] for h in headers if h.get("name")}


def parse_message(msg: dict[str, Any]) -> RawEmail:
    """Convert a Gmail ``messages.get`` payload into a :class:`RawEmail`."""
    headers = _headers_dict((msg.get("payload") or {}).get("headers") or [])
    from_raw = headers.get("from", "")
    from_name, from_addr = parseaddr(from_raw)
    from_domain = from_addr.partition("@")[2].lower()

    to_raw = headers.get("to", "")
    to_addrs = [a for _, a in (parseaddr(x) for x in to_raw.split(",")) if a]

    subject = headers.get("subject", "")
    date_header = headers.get("date")
    received_at: datetime
    if date_header:
        try:
            received_at = parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            received_at = datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000)
    else:
        received_at = datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000)

    text, html = _walk_parts(msg.get("payload") or {})
    text_stripped = _strip_quoted_replies(text) if text else ""

    return RawEmail(
        message_id=str(msg["id"]),
        thread_id=msg.get("threadId"),
        from_address=from_addr,
        from_name=from_name or None,
        from_domain=from_domain,
        to_addresses=to_addrs,
        subject=subject,
        received_at=received_at,
        body_text=text_stripped,
        body_html=html,
        snippet=msg.get("snippet", ""),
        labels=list(msg.get("labelIds") or []),
    )


class GmailClient:
    """Async wrapper over the sync Gmail API client."""

    def __init__(self, credentials_json: str, refresh_token: str, user_id: str = "me") -> None:
        creds = _credentials_from_env(credentials_json, refresh_token)
        # cache_discovery=False avoids file-cache warnings on Railway containers.
        self._service: Resource = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self._user_id = user_id

    async def list_message_ids(
        self,
        after: datetime,
        before: datetime | None = None,
        max_results_per_page: int = 500,
    ) -> list[str]:
        """Return every message ID in ``[after, before)`` (before defaults to now)."""

        def _build_query() -> str:
            parts = [f"after:{after.strftime('%Y/%m/%d')}"]
            if before is not None:
                parts.append(f"before:{before.strftime('%Y/%m/%d')}")
            return " ".join(parts)

        query = _build_query()
        out: list[str] = []
        page_token: str | None = None

        def _list(token: str | None) -> dict[str, Any]:
            req = (
                self._service.users()
                .messages()
                .list(
                    userId=self._user_id,
                    q=query,
                    maxResults=max_results_per_page,
                    pageToken=token,
                )
            )
            return req.execute()  # type: ignore[no-any-return]

        while True:
            page = await asyncio.to_thread(_list, page_token)
            for m in page.get("messages") or []:
                if "id" in m:
                    out.append(str(m["id"]))
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return out

    async def get_message(self, message_id: str) -> RawEmail:
        """Fetch one message and parse it into :class:`RawEmail`."""

        def _fetch() -> dict[str, Any]:
            req = (
                self._service.users()
                .messages()
                .get(
                    userId=self._user_id,
                    id=message_id,
                    format="full",
                )
            )
            return req.execute()  # type: ignore[no-any-return]

        raw = await asyncio.to_thread(_fetch)
        return parse_message(raw)
