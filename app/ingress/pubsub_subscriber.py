from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional
from uuid import UUID

from loguru import logger

from app.core.config import config
from app.domain.handle_result import HandleDisposition
from app.monitoring.metrics import push_metrics
from app.pipeline.runner import DeliveryRunner

try:
    from google.cloud import pubsub_v1
    from google.cloud.pubsub_v1.types import FlowControl
except ImportError:  # pragma: no cover
    pubsub_v1 = None
    FlowControl = None


def _delivery_attempt(message: Any) -> Optional[int]:
    attempt = getattr(message, "delivery_attempt", None)
    if attempt is None:
        return None
    try:
        return int(attempt)
    except (TypeError, ValueError):
        return None


def _attempts_exhausted(attempt: Optional[int]) -> bool:
    if attempt is None:
        return False
    return attempt >= config.PUBSUB_MAX_DELIVERY_ATTEMPTS


def _extend_ack_deadline(message: Any) -> None:
    """Keep the lease alive for long Expo fan-outs."""
    seconds = int(config.PUBSUB_ACK_EXTENSION_SECONDS)
    if seconds <= 0:
        return
    try:
        message.modify_ack_deadline(seconds)
    except Exception as exc:
        logger.warning("Failed to extend Pub/Sub ack deadline: {}", exc)


class ExpoPushSubscriber:
    def __init__(self, runner: DeliveryRunner, semaphore: asyncio.Semaphore) -> None:
        self.runner = runner
        self.semaphore = semaphore
        self._subscriber = None
        self._future = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def is_configured(self) -> bool:
        return bool(config.PROJECT_ID and config.NOTIFICATION_PUBSUB_TOPIC_NAME)

    def start(self) -> None:
        if pubsub_v1 is None:
            logger.warning("google-cloud-pubsub not installed; subscriber disabled")
            return
        if not self.is_configured:
            logger.warning("Pub/Sub subscriber not configured; skipping")
            return

        self._loop = asyncio.get_running_loop()
        subscription_path = (
            f"projects/{config.PROJECT_ID}/subscriptions/{config.NOTIFICATION_PUBSUB_TOPIC_NAME}"
        )
        self._subscriber = pubsub_v1.SubscriberClient()
        flow = FlowControl(max_messages=config.PUBSUB_MAX_MESSAGES)
        self._future = self._subscriber.subscribe(
            subscription_path,
            callback=self._on_message,
            flow_control=flow,
        )
        logger.info(
            "Pub/Sub subscriber started {} max_delivery_attempts={} ack_extension={}s",
            subscription_path,
            config.PUBSUB_MAX_DELIVERY_ATTEMPTS,
            config.PUBSUB_ACK_EXTENSION_SECONDS,
        )

    async def stop(self) -> None:
        if self._future is not None:
            self._future.cancel()
            self._future = None
        if self._subscriber is not None:
            try:
                self._subscriber.close()
            except Exception:
                pass
            self._subscriber = None

    def _ack_exhausted(
        self,
        message: Any,
        *,
        reason: str,
        attempt: Optional[int],
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Stop retry loops without a DLQ — ack and metric/log only."""
        push_metrics.ingress_disposition(HandleDisposition.ACK_EXHAUSTED.value)
        push_metrics.claim("exhausted_acked")
        logger.error(
            "Giving up on Pub/Sub message reason={} attempt={} payload={}",
            reason,
            attempt,
            payload,
        )
        message.ack()

    def _on_message(self, message) -> None:
        attempt = _delivery_attempt(message)
        raw = message.data.decode("utf-8") if message.data else ""
        try:
            payload = json.loads(raw) if raw else {}
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
            raw_id = payload.get("notification_id")
            if not raw_id:
                raise ValueError("notification_id missing")
            notification_id = UUID(str(raw_id))
        except Exception as exc:
            logger.error("Invalid Expo Pub/Sub payload: {}", exc)
            poison = {"raw": raw[:4000], "parse_error": str(exc)}
            self._ack_exhausted(
                message,
                reason="poison_payload",
                attempt=attempt if attempt is not None else 1,
                payload=poison,
            )
            push_metrics.claim("poison_acked")
            return

        loop = self._loop
        if loop is None:
            logger.error("Pub/Sub callback without event loop; nacking")
            message.nack()
            return

        async def _handle() -> None:
            async with self.semaphore:
                _extend_ack_deadline(message)
                result = await asyncio.to_thread(
                    self.runner.handle_notification_id,
                    notification_id,
                )
                if result.disposition.should_ack:
                    push_metrics.ingress_disposition(result.disposition.value)
                    message.ack()
                    return

                if _attempts_exhausted(attempt):
                    self._ack_exhausted(
                        message,
                        reason=result.detail or result.disposition.value,
                        attempt=attempt,
                        payload=payload,
                    )
                    return

                if attempt is None:
                    push_metrics.claim("nack_without_delivery_attempt")
                    logger.warning(
                        "Nacking notification {} without delivery_attempt "
                        "(set PUBSUB_MAX_DELIVERY_ATTEMPTS + enable delivery_attempt on subscription, "
                        "or rely on poller). detail={}",
                        notification_id,
                        result.detail,
                    )

                push_metrics.ingress_disposition(result.disposition.value)
                logger.warning(
                    "Nacking notification {} disposition={} detail={} attempt={}",
                    notification_id,
                    result.disposition.value,
                    result.detail,
                    attempt,
                )
                message.nack()

        asyncio.run_coroutine_threadsafe(_handle(), loop)
