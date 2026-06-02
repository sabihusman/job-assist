"""Semantic embedding population + validation (slice 1, feat/embeddings-slice1).

Populates pgvector columns so a FUTURE slice can blend cosine similarity into
ranking. THIS slice changes NO scoring: ``score_posting`` / ``fit_score`` /
``scorer_version`` / ``score_breakdown`` / the sort modes / ``postings_query``
are all untouched. The only consumers of these vectors are the opt-in sweep,
the profile-embed-on-save hook, and the read-only nearest-neighbour endpoint.

Mirrors ``services/jd_summary_enrichment.py``:
  * six-status state machine (embedded / skipped / exhausted / missing_context
    / error / not_found),
  * attempt cap (``settings.embedding_enrich_max_attempts``) with /retry reset,
  * cached unless stale — skip when a vector exists AND ``jd_text_hash_embedded
    == jd_text_hash``; re-embed when the JD text changed.

Row selector is OPEN rows (``closed_at IS NULL``); the vestigial ``should_embed``
flag is ignored (it is always False and was never wired).

Embedding model: ``text-embedding-004`` (768-dim) via the already-present
google-genai SDK + ``GEMINI_API_KEY``. Asymmetric task types — postings embed
as ``RETRIEVAL_DOCUMENT``, the profile query as ``RETRIEVAL_QUERY``.

SDK surface verified against google-genai 2.0.1:
  client.models.embed_content(model=..., contents=str,
      config=types.EmbedContentConfig(task_type=...))
  -> response.embeddings[0].values   # list[float], length 768

Mock seam: ``embed_text`` is the single network call — tests monkeypatch
``job_assist.services.embeddings.embed_text`` so the SDK never runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.config import settings
from job_assist.db.models import JobPosting, OperatorProfile

logger = logging.getLogger(__name__)

_ERROR_MAX_CHARS = 500
# JD text shorter than this is junk (header-only / truncated) — embedding it
# yields a meaningless vector. Mirror the jd-summary floor.
_JD_TEXT_MIN_CHARS = 100
# Truncate the embedded text — the first 3000 chars carry the role's signal
# and keep token cost bounded (mirrors the classifier's truncation).
_EMBED_TEXT_MAX_CHARS = 3000

# Task types (Gemini asymmetric retrieval embeddings).
_TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
_TASK_QUERY = "RETRIEVAL_QUERY"


EmbeddingStatus = Literal[
    "embedded",
    "skipped",
    "exhausted",
    "missing_context",
    "error",
    "not_found",
]


@dataclass(frozen=True)
class EmbeddingResult:
    """Outcome of a single ``embed_one_posting`` call."""

    status: EmbeddingStatus
    posting_id: str | None = None
    source: str | None = None
    error: str | None = None


@dataclass
class SweepSummary:
    """Counters returned by ``sweep_embeddings`` for the cron endpoint."""

    total: int = 0
    embedded: int = 0
    skipped: int = 0
    exhausted: int = 0
    missing_context: int = 0
    errors: int = 0
    error_details: list[dict[str, str]] = field(default_factory=list)

    def record(self, result: EmbeddingResult) -> None:
        self.total += 1
        if result.status == "embedded":
            self.embedded += 1
        elif result.status == "skipped":
            self.skipped += 1
        elif result.status == "exhausted":
            self.exhausted += 1
        elif result.status == "missing_context":
            self.missing_context += 1
        elif result.status == "error":
            self.errors += 1
            if result.posting_id and result.error:
                self.error_details.append(
                    {"posting_id": result.posting_id, "error": result.error[:200]}
                )


# ── Pure helpers ─────────────────────────────────────────────────────────────


def text_hash(text: str) -> str:
    """sha256 of the given text — used to detect profile-text changes."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def select_embedding_text(posting: JobPosting) -> tuple[str, str] | None:
    """Pick what to embed for a posting: the JD summary if present, else the
    truncated raw JD text.

    Returns ``(text, source)`` where source is ``"summary"`` | ``"jd_text"``,
    or ``None`` when neither source has enough signal (-> missing_context).
    Kept pure so the selection rule is unit-testable without a DB.
    """
    summary = (posting.jd_summary_markdown or "").strip()
    if len(summary) >= _JD_TEXT_MIN_CHARS:
        return summary[:_EMBED_TEXT_MAX_CHARS], "summary"
    jd = (posting.jd_text or "").strip()
    if len(jd) >= _JD_TEXT_MIN_CHARS:
        return jd[:_EMBED_TEXT_MAX_CHARS], "jd_text"
    return None


