from __future__ import annotations

from typing import List
from uuid import UUID

import requests
from loguru import logger

from app.core.config import config
from app.domain.models import DeliveryJob, DeliveryTarget


class AgentManagementClient:
    """Calls agent-management send-push API instead of Expo directly."""

    def send_push(
        self,
        job: DeliveryJob,
        targets: List[DeliveryTarget],
    ) -> tuple[bool, str | None, list[UUID]]:
        if not targets:
            return True, None, []

        base_url = (config.AGENT_MANAGEMENT_BASE_URL or "").rstrip("/")
        if not base_url:
            logger.error("AGENT_MANAGEMENT_BASE_URL is not configured")
            return False, "agent_management_base_url_missing", []

        token = config.AGENT_MANAGEMENT_SERVICE_TOKEN
        if not token:
            logger.error("AGENT_MANAGEMENT_SERVICE_TOKEN is not configured")
            return False, "agent_management_service_token_missing", []

        url = f"{base_url}/api/notifications/{job.notification_id}/push"
        device_token_ids = [str(target.device_token_id) for target in targets]
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {"device_token_ids": device_token_ids}

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception(
                "Agent push API failed for notification {}: {}",
                job.notification_id,
                exc,
            )
            return False, str(exc), []

        try:
            body = response.json()
        except ValueError:
            return True, None, []

        data = body.get("data") or body
        sent_count = int(data.get("sent_count") or 0)
        deactivated_raw = data.get("deactivated_token_ids") or []
        deactivated_ids: list[UUID] = []
        for raw_id in deactivated_raw:
            try:
                deactivated_ids.append(UUID(str(raw_id)))
            except (ValueError, TypeError):
                continue

        if sent_count <= 0:
            return False, "agent_push_no_devices_sent", deactivated_ids

        return True, None, deactivated_ids

    def send(self, job: DeliveryJob, target: DeliveryTarget) -> tuple[bool, str | None, bool]:
        """Compatibility shim for per-device runner loop — batches via single-target call."""
        ok, error, deactivated = self.send_push(job, [target])
        deactivate = target.device_token_id in deactivated
        return ok, error, deactivate
