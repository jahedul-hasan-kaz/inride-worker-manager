from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.constants.notification_constants import MessageDirection, NotificationType
from app.db.models import EmailLogInDB, LeadInDB, LeadUserFlagInDB, SMSLogInDB
from app.domain.models import DeliveryJob


def _parse_sent_by_user_id(sent_by: str | None) -> Optional[UUID]:
    if not sent_by or not str(sent_by).strip():
        return None
    try:
        return UUID(str(sent_by).strip())
    except (ValueError, TypeError):
        return None


def is_conversation_flagged_by_user(
    session: Session,
    *,
    tenant_id: UUID,
    thread_id: str | None,
    user_id: UUID,
) -> bool:
    if not thread_id:
        return False

    flagged = session.execute(
        select(
            exists(
                select(1)
                .select_from(LeadUserFlagInDB)
                .join(LeadInDB, LeadInDB.id == LeadUserFlagInDB.lead_id)
                .where(
                    and_(
                        LeadInDB.tenant_id == tenant_id,
                        LeadInDB.thread_id == thread_id,
                        LeadUserFlagInDB.user_id == user_id,
                    )
                )
            )
        )
    ).scalar()
    return bool(flagged)


def _resolve_email_lead_id(session: Session, job: DeliveryJob) -> Optional[UUID]:
    if job.email_log_id is None:
        return None
    row = (
        session.query(EmailLogInDB.lead_id)
        .filter(EmailLogInDB.id == job.email_log_id)
        .first()
    )
    if row is None or row[0] is None:
        return None
    return row[0]


def _last_outbound_sent_by_user_sms(
    session: Session,
    *,
    tenant_id: UUID,
    thread_id: str,
    user_id: UUID,
) -> bool:
    row = (
        session.query(SMSLogInDB.sent_by)
        .filter(
            SMSLogInDB.tenant_id == tenant_id,
            SMSLogInDB.thread_key == thread_id,
            SMSLogInDB.direction == MessageDirection.OUTBOUND.value,
        )
        .order_by(SMSLogInDB.created_at.desc(), SMSLogInDB.id.desc())
        .limit(1)
        .first()
    )
    if row is None:
        return False
    return _parse_sent_by_user_id(row[0]) == user_id


def _last_outbound_sent_by_user_email(
    session: Session,
    *,
    tenant_id: UUID,
    lead_id: UUID,
    user_id: UUID,
) -> bool:
    row = (
        session.query(EmailLogInDB.sent_by)
        .filter(
            EmailLogInDB.tenant_id == tenant_id,
            EmailLogInDB.lead_id == lead_id,
            EmailLogInDB.direction == MessageDirection.OUTBOUND.value,
        )
        .order_by(EmailLogInDB.created_at.desc())
        .limit(1)
        .first()
    )
    if row is None:
        return False
    return _parse_sent_by_user_id(row[0]) == user_id


def is_reply_to_user_manual_conversation(
    session: Session,
    job: DeliveryJob,
    user_id: UUID,
) -> bool:
    if (job.direction or "").lower() != MessageDirection.INBOUND.value:
        return False
    if job.tenant_id is None:
        return False

    notification_type = (job.notification_type or "").lower()
    if notification_type == NotificationType.SMS.value:
        if not job.thread_id:
            return False
        return _last_outbound_sent_by_user_sms(
            session,
            tenant_id=job.tenant_id,
            thread_id=job.thread_id,
            user_id=user_id,
        )

    if notification_type == NotificationType.EMAIL.value:
        lead_id = _resolve_email_lead_id(session, job)
        if lead_id is None:
            return False
        return _last_outbound_sent_by_user_email(
            session,
            tenant_id=job.tenant_id,
            lead_id=lead_id,
            user_id=user_id,
        )

    return False


def should_notify_user_for_conversation(
    session: Session,
    job: DeliveryJob,
    user_id: UUID,
) -> bool:
    if job.tenant_id is None:
        return False

    if is_conversation_flagged_by_user(
        session,
        tenant_id=job.tenant_id,
        thread_id=job.thread_id,
        user_id=user_id,
    ):
        return True

    return is_reply_to_user_manual_conversation(session, job, user_id)
