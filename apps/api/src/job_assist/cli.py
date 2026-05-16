"""CLI entry point for job-assist commands.

Usage examples
──────────────
  job-assist ingest greenhouse --handle stripe
  job-assist ingest greenhouse --all
  job-assist discover-ats --name "Stripe"
  job-assist discover-ats --name "Q2 Holdings"
  job-assist discover-ats --all
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

import httpx
import typer

# (done, total) -> None — used by discover_target_companies to drive a progress bar.
ProgressCallback = Callable[[int, int], None]

app = typer.Typer(
    name="job-assist",
    help="Job Assist — job-search aggregation and ATS discovery CLI.",
    no_args_is_help=True,
)

# ── ATS probe URLs ─────────────────────────────────────────────────────────────

_PROBE_URLS: dict[str, str] = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{handle}/jobs",
    "lever": "https://api.lever.co/v0/postings/{handle}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{handle}",
}


# ── Handle generation ─────────────────────────────────────────────────────────

# Trailing corporate suffixes worth stripping before generating handles.
# Match at end-of-string so we don't accidentally drop mid-name "Group" tokens.
_CORPORATE_SUFFIX_RE = re.compile(
    r"\s+(?:inc|llc|ltd|corp|corporation|company|co|holdings?|group|plc|gmbh)\.?\s*$",
    re.IGNORECASE,
)

# Names get split on these separators into independent sub-names. Each side
# contributes its own handle variants — e.g. "John Hancock / Manulife US"
# produces candidates for both halves.
_NAME_SPLIT_RE = re.compile(r"\s*[/&]\s*")


def _simple_handles(name: str) -> list[str]:
    """Handle variants for a single, already-split sub-name."""
    clean = re.sub(r"[^\w\s-]", "", name.lower())
    words = clean.split()
    out: list[str] = []
    if not words:
        return out
    out.append("".join(words))  # all words joined
    out.append("-".join(words))  # hyphenated
    out.append(words[0])  # first word
    if len(words) >= 2:
        out.append("".join(words[:2]))  # first two words joined
    return out


def candidate_handles(name: str) -> list[str]:
    """Generate plausible ATS board handles from a company name.

    Examples
    --------
    "Stripe"                          -> ['stripe']
    "Q2 Holdings"                     -> ['q2holdings', 'q2-holdings', 'q2']
    "Morgan Stanley Wealth Management"-> ['morganstanleywealthmanagement',
                                          'morgan-stanley-wealth-management',
                                          'morganstanley']
    "Capital One"                     -> ['capitalone', 'capital-one', 'capital']
    "Acme Inc."                       -> includes 'acme' (suffix stripped)
    "John Hancock / Manulife US"      -> includes 'johnhancock' and 'manulife'
    "AT&T"                            -> includes 'att', 'at', 't'
    """
    sub_names = [s.strip() for s in _NAME_SPLIT_RE.split(name) if s.strip()]
    if not sub_names:
        sub_names = [name]

    candidates: list[str] = []

    # 1. Variants for the full original name (back-compat: callers that pass
    #    "Capital One" still get its original candidate list first).
    candidates.extend(_simple_handles(name))

    # 2. Variants for each '/' or '&' split half.
    if len(sub_names) > 1:
        for sub in sub_names:
            candidates.extend(_simple_handles(sub))

    # 3. Variants with trailing corporate suffix stripped (e.g. "Acme Inc." -> "Acme").
    for sub in sub_names if len(sub_names) > 1 else [name]:
        stripped = _CORPORATE_SUFFIX_RE.sub("", sub).strip()
        if stripped and stripped != sub:
            candidates.extend(_simple_handles(stripped))

    # Deduplicate, preserving the order above (most-specific first).
    seen: set[str] = set()
    deduped: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped


# ── ATS probing ───────────────────────────────────────────────────────────────

# Minimum postings required for a first-word-fallback handle match to count
# as a real hit. The first-word fallback (e.g. "charles" from "Charles
# Schwab") is intentionally aggressive and collides with any unrelated
# small board that owns that single word — we saw "Charles Schwab" pick up
# Berlin SaaS hello-charles.com (3 postings) and "Orion Advisor Solutions"
# pick up an unidentified 0-posting Ashby board. Requiring a substantive
# posting count gates against those false positives while still accepting
# legit matches like "Capital One" (Greenhouse "capital" with hundreds).
_FIRST_WORD_FALLBACK_MIN_POSTINGS = 5


def _is_first_word_fallback(name: str, handle: str) -> bool:
    """True when *handle* is only the first whitespace token of a multi-word
    *name* (and would never have been generated for a single-word name)."""
    tokens = name.split()
    if len(tokens) <= 1:
        return False
    return handle.lower() == tokens[0].lower()


def _extract_job_count(ats: str, data: object) -> int | None:
    """Pull a job count out of a per-ATS response. None means shape mismatch."""
    if ats == "greenhouse" and isinstance(data, dict):
        jobs = data.get("jobs", [])
        if isinstance(jobs, list):
            return len(jobs)
    elif ats == "lever" and isinstance(data, list):
        return len(data)
    elif ats == "ashby" and isinstance(data, dict):
        # Ashby boards return either jobPostings or jobs key.
        jobs = data.get("jobPostings") or data.get("jobs") or []
        if isinstance(jobs, list):
            return len(jobs)
    return None


async def _probe_company(
    name: str,
    client: httpx.AsyncClient,
) -> dict[str, object] | None:
    """Try each ATS for *name*. Return match dict or None on no match.

    Multi-word names probed via the first-word-only fallback must clear
    ``_FIRST_WORD_FALLBACK_MIN_POSTINGS`` to avoid generic-word collisions.
    """
    for handle in candidate_handles(name):
        for ats, url_template in _PROBE_URLS.items():
            url = url_template.format(handle=handle)
            try:
                resp = await client.get(url, timeout=10.0)
            except (httpx.HTTPError, httpx.TimeoutException):
                continue
            if resp.status_code != 200:
                continue
            try:
                data = resp.json()
            except Exception:
                continue

            job_count = _extract_job_count(ats, data)
            if job_count is None:
                # Response shape didn't validate — keep trying.
                continue

            # Gate low-confidence first-word fallback matches.
            if (
                _is_first_word_fallback(name, handle)
                and job_count < _FIRST_WORD_FALLBACK_MIN_POSTINGS
            ):
                continue

            return {"ats": ats, "handle": handle, "job_count": job_count}
    return None


# ── Commands ──────────────────────────────────────────────────────────────────


@app.command()
def ingest(
    ats: str = typer.Argument(..., help="ATS source: greenhouse | lever | ashby"),
    handle: str | None = typer.Option(None, "--handle", help="Company handle"),
    all_companies: bool = typer.Option(
        False, "--all", help="Ingest all target_company rows for this ATS"
    ),
) -> None:
    """Ingest job postings from an ATS source into Postgres."""
    if not handle and not all_companies:
        typer.echo("Provide --handle <handle> or --all", err=True)
        raise typer.Exit(1)
    asyncio.run(_ingest_async(ats, handle, all_companies))


_SUPPORTED_ATS = {"greenhouse", "lever", "ashby"}


async def _ingest_async(ats: str, handle: str | None, all_companies: bool) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from job_assist.adapters.base import Adapter
    from job_assist.config import settings
    from job_assist.db.models.target_company import TargetCompany
    from job_assist.services.ingestion import IngestionService

    if ats not in _SUPPORTED_ATS:
        typer.echo(
            f"Unsupported ATS: {ats!r}. Supported: {sorted(_SUPPORTED_ATS)}",
            err=True,
        )
        raise typer.Exit(1)

    # Build the adapter for the requested ATS.
    adapter: Adapter
    if ats == "greenhouse":
        from job_assist.adapters.greenhouse import GreenhouseAdapter

        adapter = GreenhouseAdapter()
    elif ats == "lever":
        from job_assist.adapters.lever import LeverAdapter

        adapter = LeverAdapter()
    elif ats == "ashby":
        from job_assist.adapters.ashby import AshbyAdapter

        adapter = AshbyAdapter()
    else:  # pragma: no cover — guarded by _SUPPORTED_ATS above
        raise typer.Exit(1)

    engine = create_async_engine(settings.database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    service = IngestionService()

    handles: list[str]
    if all_companies:
        async with factory() as session:
            rows = await session.execute(
                select(TargetCompany).where(
                    TargetCompany.ats == ats,
                    TargetCompany.ats_handle.isnot(None),
                )
            )
            companies = rows.scalars().all()
        handles = [c.ats_handle for c in companies if c.ats_handle]
        if not handles:
            typer.echo(f"No target_company rows with ats={ats} and a handle set.")
            return
    else:
        assert handle is not None
        handles = [handle]

    async with adapter:
        for h in handles:
            async with factory() as session:
                typer.echo(f"Ingesting {ats}/{h} …")
                run = await service.ingest_source(adapter, h, session)
                icon = "✓" if run.status == "success" else "✗"
                typer.echo(
                    f"  {icon} {h}: status={run.status}  "
                    f"new={run.postings_new}  updated={run.postings_updated}  "
                    f"fetched={run.postings_fetched}"
                )

    await engine.dispose()


@app.command(name="discover-ats")
def discover_ats(
    name: str | None = typer.Option(None, "--name", help="Company name to probe"),
    all_companies: bool = typer.Option(
        False, "--all", help="Probe all target_company rows with ats=unknown"
    ),
    commit: bool = typer.Option(
        False,
        "--commit",
        help="With --all, write detected ats + ats_handle back to target_company",
    ),
) -> None:
    """Probe a company across Greenhouse / Lever / Ashby and report the ATS."""
    if not name and not all_companies:
        typer.echo("Provide --name <name> or --all", err=True)
        raise typer.Exit(1)
    if commit and not all_companies:
        typer.echo("--commit is only meaningful with --all", err=True)
        raise typer.Exit(1)
    asyncio.run(_discover_async(name, all_companies, commit))


# Cap concurrent HTTP fan-out during a full target_company sweep.
_DISCOVER_CONCURRENCY = 10


async def _probe_with_semaphore(
    company_name: str,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
) -> dict[str, object] | None:
    async with sem:
        return await _probe_company(company_name, client)


async def _discover_async(
    name: str | None,
    all_companies: bool,
    commit: bool,
) -> None:
    if all_companies:
        await _discover_batch(commit)
        return

    # Single --name probe: one-liner, no DB writes.
    assert name is not None
    async with httpx.AsyncClient(follow_redirects=True) as client:
        match = await _probe_company(name, client)
    if match:
        typer.echo(
            f"✓  {name}: ats={match['ats']}  handle={match['handle']}  jobs={match['job_count']}"
        )
    else:
        typer.echo(f"✗  {name}: unknown — suggest manual check")


async def discover_target_companies(
    session: Any,
    *,
    commit: bool,
    on_progress: ProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Probe every ``target_company`` with ``ats='unknown'`` and return a summary.

    Returns ``(matched, unmatched)``:
      * ``matched`` — one dict per match with keys
        ``id``, ``name``, ``ats``, ``handle``, ``job_count``.
      * ``unmatched`` — list of company names that no probe could resolve.

    If ``commit`` is True, also writes ``ats`` and ``ats_handle`` back onto the
    matched rows inside this session and commits. The caller owns the session
    lifecycle (the CLI opens its own engine; the FastAPI endpoint uses the
    request-scoped dependency).

    ``on_progress(done, total)`` is invoked after each probe completes — used
    by the CLI to drive a Rich progress bar; FastAPI passes ``None``.
    """
    from sqlalchemy import select

    from job_assist.db.models.target_company import TargetCompany

    rows = await session.execute(select(TargetCompany).where(TargetCompany.ats == "unknown"))
    companies: list[TargetCompany] = list(rows.scalars().all())
    if not companies:
        return [], []

    sem = asyncio.Semaphore(_DISCOVER_CONCURRENCY)
    results: list[tuple[TargetCompany, dict[str, object] | None]] = []
    completed = 0
    total = len(companies)

    async with httpx.AsyncClient(follow_redirects=True) as client:

        async def run_one(
            c: TargetCompany,
        ) -> tuple[TargetCompany, dict[str, object] | None]:
            match = await _probe_with_semaphore(c.name, client, sem)
            return c, match

        for coro in asyncio.as_completed([run_one(c) for c in companies]):
            results.append(await coro)
            completed += 1
            if on_progress is not None:
                on_progress(completed, total)

    # Preserve input order for deterministic output.
    by_id = {c.id: r for c, r in results}
    ordered = [(c, by_id[c.id]) for c in companies]

    matched_pairs = [(c, m) for c, m in ordered if m is not None]
    unmatched_names = [c.name for c, m in ordered if m is None]

    if commit and matched_pairs:
        for tc, match in matched_pairs:
            tc.ats = str(match["ats"])  # type: ignore[assignment]
            tc.ats_handle = str(match["handle"])
        await session.commit()

    matched_summary = [
        {
            "id": str(c.id),
            "name": c.name,
            "ats": str(m["ats"]),
            "handle": str(m["handle"]),
            "job_count": _as_int(m.get("job_count")),
        }
        for c, m in matched_pairs
    ]
    return matched_summary, unmatched_names


