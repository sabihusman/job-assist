"""Tests for the Gmail sweep mutual-exclusion guard (fix/audit).

A backfill (5-10 min under the Gemini throttle) overlapping the 15-minute
cron poll double-spends Gemini on shared messages, then the slower runner's
batch commit IntegrityErrors on the unique ``email_message_id`` — aborting up
to 24 legitimately-new rows. One non-blocking in-process lock prevents the
overlap entirely (single-uvicorn-worker deployment).

Pure asyncio — no DB needed.
"""

from __future__ import annotations

import asyncio

import pytest

from job_assist.gmail.sweep_lock import GmailSweepBusyError, gmail_sweep_slot


async def test_second_sweep_raises_busy_while_first_holds_the_slot() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def long_sweep() -> None:
        async with gmail_sweep_slot():
            entered.set()
            await release.wait()

    task = asyncio.create_task(long_sweep())
    await entered.wait()

    # A concurrent sweep must fail fast — not queue behind the first.
    with pytest.raises(GmailSweepBusyError):
        async with gmail_sweep_slot():
            pytest.fail("second sweep must not enter while the first holds the slot")

    release.set()
    await task


async def test_slot_frees_after_exit_even_on_error() -> None:
    # A sweep that raises must still release the slot for the next cron tick.
    with pytest.raises(RuntimeError):
        async with gmail_sweep_slot():
            raise RuntimeError("sweep blew up")

    # Slot is free again — entering succeeds.
    async with gmail_sweep_slot():
        pass


async def test_sequential_sweeps_do_not_collide() -> None:
    for _ in range(3):
        async with gmail_sweep_slot():
            pass
