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
from app.monitoring.metrics import push_metrics
from app.pipeline.runner import DeliveryRunner
from app.services.gcp.pubsub import notification_pubsub, setup_pubsub_publisher


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    PostgresClient.initialize()
    setup_pubsub_publisher(
        notification_pubsub,
        config.PROJECT_ID or "",
        config.NOTIFICATION_PUBSUB_TOPIC_NAME,
    )
    runner = DeliveryRunner()
    semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_NOTIFICATIONS)
    poller = PendingPoller(runner, semaphore)
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
    summary_task = asyncio.create_task(_summary_loop(), name="metrics-summary")
    app.state.runner = runner
    app.state.poller = poller
    logger.info(
        "ai-agent-notification started poll_interval={}s batch_size={} notification_pubsub_topic={}",
        config.POLL_INTERVAL_SECONDS,
        config.BATCH_SIZE,
        config.NOTIFICATION_PUBSUB_TOPIC_NAME or "(not set)",
    )

    try:
        yield
    finally:
        stop_event = getattr(app.state, "summary_stop", None)
        if stop_event is not None:
            stop_event.set()
        if summary_task is not None:
            summary_task.cancel()
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
    """Readiness: DB reachable and notification Pub/Sub publisher configured."""
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database_unavailable: {exc}") from exc

    notification_pubsub_ok = bool(
        config.PROJECT_ID
        and config.NOTIFICATION_PUBSUB_TOPIC_NAME
        and notification_pubsub.publisher is not None
    )
    if config.ENV != "dev" and not notification_pubsub_ok:
        raise HTTPException(status_code=503, detail="notification_pubsub_not_configured")

    return {
        "status": "ready",
        "service": config.SERVICE_NAME,
        "notification_pubsub_configured": notification_pubsub_ok,
    }


@app.get("/metrics/snapshot")
async def metrics_snapshot() -> dict:
    """Process-local counter snapshot (does not reset). Prefer OTEL in prod."""
    return push_metrics.snapshot()
