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

import httpx
import typer

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


def candidate_handles(name: str) -> list[str]:
    """Generate plausible ATS board handles from a company name.

    Examples
    --------
    "Stripe"                          → ['stripe']
    "Q2 Holdings"                     → ['q2holdings', 'q2-holdings', 'q2']
    "Morgan Stanley Wealth Management"→ ['morganstanleywealthmanagement',
                                         'morgan-stanley-wealth-management',
                                         'morganstanley']
    "Capital One"                     → ['capitalone', 'capital-one', 'capital']
    """
    # Strip punctuation (keep hyphens), lowercase
    clean = re.sub(r"[^\w\s-]", "", name.lower())
    words = clean.split()

    candidates: list[str] = []

    # All words joined (e.g. "capitalone")
    candidates.append("".join(words))
    # All words hyphenated (e.g. "capital-one")
    candidates.append("-".join(words))
    # First word only (e.g. "stripe", "anthropic")
    if words:
        candidates.append(words[0])
    # First two words joined (e.g. "morganstanley")
    if len(words) >= 2:
        candidates.append("".join(words[:2]))

    # Deduplicate preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped


# ── ATS probing ───────────────────────────────────────────────────────────────


async def _probe_company(
    name: str,
    client: httpx.AsyncClient,
) -> dict[str, object] | None:
    """Try each ATS for *name*. Return match dict or None on no match."""
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

            # Validate the response looks like a real board
            if ats == "greenhouse":
                jobs = data.get("jobs", [])
                if isinstance(jobs, list):
                    return {"ats": ats, "handle": handle, "job_count": len(jobs)}
            elif ats == "lever":
                if isinstance(data, list):
                    return {"ats": ats, "handle": handle, "job_count": len(data)}
            elif ats == "ashby":
                # Ashby boards return either jobPostings or jobs key
                jobs = data.get("jobPostings") or data.get("jobs") or []
                if isinstance(jobs, list):
                    return {"ats": ats, "handle": handle, "job_count": len(jobs)}
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
) -> None:
    """Probe a company across Greenhouse / Lever / Ashby and report the ATS."""
    if not name and not all_companies:
        typer.echo("Provide --name <name> or --all", err=True)
        raise typer.Exit(1)
    asyncio.run(_discover_async(name, all_companies))


async def _discover_async(name: str | None, all_companies: bool) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from job_assist.config import settings
    from job_assist.db.models.target_company import TargetCompany

    names: list[str]

    if all_companies:
        engine = create_async_engine(settings.database_url)
        factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            rows = await session.execute(
                select(TargetCompany).where(TargetCompany.ats == "unknown")
            )
            companies = rows.scalars().all()
        names = [c.name for c in companies]
        await engine.dispose()
    else:
        assert name is not None
        names = [name]

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for company_name in names:
            match = await _probe_company(company_name, client)
            if match:
                typer.echo(
                    f"✓  {company_name}: ats={match['ats']}  "
                    f"handle={match['handle']}  jobs={match['job_count']}"
                )
            else:
                typer.echo(f"✗  {company_name}: unknown — suggest manual check")
