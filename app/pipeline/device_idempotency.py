from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import NotificationPushDeliveryInDB
from app.domain.models import DeliveryJob, DeliveryTarget


class DeviceIdempotency:
    """Claim per (ledger_notification_id, device_token_id) before Expo send.

    Any existing ledger row (sending or sent) blocks another Expo call.
    ``sending`` is never deleted by reclaim — only sealed to ``sent`` or
    released on explicit Expo failure in this process.
    """

    @staticmethod
    def try_claim(session: Session, job: DeliveryJob, target: DeliveryTarget) -> bool:
        stmt = (
            insert(NotificationPushDeliveryInDB)
            .values(
                id=uuid4(),
                notification_id=job.ledger_notification_id,
                device_token_id=target.device_token_id,
                status="sending",
            )
            .on_conflict_do_nothing(
                constraint="uq_notification_push_deliveries_notif_device",
            )
            .returning(NotificationPushDeliveryInDB.id)
        )
        result = session.execute(stmt)
        row = result.first()
        session.commit()
        return row is not None

    @staticmethod
    def mark_sent(session: Session, job: DeliveryJob, device_token_id: UUID) -> None:
        (
            session.query(NotificationPushDeliveryInDB)
            .filter(
                NotificationPushDeliveryInDB.notification_id == job.ledger_notification_id,
                NotificationPushDeliveryInDB.device_token_id == device_token_id,
            )
            .update(
                {
                    NotificationPushDeliveryInDB.status: "sent",
                    NotificationPushDeliveryInDB.updated_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
        )
        session.commit()

    @staticmethod
    def release_claim(session: Session, job: DeliveryJob, device_token_id: UUID) -> None:
        """Expo failed in this process — allow a later retry for this device."""
        (
            session.query(NotificationPushDeliveryInDB)
            .filter(
                NotificationPushDeliveryInDB.notification_id == job.ledger_notification_id,
                NotificationPushDeliveryInDB.device_token_id == device_token_id,
                NotificationPushDeliveryInDB.status == "sending",
            )
            .delete(synchronize_session=False)
        )
        session.commit()

    @staticmethod
    def seal_open_sending(session: Session, job: DeliveryJob) -> int:
        """Promote leftover ``sending`` → ``sent`` (no second Expo)."""
        updated = (
            session.query(NotificationPushDeliveryInDB)
            .filter(
                NotificationPushDeliveryInDB.notification_id == job.ledger_notification_id,
                NotificationPushDeliveryInDB.status == "sending",
            )
            .update(
                {
                    NotificationPushDeliveryInDB.status: "sent",
                    NotificationPushDeliveryInDB.updated_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
        )
        session.commit()
        return int(updated or 0)

    @staticmethod
    def has_any_sent(session: Session, job: DeliveryJob) -> bool:
        return (
            session.query(NotificationPushDeliveryInDB.id)
            .filter(
                NotificationPushDeliveryInDB.notification_id == job.ledger_notification_id,
                NotificationPushDeliveryInDB.status == "sent",
            )
            .first()
            is not None
        )
