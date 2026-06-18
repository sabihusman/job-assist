"""Applied-corpus basis loader (Phase A3).

Builds the :class:`AppliedBasis` (centroid + reference band + n) for the surgical
revealed-preference boost. The basis is the centroid of the operator's APPLIED,
NON-ROLE-GATED postings' jd_embeddings — the same membership the A2 read-only
``/admin/diagnostics/applied-similarity`` endpoint uses:

    resolved_status='applied' AND fit_score > 40 AND jd_embedding IS NOT NULL

Computed ONCE per scoring sweep (one cheap query over ~16 vectors) and injected
into the pure ``score_posting_decomposed`` so the scorer stays I/O-free.
Returns ``None`` when the basis is empty (n=0) — the boost then no-ops.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from job_assist.db.models import JobPosting
from job_assist.services.scoring import AppliedBasis

_APPLIED_BASIS_IDS_SQL = text(
    "SELECT jp.id FROM job_posting jp "
    "WHERE jp.jd_embedding IS NOT NULL AND jp.fit_score > 40 "
    "  AND COALESCE("
    "    (SELECT status FROM application_state WHERE job_posting_id = jp.id LIMIT 1), "
    "    CASE WHEN (SELECT action_type FROM posting_action "
    "               WHERE job_posting_id = jp.id ORDER BY created_at DESC LIMIT 1) "
    "             = 'applied' THEN 'applied' END) = 'applied'"
)


async def load_applied_basis(session: AsyncSession) -> AppliedBasis | None:
    """Build the applied-corpus basis, or None if empty."""
    ids = (await session.execute(_APPLIED_BASIS_IDS_SQL)).scalars().all()
    if not ids:
        return None
    emb_rows = (
        (await session.execute(select(JobPosting.jd_embedding).where(JobPosting.id.in_(ids))))
        .scalars()
        .all()
    )
    vecs = [[float(x) for x in v] for v in emb_rows if v is not None]
    n = len(vecs)
    if n == 0:
        return None
    dim = len(vecs[0])
    centroid = [sum(v[i] for v in vecs) / n for i in range(dim)]
    centroid_norm = sum(c * c for c in centroid) ** 0.5
    # reference_band = avg cosine of each (unit) basis vector to the centroid.
    if centroid_norm > 0:
        ref = (
            sum(sum(a * b for a, b in zip(v, centroid, strict=False)) for v in vecs)
            / centroid_norm
            / n
        )
    else:
        ref = 0.0
    return AppliedBasis(
        centroid=centroid,
        centroid_norm=centroid_norm,
        reference_band=round(ref, 6),
        n=n,
    )


__all__ = ["load_applied_basis"]
