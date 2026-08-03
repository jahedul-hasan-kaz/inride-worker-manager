from __future__ import annotations

import json
from typing import Any, Dict, List
from uuid import UUID

from loguru import logger

from app.pipeline.push_types import BatchPushResponse, PushSendResult
from app.services.gcp.pubsub import notification_pubsub


def build_pubsub_push_message(
    *,
    push_token: str,
    title: str,
    body: str | None,
    data: Dict[str, Any],
) -> dict:
    """Match ai-agent-management notification_push_service._publish_push_message payload."""
    return {
        "to": push_token,
        "medium": "push_notification",
        "args": {
            "title": title,
            "body": body,
            "data": data,
            "sound": "default",
        },
    }


class NotificationPubSubClient:
    """Publishes pre-built push messages to the notification Pub/Sub topic."""

    def send_push_batch(self, items: List[dict]) -> BatchPushResponse:
        if not items:
            return BatchPushResponse()

        publisher = notification_pubsub.publisher
        if publisher is None:
            logger.error("Notification Pub/Sub publisher is not configured")
            return BatchPushResponse(failed_count=len(items))

        sent_count = 0
        failed_count = 0
        results: List[PushSendResult] = []

        for item in items:
            try:
                device_token_id = UUID(str(item["device_token_id"]))
            except (KeyError, ValueError, TypeError):
                failed_count += 1
                continue

            push_token = (item.get("push_token") or "").strip()
            if not push_token:
                failed_count += 1
                results.append(
                    PushSendResult(
                        device_token_id=device_token_id,
                        status="failed",
                        error="missing_push_token",
                    )
                )
                continue

            message = build_pubsub_push_message(
                push_token=push_token,
                title=item.get("title") or "",
                body=item.get("body"),
                data=item.get("data") or {},
            )

            try:
                publisher.publish_message(json.dumps(message))
                sent_count += 1
                results.append(
                    PushSendResult(
                        device_token_id=device_token_id,
                        status="sent",
                    )
                )
                logger.info(
                    "Published push_notification device_token_id={} token={}",
                    device_token_id,
                    push_token,
                )
            except Exception as exc:
                failed_count += 1
                results.append(
                    PushSendResult(
                        device_token_id=device_token_id,
                        status="failed",
                        error=str(exc),
                    )
                )
                logger.exception(
                    "Failed to publish push_notification device_token_id={}: {}",
                    device_token_id,
                    exc,
                )

        logger.info(
            "Notification Pub/Sub batch result sent={} failed={}",
            sent_count,
            failed_count,
        )
        return BatchPushResponse(
            sent_count=sent_count,
            failed_count=failed_count,
            results=results,
        )
