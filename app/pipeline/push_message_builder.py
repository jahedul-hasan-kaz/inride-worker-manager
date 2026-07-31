from __future__ import annotations

from typing import Any, Dict, Optional

from app.domain.models import DeliveryJob, DeliveryTarget


def _truncate(text: Optional[str], limit: int = 180) -> Optional[str]:
    value = (text or "").strip()
    if not value:
        return None
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def build_title(job: DeliveryJob) -> str:
    if job.preview_title:
        return job.preview_title
    sender = job.sender or "unknown sender"
    if job.notification_type == "sms":
        return f"New SMS from {sender}"
    if job.notification_type == "email":
        return f"New Email from {sender}"
    return f"New Notification from {sender}"


def build_data(job: DeliveryJob) -> Dict[str, Any]:
    return {
        key: value
        for key, value in {
            "notificationId": str(job.notification_id),
            "type": job.notification_type,
            "sender": job.sender,
            "to": job.to,
            "tenantId": str(job.tenant_id) if job.tenant_id else None,
            "userId": str(job.actor_user_id) if job.actor_user_id else None,
            "previewText": job.preview_text,
            "threadId": job.thread_id,
            "emailLogId": str(job.email_log_id) if job.email_log_id else None,
            "smsLogId": str(job.sms_log_id) if job.sms_log_id is not None else None,
            "direction": job.direction,
        }.items()
        if value is not None
    }


def build_push_item(job: DeliveryJob, target: DeliveryTarget) -> dict:
    """Build one Expo payload for the agent batch send API."""
    return {
        "device_token_id": str(target.device_token_id),
        "push_token": target.push_token,
        "title": build_title(job),
        "body": _truncate(job.preview_text),
        "data": build_data(job),
    }
