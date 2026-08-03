from __future__ import annotations

from typing import List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import NotificationInDB
from app.domain.models import DeliveryJob, JobKind, PushStatus
from app.monitoring.metrics import push_metrics


def _row_to_job(row: NotificationInDB) -> DeliveryJob:
    return DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=row.id,
        tenant_id=row.tenant_id,
        actor_user_id=row.user_id,
        notification_type=(row.type or "").lower(),
        preview_text=row.preview_text,
        preview_title=row.preview_title,
        sender=row.from_,
        to=row.to,
        thread_id=row.thread_id,
        direction=row.direction,
        email_log_id=row.email_log_id,
        sms_log_id=int(row.sms_log_id) if row.sms_log_id is not None else None,
        source_notification_ids=[row.id],
    )


class NotificationClaimer:
    @staticmethod
    def get_push_status(session: Session, notification_id: UUID) -> Optional[str]:
        row = (
            session.query(NotificationInDB.push_status)
            .filter(NotificationInDB.id == notification_id)
            .first()
        )
        if row is None:
            return None
        return row[0]

    @staticmethod
    def claim_by_id(
        session: Session,
        notification_id: UUID,
    ) -> Tuple[Optional[DeliveryJob], Optional[str]]:
        """
        Returns (job, miss_status).
        - (job, None) on successful claim
        - (None, status) when row exists but was not pending (sent/failed/processing/…)
        - (None, None) when row does not exist
        """
        result = session.execute(
            text(
                """
                UPDATE public.notifications
                SET push_status = 'processing', updated_at = NOW(), push_error = NULL
                WHERE id = :id AND push_status = 'pending'
                RETURNING id
                """
            ),
            {"id": str(notification_id)},
        )
        claimed_id = result.scalar_one_or_none()
        if claimed_id is None:
            status = NotificationClaimer.get_push_status(session, notification_id)
            session.commit()
            if status is None:
                push_metrics.claim("miss_missing")
            elif status == PushStatus.PROCESSING.value:
                push_metrics.claim("miss_in_flight")
            elif status in (PushStatus.SENT.value, PushStatus.FAILED.value):
                push_metrics.claim("miss_done")
            else:
                push_metrics.claim("miss")
            return None, status

        session.commit()
        push_metrics.claim("won")
        row = session.query(NotificationInDB).filter(NotificationInDB.id == notification_id).one()
        return _row_to_job(row), None

    @staticmethod
    def claim_pending_batch(session: Session, limit: int) -> List[DeliveryJob]:
        result = session.execute(
            text(
                """
                WITH claimed AS (
                    SELECT id
                    FROM public.notifications
                    WHERE push_status = 'pending'
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT :limit
                )
                UPDATE public.notifications n
                SET push_status = 'processing', updated_at = NOW(), push_error = NULL
                FROM claimed
                WHERE n.id = claimed.id
                RETURNING n.id
                """
            ),
            {"limit": limit},
        )
        ids = [row[0] for row in result.fetchall()]
        session.commit()
        if not ids:
            logger.debug("Claim batch: no pending notifications")
            return []

        logger.info("Claimed {} pending notification(s): {}", len(ids), [str(i) for i in ids])
        push_metrics.claim("won", amount=len(ids))

        rows = (
            session.query(NotificationInDB)
            .filter(NotificationInDB.id.in_(ids))
            .order_by(NotificationInDB.created_at.asc())
            .all()
        )
        return [_row_to_job(row) for row in rows]
