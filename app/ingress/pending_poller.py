from __future__ import annotations

import asyncio
import time
from typing import List, Optional, Set

from loguru import logger

from app.core.config import config
from app.db.postgres import PostgresClient
from app.domain.models import DeliveryJob
from app.eligibility.context import EffectiveConfig
from app.eligibility.notification_config_resolver import load_global_effective_config
from app.ingress.reclaim import reclaim_stale
from app.pipeline.claimer import NotificationClaimer
from app.pipeline.delivery_timing import aggregation_enabled
from app.pipeline.runner import DeliveryRunner


def poll_interval_seconds(delivery_config: EffectiveConfig) -> float:
    if aggregation_enabled(delivery_config):
        return float(delivery_config.aggregation_sec)
    return float(config.POLL_INTERVAL_SECONDS)


def sleep_after_tick_seconds(interval: float, elapsed: float) -> float:
    """Keep claim start→next claim start ≈ interval (not interval + work)."""
    return max(0.0, float(interval) - max(0.0, float(elapsed)))


class PendingPoller:
    def __init__(self, runner: DeliveryRunner, semaphore: asyncio.Semaphore) -> None:
        self.runner = runner
        self.semaphore = semaphore
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._interval_seconds = float(config.POLL_INTERVAL_SECONDS)
        self._in_flight: Set[asyncio.Task] = set()

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
        if self._in_flight:
            await asyncio.gather(*list(self._in_flight), return_exceptions=True)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            interval = self._interval_seconds
            try:
                interval, jobs = await self._claim_tick()
                self._interval_seconds = interval
                if jobs:
                    self._spawn_process(jobs)
            except Exception as exc:
                logger.exception("Pending poller tick failed: {}", exc)

            elapsed = time.monotonic() - started
            sleep_for = sleep_after_tick_seconds(interval, elapsed)
            logger.debug(
                "Poll cadence interval={}s elapsed={:.3f}s sleep={:.3f}s in_flight={}",
                interval,
                elapsed,
                sleep_for,
                len(self._in_flight),
            )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass

    async def _claim_tick(self) -> tuple[float, List[DeliveryJob]]:
        session = PostgresClient.session()
        try:
            reclaimed = await asyncio.to_thread(reclaim_stale, session)
            if reclaimed:
                logger.warning("Reclaimed {} stale processing notification(s)", reclaimed)
        finally:
            session.close()

        session = PostgresClient.session()
        try:
            delivery_config = load_global_effective_config(session)
            jobs = await asyncio.to_thread(
                NotificationClaimer.claim_pending_batch,
                session,
                config.BATCH_SIZE,
                config=delivery_config,
            )
        finally:
            session.close()

        interval = poll_interval_seconds(delivery_config)
        if not jobs:
            logger.debug("Poll tick: no pending notifications (next interval {}s)", interval)
            return interval, []

        notification_ids = [str(job.notification_id) for job in jobs]
        logger.info(
            "Poll tick claimed {} notification(s): {}",
            len(jobs),
            notification_ids,
        )
        return interval, jobs

    def _spawn_process(self, jobs: List[DeliveryJob]) -> None:
        task = asyncio.create_task(self._process_jobs(jobs), name="pending-process")
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)

    async def _process_jobs(self, jobs: List[DeliveryJob]) -> None:
        try:
            async with self.semaphore:
                await asyncio.to_thread(self.runner.process_batch, jobs)
            logger.info("Poll tick completed for {} notification(s)", len(jobs))
        except Exception as exc:
            logger.exception(
                "Poll process failed for {} notification(s): {}",
                len(jobs),
                exc,
            )
