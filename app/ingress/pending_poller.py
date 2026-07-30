from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from app.core.config import config
from app.db.postgres import PostgresClient
from app.ingress.reclaim import reclaim_stale
from app.pipeline.claimer import NotificationClaimer
from app.pipeline.runner import DeliveryRunner


class PendingPoller:
    def __init__(self, runner: DeliveryRunner, semaphore: asyncio.Semaphore) -> None:
        self.runner = runner
        self.semaphore = semaphore
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="pending-poller")
        logger.info(
            "Pending poller started interval={}s batch={}",
            config.POLL_INTERVAL_SECONDS,
            config.BATCH_SIZE,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("Pending poller tick failed: {}", exc)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=config.POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        session = PostgresClient.session()
        try:
            reclaimed = await asyncio.to_thread(reclaim_stale, session)
            if reclaimed:
                logger.warning("Reclaimed {} stale processing notification(s)", reclaimed)
        finally:
            session.close()

        session = PostgresClient.session()
        try:
            jobs = await asyncio.to_thread(
                NotificationClaimer.claim_pending_batch,
                session,
                config.BATCH_SIZE,
            )
        finally:
            session.close()

        if not jobs:
            return

        async def _run(job):
            async with self.semaphore:
                await asyncio.to_thread(self.runner.process_job, job)

        await asyncio.gather(*[_run(job) for job in jobs], return_exceptions=True)