# ── Gemini embedding call (the single mock seam) ──────────────────────────────


async def embed_text(
    text: str,
    *,
    task_type: str = _TASK_DOCUMENT,
    api_key: str | None = None,
    model: str | None = None,
) -> list[float]:
    """Embed one text via Gemini ``text-embedding-004``.

    Returns a 768-float list. Raises ``RuntimeError`` / ``ValueError`` on a
    missing key, empty response, or dimension mismatch. Monkeypatched in tests
    so the SDK never actually runs there.

    SDK shape verified against google-genai 2.0.1 (see module docstring).
    """
    from google import genai
    from google.genai import types

    key = api_key if api_key is not None else settings.gemini_api_key
    if not key:
        raise RuntimeError("gemini_api_key is unset — cannot embed")

    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("cannot embed empty text")

    used_model = model or settings.embedding_model
    client = genai.Client(api_key=key)

    def _call() -> Any:
        return client.models.embed_content(
            model=used_model,
            contents=cleaned,
            config=types.EmbedContentConfig(task_type=task_type),
        )

    response = await asyncio.to_thread(_call)
    embeddings = getattr(response, "embeddings", None)
    if not embeddings:
        raise ValueError("Gemini returned no embeddings")
    values = list(getattr(embeddings[0], "values", []) or [])
    if len(values) != settings.embedding_dim:
        raise ValueError(
            f"embedding dim mismatch: got {len(values)}, expected {settings.embedding_dim}"
        )
    return [float(v) for v in values]


# ── Row-level orchestrator ────────────────────────────────────────────────────


async def _fetch_posting(session: AsyncSession, posting_id: uuid.UUID) -> JobPosting | None:
    return (
        await session.execute(select(JobPosting).where(JobPosting.id == posting_id))
    ).scalar_one_or_none()


async def embed_one_posting(
    session: AsyncSession,
    posting_id: uuid.UUID,
) -> EmbeddingResult:
    """Embed a single posting. Idempotent + cache-aware.

    Status meanings:
      * ``embedded``        — vector written, error cleared, embedded_at stamped
      * ``skipped``         — fresh vector already present (hash matches)
      * ``exhausted``       — attempt_count >= max; no call until /retry
      * ``missing_context`` — no JD summary and jd_text too short to embed
      * ``error``           — embedding call (or validation) failed
      * ``not_found``       — no row with that id
    """
    posting = await _fetch_posting(session, posting_id)
    if posting is None:
        return EmbeddingResult(status="not_found", posting_id=str(posting_id))

    pid = str(posting.id)

    # Cached unless stale: a vector exists AND the JD text hasn't changed.
    if posting.jd_embedding is not None and posting.jd_text_hash_embedded == posting.jd_text_hash:
        return EmbeddingResult(status="skipped", posting_id=pid)

    if posting.embedding_attempt_count >= settings.embedding_enrich_max_attempts:
        return EmbeddingResult(status="exhausted", posting_id=pid)

    selected = select_embedding_text(posting)
    if selected is None:
        # Mark the attempt so a perpetually-empty row eventually exhausts
        # instead of being re-picked every sweep.
        posting.embedding_error = "no summary and jd_text too short to embed"
        posting.embedding_attempt_count += 1
        await session.commit()
        return EmbeddingResult(status="missing_context", posting_id=pid)

    text, source = selected
    try:
        vector = await embed_text(text, task_type=_TASK_DOCUMENT)
    except Exception as exc:
        err = str(exc)[:_ERROR_MAX_CHARS]
        posting.embedding_error = err
        posting.embedding_attempt_count += 1
        await session.commit()
        logger.warning("embeddings.error", extra={"posting_id": pid, "error": err})
        return EmbeddingResult(status="error", posting_id=pid, source=source, error=err)

    posting.jd_embedding = vector
    posting.embedded_at = datetime.now(tz=UTC)
    posting.embedding_model_version = settings.embedding_model
    posting.jd_text_hash_embedded = posting.jd_text_hash
    posting.embedded_source = source
    posting.embedding_error = None
    # Leave attempt_count alone on success (audit trail).
    await session.commit()
    logger.info("embeddings.success", extra={"posting_id": pid, "source": source})
    return EmbeddingResult(status="embedded", posting_id=pid, source=source)


