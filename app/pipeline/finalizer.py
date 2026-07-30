from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import DeviceTokenInDB, NotificationInDB
from app.domain.models import DeliveryJob, PushStatus
from app.monitoring.metrics import push_metrics


class NotificationFinalizer:
    @staticmethod
    def finalize(
        session: Session,
        job: DeliveryJob,
        *,
        status: PushStatus,
        error: Optional[str] = None,
    ) -> None:
        (
            session.query(NotificationInDB)
            .filter(NotificationInDB.id == job.notification_id)
            .update(
                {
                    NotificationInDB.push_status: status.value,
                    NotificationInDB.push_error: (error or None)[:2000] if error else None,
                    NotificationInDB.updated_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
        )
        session.commit()
        push_metrics.notification_finalized(status.value)

    @staticmethod
    def deactivate_tokens(session: Session, token_ids: List[UUID]) -> None:
        if not token_ids:
            return
        (
            session.query(DeviceTokenInDB)
            .filter(DeviceTokenInDB.id.in_(token_ids), DeviceTokenInDB.is_active.is_(True))
            .update(
                {
                    DeviceTokenInDB.is_active: False,
                },
                synchronize_session=False,
            )
        )
        session.commit()
