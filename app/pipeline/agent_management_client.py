from __future__ import annotations

from typing import List
from uuid import UUID

import requests
from loguru import logger

from app.core.config import config
from app.pipeline.push_types import BatchPushResponse, PushSendResult


class AgentManagementClient:
    """Sends pre-built push batches to agent-management (Expo only)."""

    def send_push_batch(self, items: List[dict]) -> BatchPushResponse:
        if not items:
            return BatchPushResponse()

        base_url = (config.AGENT_MANAGEMENT_BASE_URL or "").rstrip("/")
        if not base_url:
            logger.error("AGENT_MANAGEMENT_BASE_URL is not configured")
            return BatchPushResponse(failed_count=len(items))

        token = config.AGENT_MANAGEMENT_SERVICE_TOKEN
        if not token:
            logger.error("AGENT_MANAGEMENT_SERVICE_TOKEN is not configured")
            return BatchPushResponse(failed_count=len(items))

        url = f"{base_url}/api/notifications/push"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {"items": items}

        logger.info(
            "Calling agent push API url={} items={}",
            url,
            len(items),
        )

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception(
                "Agent push batch API failed items={} error={}",
                len(items),
                exc,
            )
            return BatchPushResponse(failed_count=len(items))

        try:
            body = response.json()
        except ValueError:
            logger.error("Agent push API returned non-JSON response")
            return BatchPushResponse(failed_count=len(items))

        data = body.get("data") or body
        sent_count = int(data.get("sent_count") or 0)
        failed_count = int(data.get("failed_count") or 0)
        logger.info(
            "Agent push API response sent={} failed={} http_status={}",
            sent_count,
            failed_count,
            response.status_code,
        )
        results: List[PushSendResult] = []

        for raw in data.get("results") or []:
            try:
                device_token_id = UUID(str(raw.get("device_token_id")))
            except (ValueError, TypeError):
                continue
            results.append(
                PushSendResult(
                    device_token_id=device_token_id,
                    status=str(raw.get("status") or "failed"),
                    deactivate=bool(raw.get("deactivate")),
                    error=raw.get("error"),
                )
            )

        if not results and items:
            failed_count = len(items)

        return BatchPushResponse(
            sent_count=sent_count,
            failed_count=failed_count,
            results=results,
        )