def _as_int(value: object) -> int:
    """Best-effort int conversion; defaults to 0 for unexpected types."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


async def _discover_batch(commit: bool) -> None:
    """CLI entry into the shared discover flow with a Rich progress bar."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from job_assist.config import settings

    engine = create_async_engine(settings.database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    try:
        async with factory() as session:
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                TextColumn,
                TimeElapsedColumn,
            )

            # Pre-count so we can decide whether to render the progress bar
            # and to print the opening "Probing N companies…" line.
            from sqlalchemy import func, select

            from job_assist.db.models.target_company import TargetCompany

            count = (
                await session.execute(
                    select(func.count())
                    .select_from(TargetCompany)
                    .where(TargetCompany.ats == "unknown")
                )
            ).scalar_one()

            if count == 0:
                typer.echo("No target_company rows with ats='unknown' — nothing to probe.")
                return

            typer.echo(f"Probing {count} companies across Greenhouse/Lever/Ashby…")

            with Progress(
                TextColumn("[bold]Probing[/bold]"),
                BarColumn(),
                MofNCompleteColumn(),
                TextColumn("•"),
                TimeElapsedColumn(),
                transient=True,
            ) as progress:
                task_id = progress.add_task("probe", total=count)

                def on_progress(done: int, total: int) -> None:
                    progress.update(task_id, completed=done, total=total)

                matched, unmatched = await discover_target_companies(
                    session, commit=commit, on_progress=on_progress
                )

        _print_discover_summary(matched, unmatched, commit_applied=commit)
        if commit and matched:
            typer.echo(f"\nUpdated {len(matched)} target_company rows.")
    finally:
        await engine.dispose()


def _print_discover_summary(
    matched: list[dict[str, Any]],
    unmatched: list[str],
    *,
    commit_applied: bool,
) -> None:
    typer.echo("")
    typer.echo(f"Matched ({len(matched)}):")
    if not matched:
        typer.echo("  (none)")
    for m in matched:
        typer.echo(
            f"  {m['name']:<40s}  {m['ats']:<10s}  {m['handle']:<25s}  {m['job_count']} postings"
        )

    typer.echo("")
    typer.echo(f"Unmatched ({len(unmatched)}):")
    if not unmatched:
        typer.echo("  (none)")
    for n in unmatched:
        typer.echo(f"  {n}")

    if matched and not commit_applied:
        typer.echo("")
        typer.echo("Re-run with --commit to write these matches to target_company.")
