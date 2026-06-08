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

Embedding model: ``gemini-embedding-001`` (output_dimensionality=768) via the
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
import math
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


def l2_normalize(vec: list[float]) -> list[float]:
    """Return the unit-length (L2-normalized) version of ``vec``.

    gemini-embedding-001 does NOT normalize sub-3072 outputs (we request 768),
    and the Gemini docs require the caller to do it. Cosine (our nearest()
    consumer) is scale-invariant so it's unaffected, but unit vectors are the
    doc-recommended storage form and keep us correct if a future slice uses
    inner-product / L2 distance. A zero vector is returned unchanged.
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


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
    """Embed one text via Gemini ``gemini-embedding-001`` (768-dim).

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
            # gemini-embedding-001 defaults to 3072 dims; pin to our Vector
            # column width (768). Cosine (the only consumer) is scale-invariant,
            # so the non-normalized truncated vector is fine for nearest().
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=settings.embedding_dim,
            ),
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
    return l2_normalize([float(v) for v in values])


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

    Concurrency (feat/sweep-skip-locked): rows are CLAIMED one at a time with
    ``FOR UPDATE SKIP LOCKED`` (``claim_next_id``) instead of bulk-selected, so
    overlapping sweeps can't grab the same row and double-call the embedding API.
    The lock is held through the row's embed call and released when
    ``embed_one_posting`` commits. A bulk ``FOR UPDATE`` would NOT work here: the
    first per-row commit releases every lock the bulk SELECT took.
    """
    from job_assist.services.sweep_claim import claim_next_id

    eligible_base = (
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
    )

    summary = SweepSummary()
    seen: set[uuid.UUID] = set()
    while len(seen) < limit:
        posting_id = await claim_next_id(session, eligible_base, JobPosting.id, seen)
        if posting_id is None:
            break  # no more eligible rows we can lock (drained, or locked by a peer run)
        seen.add(posting_id)
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
        # Eligible claimed rows always commit inside embed_one_posting; this is a
        # belt-and-braces release of the claim lock on any path that didn't.
        if session.in_transaction():
            await session.commit()
        summary.record(result)

    # slice 2a: when new vectors landed, the percentile ranks shift — recompute
    # the calibrated similarity_score across the corpus. Best-effort: a
    # calibration failure must not fail the sweep.
    if summary.embedded > 0:
        try:
            await recalibrate_similarity(session)
        except Exception as exc:
            await session.rollback()
            logger.warning("embeddings.recalibrate_failed", extra={"error": str(exc)[:300]})
        else:
            # slice 2b: similarity_score just changed, so the scorer's
            # semantic_fit feature is stale — re-score open postings so
            # fit_score reflects the new blend without a manual score sweep.
            # recalibrate_similarity committed above, so a re-score failure
            # here rolls back only its own work, not the calibration.
            try:
                from job_assist.services.rescore import rescore_open_postings

                await rescore_open_postings(session)
            except Exception as exc:
                await session.rollback()
                logger.warning("embeddings.rescore_failed", extra={"error": str(exc)[:300]})

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
                JobPosting.similarity_score,
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
            # slice 2a: the calibrated similarity (PERCENT_RANK 0-100). NULL
            # until POST /admin/embeddings/recalibrate has run.
            "similarity_score": r.similarity_score,
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

    # slice 2a verification: distribution of the CALIBRATED similarity_score
    # across embedded open rows. After recalibrate this should be a uniform
    # ~0-100 spread (vs the compressed 0.58-0.75 raw-cosine band).
    sim_agg = (
        await session.execute(
            select(
                func.count(JobPosting.similarity_score),
                func.min(JobPosting.similarity_score),
                func.percentile_cont(0.25).within_group(JobPosting.similarity_score.asc()),
                func.percentile_cont(0.5).within_group(JobPosting.similarity_score.asc()),
                func.percentile_cont(0.75).within_group(JobPosting.similarity_score.asc()),
                func.max(JobPosting.similarity_score),
            )
            .where(JobPosting.jd_embedding.is_not(None))
            .where(JobPosting.closed_at.is_(None))
            .where(JobPosting.similarity_score.is_not(None))
        )
    ).one()
    sim_count = int(sim_agg[0] or 0)
    similarity_spread: dict[str, Any]
    if sim_count == 0:
        similarity_spread = {
            "calibrated_count": 0,
            "note": "run POST /admin/embeddings/recalibrate",
        }
    else:
        similarity_spread = {
            "calibrated_count": sim_count,
            "min": int(sim_agg[1]),
            "p25": round(float(sim_agg[2]), 1),
            "median": round(float(sim_agg[3]), 1),
            "p75": round(float(sim_agg[4]), 1),
            "max": int(sim_agg[5]),
        }

    return {
        "available": True,
        "n": len(results),
        "results": results,
        "spread": spread,
        "similarity_spread": similarity_spread,
    }


# ── Calibration: cosine → PERCENT_RANK 0-100 (slice 2a) ───────────────────────


async def similarity_distribution(session: AsyncSession) -> dict[str, Any]:
    """Read-only: the ``similarity_score`` distribution + top-15 semantic roles
    across embedded open rows — the literal slice 2a verification gate.

    Exposed as a callable (not only the cached nearest GET) so the gate can be
    read off the reliable POST path. Issues the two gate queries verbatim:
    a count/min/percentile/max aggregate, then ``ORDER BY similarity_score DESC
    LIMIT 15``.
    """
    dist = (
        await session.execute(
            select(
                func.count(),  # count(*) — total embedded open rows
                func.count(JobPosting.similarity_score),  # calibrated rows
                func.min(JobPosting.similarity_score),
                func.percentile_cont(0.25).within_group(JobPosting.similarity_score.asc()),
                func.percentile_cont(0.5).within_group(JobPosting.similarity_score.asc()),
                func.percentile_cont(0.75).within_group(JobPosting.similarity_score.asc()),
                func.max(JobPosting.similarity_score),
            )
            .where(JobPosting.jd_embedding.is_not(None))
            .where(JobPosting.closed_at.is_(None))
        )
    ).one()
    total = int(dist[0] or 0)
    calibrated = int(dist[1] or 0)
    if calibrated == 0:
        distribution: dict[str, Any] = {
            "count": total,
            "calibrated_count": 0,
            "note": "run POST /admin/embeddings/recalibrate",
        }
    else:
        distribution = {
            "count": total,
            "calibrated_count": calibrated,
            "min": int(dist[2]),
            "p25": round(float(dist[3]), 1),
            "median": round(float(dist[4]), 1),
            "p75": round(float(dist[5]), 1),
            "max": int(dist[6]),
        }
    top_rows = (
        await session.execute(
            select(
                JobPosting.normalized_title,
                JobPosting.canonical_company_name,
                JobPosting.similarity_score,
                JobPosting.fit_score,
            )
            .where(JobPosting.similarity_score.is_not(None))
            .where(JobPosting.closed_at.is_(None))
            .order_by(JobPosting.similarity_score.desc())
            .limit(15)
        )
    ).all()
    top = [
        {
            "title": r.normalized_title,
            "company": r.canonical_company_name,
            "similarity_score": r.similarity_score,
            "fit_score": r.fit_score,
        }
        for r in top_rows
    ]
    return {"distribution": distribution, "top_by_similarity": top}


async def recalibrate_similarity(
    session: AsyncSession, *, include_distribution: bool = False
) -> dict[str, Any]:
    """Materialize ``job_posting.similarity_score`` for every embedded open row
    as ``100 * PERCENT_RANK()`` of its cosine-to-profile across the corpus.

    ONE SQL UPDATE...FROM pass, deterministic. Turns the compressed raw-cosine
    band (0.58-0.75) into a uniform 0-100 score that's directly comparable to
    ``fit_score`` for slice 2b's blend. Percentile depends on the profile
    vector, so this must re-run when the profile embedding changes (wired into
    the sweep tail + the PUT /operator/profile hook).

    No-op (calibrated=0) when the profile isn't embedded. Does NOT touch
    fit_score / score_posting — similarity_score is a separate column.

    ``include_distribution`` (endpoint only) appends the verification gate
    (distribution + top-15) so it can be read off the POST path; the sweep /
    PUT hooks leave it False to avoid the two extra queries on the hot path.
    """
    from typing import cast

    from sqlalchemy import Integer as SAInteger
    from sqlalchemy import update
    from sqlalchemy.engine import CursorResult

    profile = (
        await session.execute(select(OperatorProfile).where(OperatorProfile.id == 1))
    ).scalar_one_or_none()
    if profile is None or profile.looking_for_embedding is None:
        return {"available": False, "calibrated": 0, "reason": "profile not embedded yet"}

    profile_vec = list(profile.looking_for_embedding)
    distance = JobPosting.jd_embedding.cosine_distance(profile_vec)
    # Order by distance DESC → largest distance (least similar) gets
    # percent_rank 0, smallest distance (most similar) gets 1. Scale to 0-100.
    score_expr = func.round(100.0 * func.percent_rank().over(order_by=distance.desc())).cast(
        SAInteger
    )
    ranked = (
        select(JobPosting.id.label("jid"), score_expr.label("ss"))
        .where(JobPosting.jd_embedding.is_not(None))
        .where(JobPosting.closed_at.is_(None))
        .subquery()
    )
    result = await session.execute(
        update(JobPosting).where(JobPosting.id == ranked.c.jid).values(similarity_score=ranked.c.ss)
    )
    await session.commit()
    rowcount = cast("CursorResult[Any]", result).rowcount or 0
    out: dict[str, Any] = {"available": True, "calibrated": int(rowcount)}
    if include_distribution:
        out.update(await similarity_distribution(session))
    return out


__all__ = [
    "EmbeddingResult",
    "SweepSummary",
    "embed_one_posting",
    "embed_profile_if_changed",
    "embed_text",
    "l2_normalize",
    "nearest_postings",
    "recalibrate_similarity",
    "reset_attempts_and_retry",
    "select_embedding_text",
    "similarity_distribution",
    "sweep_embeddings",
    "text_hash",
]
