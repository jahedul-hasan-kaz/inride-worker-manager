from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException
from loguru import logger
from sqlalchemy import text

from app.core.config import config
from app.db.postgres import PostgresClient
from app.db.session import session_scope
from app.ingress.pending_poller import PendingPoller
from app.ingress.pubsub_subscriber import ExpoPushSubscriber
from app.monitoring.metrics import push_metrics
from app.pipeline.runner import DeliveryRunner


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    PostgresClient.initialize()
    runner = DeliveryRunner()
    semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_NOTIFICATIONS)
    poller = PendingPoller(runner, semaphore)
    subscriber = ExpoPushSubscriber(runner, semaphore)
    summary_task: Optional[asyncio.Task] = None

    async def _summary_loop() -> None:
        stop = asyncio.Event()
        app.state.summary_stop = stop
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=config.SUMMARY_LOG_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                push_metrics.log_summary()

    poller.start()
    subscriber.start()
    summary_task = asyncio.create_task(_summary_loop(), name="metrics-summary")
    app.state.runner = runner
    app.state.poller = poller
    app.state.subscriber = subscriber
    logger.info("ai-agent-notification started")

    try:
        yield
    finally:
        stop_event = getattr(app.state, "summary_stop", None)
        if stop_event is not None:
            stop_event.set()
        if summary_task is not None:
            summary_task.cancel()
        await subscriber.stop()
        await poller.stop()
        push_metrics.log_summary()
        PostgresClient.close()
        logger.info("ai-agent-notification stopped")


app = FastAPI(title="ai-agent-notification", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": config.SERVICE_NAME}


@app.get("/ready")
async def ready() -> dict:
    """Readiness: DB reachable. Pub/Sub config required in non-dev when enabled."""
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database_unavailable: {exc}") from exc

    pubsub_ok = bool(config.PROJECT_ID and config.EXPO_PUSH_PUBSUB_SUBSCRIPTION)
    if config.ENV != "dev" and not pubsub_ok:
        raise HTTPException(status_code=503, detail="pubsub_not_configured")

    return {
        "status": "ready",
        "service": config.SERVICE_NAME,
        "pubsub_configured": pubsub_ok,
    }


@app.get("/metrics/snapshot")
async def metrics_snapshot() -> dict:
    """Process-local counter snapshot (does not reset). Prefer OTEL in prod."""
    return push_metrics.snapshot()