# ── Sweep ──────────────────────────────────────────────────────────────────────


async def sweep_embeddings(session: AsyncSession, limit: int = 100) -> SweepSummary:
    """Embed eligible OPEN postings; call ``embed_one_posting`` on each.

    Eligibility (filtered at the SELECT so a sweep doesn't fetch everything
    just to skip it):
      * ``closed_at IS NULL`` — don't embed removed postings,
      * not yet embedded OR stale (``jd_text_hash_embedded`` != ``jd_text_hash``),
      * ``embedding_attempt_count < max``.

    Order: never-tried first (attempt_count ASC), oldest within each tier
    (first_seen_at ASC) — drains the backlog, same as the jd-summary sweep.

    ``should_embed`` is intentionally NOT used as a selector — it is vestigial
    (always False) and would select zero rows.
    """
    eligible = (
        (
            await session.execute(
                select(JobPosting.id)
                .where(JobPosting.closed_at.is_(None))
                .where(
                    (JobPosting.jd_embedding.is_(None))
                    | (JobPosting.jd_text_hash_embedded != JobPosting.jd_text_hash)
                )
                .where(JobPosting.embedding_attempt_count < settings.embedding_enrich_max_attempts)
                .order_by(
                    JobPosting.embedding_attempt_count.asc(),
                    JobPosting.first_seen_at.asc(),
                )
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    summary = SweepSummary()
    for posting_id in eligible:
        try:
            result = await embed_one_posting(session, posting_id)
        except Exception as exc:
            # Defence-in-depth for commit paths outside embed_one's own try.
            await session.rollback()
            err = str(exc)[:_ERROR_MAX_CHARS]
            logger.warning(
                "embeddings.sweep_row_failed",
                extra={"posting_id": str(posting_id), "error": err},
            )
            result = EmbeddingResult(status="error", posting_id=str(posting_id), error=err)
        summary.record(result)
    return summary


async def reset_attempts_and_retry(
    session: AsyncSession,
    posting_id: uuid.UUID,
) -> EmbeddingResult:
    """Operator-only: zero the attempt counter, clear the vector, re-embed."""
    posting = await _fetch_posting(session, posting_id)
    if posting is None:
        return EmbeddingResult(status="not_found", posting_id=str(posting_id))
    posting.embedding_attempt_count = 0
    posting.embedding_error = None
    posting.jd_embedding = None
    posting.jd_text_hash_embedded = None
    posting.embedded_at = None
    posting.embedded_source = None
    await session.commit()
    return await embed_one_posting(session, posting_id)


# ── Profile embedding (PUT /operator/profile hook) ────────────────────────────


async def embed_profile_if_changed(session: AsyncSession) -> bool:
    """Re-embed the operator profile's ``looking_for_text`` when it changed.

    Hash-gated: a no-op save (text unchanged) skips the call. Empty text
    clears the vector. Returns True when a (re-)embed happened.

    CALLER WRAPS THIS IN try/except — an embedding failure must NEVER fail the
    profile save (same "must not cascade" contract as scoring at ingest).
    Commits its own change.
    """
    profile = (
        await session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one_or_none()
    if profile is None:
        return False

    text = (profile.looking_for_text or "").strip()
    if not text:
        # Cleared the field — drop any stale vector so nearest() returns empty.
        if profile.looking_for_embedding is not None:
            profile.looking_for_embedding = None
            profile.looking_for_embedding_hash = None
            profile.looking_for_embedded_at = None
            await session.commit()
        return False

    new_hash = text_hash(text)
    if profile.looking_for_embedding is not None and profile.looking_for_embedding_hash == new_hash:
        return False  # unchanged — no-op

    vector = await embed_text(text, task_type=_TASK_QUERY)
    profile.looking_for_embedding = vector
    profile.looking_for_embedding_hash = new_hash
    profile.looking_for_embedded_at = datetime.now(tz=UTC)
    await session.commit()
    logger.info("embeddings.profile_embedded", extra={"hash": new_hash[:12]})
    return True


# ── Validation gate: nearest postings to the profile vector ───────────────────


async def nearest_postings(session: AsyncSession, n: int = 20) -> dict[str, Any]:
    """Read-only: the N postings nearest the profile vector by cosine.

    The slice-1 go/no-go signal — lets the operator eyeball "are these the most
    relevant roles?" before any scoring change. Returns each posting's title /
    company / cosine similarity / heuristic ``fit_score`` (to see where semantic
    and heuristic agree or diverge) / ``embedded_source``, plus the cosine
    min/max/median spread across all embedded open rows (a compressed spread is
    an early calibration warning for slice 2).

    Returns ``{"available": False, "reason": ...}`` when the profile isn't
    embedded yet or no postings are embedded — never raises on empty state.
    """
    profile = (
        await session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one_or_none()
    if profile is None or profile.looking_for_embedding is None:
        return {
            "available": False,
            "reason": "profile not embedded yet — set looking_for_text and run a profile save",
            "results": [],
        }

    profile_vec = list(profile.looking_for_embedding)
    # pgvector cosine distance; cosine_sim = 1 - distance.
    distance = JobPosting.jd_embedding.cosine_distance(profile_vec)

    rows = (
        await session.execute(
            select(
                JobPosting.id,
                JobPosting.normalized_title,
                JobPosting.canonical_company_name,
                JobPosting.fit_score,
                JobPosting.embedded_source,
                distance.label("distance"),
            )
            .where(JobPosting.jd_embedding.is_not(None))
            .where(JobPosting.closed_at.is_(None))
            .order_by(distance.asc())
            .limit(n)
        )
    ).all()

    results = [
        {
            "posting_id": str(r.id),
            "title": r.normalized_title,
            "company": r.canonical_company_name,
            "cosine_sim": round(1.0 - float(r.distance), 4),
            "fit_score": r.fit_score,
            "embedded_source": r.embedded_source,
        }
        for r in rows
    ]

    # Spread across ALL embedded open rows (min/median/max similarity).
    agg = (
        await session.execute(
            select(
                func.count(),
                func.min(distance),
                func.max(distance),
                func.percentile_cont(0.5).within_group(distance.asc()),
            )
            .where(JobPosting.jd_embedding.is_not(None))
            .where(JobPosting.closed_at.is_(None))
        )
    ).one()
    embedded_count = int(agg[0] or 0)
    spread: dict[str, Any]
    if embedded_count == 0:
        spread = {"embedded_count": 0}
    else:
        # min distance -> max similarity, and vice versa.
        spread = {
            "embedded_count": embedded_count,
            "cosine_sim_max": round(1.0 - float(agg[1]), 4),
            "cosine_sim_median": round(1.0 - float(agg[3]), 4),
            "cosine_sim_min": round(1.0 - float(agg[2]), 4),
        }

    return {"available": True, "n": len(results), "results": results, "spread": spread}


__all__ = [
    "EmbeddingResult",
    "SweepSummary",
    "embed_one_posting",
    "embed_profile_if_changed",
    "embed_text",
    "nearest_postings",
    "reset_attempts_and_retry",
    "select_embedding_text",
    "sweep_embeddings",
    "text_hash",
]
