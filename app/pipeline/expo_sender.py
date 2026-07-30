from __future__ import annotations

import time
from typing import Any, Optional

from loguru import logger
from requests import Session as RequestsSession
from requests.exceptions import ConnectionError, HTTPError

from app.core.config import config
from app.domain.models import DeliveryJob, DeliveryTarget

try:
    from exponent_server_sdk import (
        DeviceNotRegisteredError,
        PushClient,
        PushMessage,
    )
except ImportError:  # pragma: no cover
    DeviceNotRegisteredError = None  # type: ignore
    PushClient = None
    PushMessage = None
    logger.warning("exponent-server-sdk unavailable; Expo sends disabled")


def _truncate(text: Optional[str], limit: int = 180) -> Optional[str]:
    value = (text or "").strip()
    if not value:
        return None
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def _title(job: DeliveryJob) -> str:
    sender = job.sender or "unknown sender"
    if job.notification_type == "sms":
        return f"New SMS from {sender}"
    if job.notification_type == "email":
        return f"New Email from {sender}"
    return f"New Notification from {sender}"


class ExpoSender:
    def __init__(self) -> None:
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if PushClient is None or PushMessage is None:
            return None
        if self._client is not None:
            return self._client

        token = config.EXPO_ACCESS_TOKEN_KEY
        if not token:
            self._client = PushClient()
            return self._client

        session = RequestsSession()
        session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "accept": "application/json",
                "accept-encoding": "gzip, deflate",
                "content-type": "application/json",
            }
        )
        self._client = PushClient(session=session)
        return self._client

    def send(self, job: DeliveryJob, target: DeliveryTarget) -> tuple[bool, Optional[str], bool]:
        """Returns (ok, error_message, deactivate_token)."""
        client = self._ensure_client()
        if client is None or PushMessage is None:
            return False, "expo_sdk_unavailable", False

        data = {
            "notificationId": str(job.notification_id),
            "type": job.notification_type,
            "sender": job.sender,
            "to": job.to,
            "tenantId": str(job.tenant_id) if job.tenant_id else None,
            "userId": str(job.actor_user_id) if job.actor_user_id else None,
            "previewText": job.preview_text,
            "threadId": job.thread_id,
            "emailLogId": str(job.email_log_id) if job.email_log_id else None,
        }
        message = PushMessage(
            to=target.push_token,
            title=_title(job),
            body=_truncate(job.preview_text),
            data={k: v for k, v in data.items() if v is not None},
            sound="default",
        )

        started = time.perf_counter()
        try:
            response = client.publish(message)
            response.validate_response()
            return True, None, False
        except (ConnectionError, HTTPError) as exc:
            logger.warning(
                "Expo network error notification={} device={}: {}",
                job.notification_id,
                target.device_token_id,
                exc,
            )
            return False, str(exc), False
        except Exception as exc:
            deactivate = False
            if DeviceNotRegisteredError is not None and isinstance(exc, DeviceNotRegisteredError):
                deactivate = True
            else:
                err = str(exc).lower()
                deactivate = (
                    "devicenotregistered" in err
                    or "not a registered push notification recipient" in err
                )
            logger.warning(
                "Expo send failed notification={} device={}: {}",
                job.notification_id,
                target.device_token_id,
                exc,
            )
            return False, str(exc), deactivate
        finally:
            _ = time.perf_counter() - started
