from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

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


def build_push_item(
    job: DeliveryJob,
    target: DeliveryTarget,
    *,
    ttl_sec: Optional[int] = None,
) -> dict:
    """Build one Expo payload for the agent batch send API."""
    return build_digest_push_item(job, target, [job], ttl_sec=ttl_sec)


def build_digest_push_item(
    primary_job: DeliveryJob,
    target: DeliveryTarget,
    jobs: List[DeliveryJob],
    *,
    ttl_sec: Optional[int] = None,
) -> dict:
    count = len(jobs)
    latest = jobs[-1]
    title = build_digest_title(latest, count)
    body = build_digest_body(jobs)
    data = build_data(latest)
    if count > 1:
        data["notificationIds"] = [str(job.notification_id) for job in jobs]
        data["aggregatedCount"] = count
    return {
        "device_token_id": str(target.device_token_id),
        "push_token": target.push_token,
        "notification_id": str(latest.notification_id),
        "ttl": ttl_sec,
        "title": title,
        "body": body,
        "data": data,
    }


def _format_since_time(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _earliest_created_at(jobs: List[DeliveryJob]) -> Optional[datetime]:
    timestamps = [job.created_at for job in jobs if job.created_at is not None]
    if not timestamps:
        return None
    return min(timestamps)


def build_digest_title(job: DeliveryJob, count: int) -> str:
    del count  # Aggregated digests keep the same title as a single push.
    return build_title(job)


def build_digest_body(jobs: List[DeliveryJob]) -> Optional[str]:
    if len(jobs) == 1:
        return _truncate(jobs[0].preview_text)
    # since = _earliest_created_at(jobs)
    # since_label = _format_since_time(since) if since is not None else "recently"
    count = len(jobs)
    noun = "notification" if count == 1 else "notifications"
    return f"{count} new {noun}"
