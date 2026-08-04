from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.constants.notification_constants import MessageDirection, NotificationType
from app.db.models import EmailLogInDB, LeadInDB, LeadUserFlagInDB, SMSLogInDB
from app.domain.models import DeliveryJob
from app.eligibility.context import EffectiveConfig


@dataclass(frozen=True)
class ConversationMatchContext:
    """Cached conversation match for one notification job."""

    flagged_user_ids: Set[UUID]
    manual_user_id: Optional[UUID]


def _parse_sent_by_user_id(sent_by: str | None) -> Optional[UUID]:
    if not sent_by or not str(sent_by).strip():
        return None
    try:
        return UUID(str(sent_by).strip())
    except (ValueError, TypeError):
        return None


def _parse_thread_root_id(thread_id: str | None) -> Optional[UUID]:
    if not thread_id or not str(thread_id).strip():
        return None
    try:
        return UUID(str(thread_id).strip())
    except (ValueError, TypeError):
        return None


def resolve_lead_ids_for_notification(
    session: Session,
    job: DeliveryJob,
) -> Set[UUID]:
    """Resolve all lead ids tied to a notification (sms_log.lead_id and leads.thread_id)."""
    lead_ids: Set[UUID] = set()
    if job.tenant_id is None:
        return lead_ids

    if job.sms_log_id is not None:
        row = (
            session.query(SMSLogInDB.lead_id)
            .filter(
                SMSLogInDB.id == job.sms_log_id,
                SMSLogInDB.tenant_id == job.tenant_id,
            )
            .first()
        )
        if row is not None and row[0] is not None:
            lead_ids.add(row[0])

    if job.thread_id:
        rows = (
            session.query(LeadInDB.id)
            .filter(
                LeadInDB.tenant_id == job.tenant_id,
                LeadInDB.thread_id == job.thread_id,
            )
            .all()
        )
        lead_ids.update(row[0] for row in rows)

    return lead_ids


def resolve_lead_id_for_notification(
    session: Session,
    job: DeliveryJob,
) -> Optional[UUID]:
    lead_ids = resolve_lead_ids_for_notification(session, job)
    if not lead_ids:
        return None
    return next(iter(lead_ids))


def get_flagged_user_ids_for_notification(
    session: Session,
    job: DeliveryJob,
) -> Set[UUID]:
    """Return user_ids from lead_user_flags for any lead tied to the notification."""
    lead_ids = resolve_lead_ids_for_notification(session, job)
    if not lead_ids:
        return set()

    rows = (
        session.query(LeadUserFlagInDB.user_id)
        .filter(LeadUserFlagInDB.lead_id.in_(lead_ids))
        .all()
    )
    return {row[0] for row in rows}


def is_conversation_flagged_by_user(
    session: Session,
    job: DeliveryJob,
    user_id: UUID,
) -> bool:
    return user_id in get_flagged_user_ids_for_notification(session, job)


def _last_outbound_sender_user_id_sms(
    session: Session,
    *,
    tenant_id: UUID,
    thread_id: str,
) -> Optional[UUID]:
    """Latest outbound in the thread; sent_by must be a user UUID (not automated/agent)."""
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
        return None
    return _parse_sent_by_user_id(row[0])


def _previous_outbound_sender_user_id_email(
    session: Session,
    *,
    tenant_id: UUID,
    root_id: UUID,
    current_email_id: UUID,
) -> Optional[UUID]:
    """Return sender_user_id on the outbound email immediately before the current one in the thread."""
    current = (
        session.query(EmailLogInDB.created_at)
        .filter(
            EmailLogInDB.id == current_email_id,
            EmailLogInDB.tenant_id == tenant_id,
        )
        .first()
    )
    if current is None or current[0] is None:
        return None

    row = session.execute(
        text(
            """
            SELECT el.sender_user_id
            FROM public.email_log el
            WHERE el.tenant_id = :tenant_id
              AND public.get_email_root_id(
                    el.id,
                    el.in_reply_to_message_id,
                    el.tenant_id
                  ) = :root_id
              AND el.direction = :outbound
              AND el.id != :current_email_id
              AND el.created_at < :current_created_at
            ORDER BY el.created_at DESC, el.id DESC
            LIMIT 1
            """
        ),
        {
            "tenant_id": tenant_id,
            "root_id": root_id,
            "outbound": MessageDirection.OUTBOUND.value,
            "current_email_id": current_email_id,
            "current_created_at": current[0],
        },
    ).first()
    if row is None or row[0] is None:
        return None
    sender_user_id = row[0]
    return sender_user_id if isinstance(sender_user_id, UUID) else UUID(str(sender_user_id))


def get_manual_reply_user_id(
    session: Session,
    job: DeliveryJob,
) -> Optional[UUID]:
    if (job.direction or "").lower() != MessageDirection.INBOUND.value:
        return None
    if job.tenant_id is None:
        return None

    notification_type = (job.notification_type or "").lower()
    if notification_type == NotificationType.SMS.value:
        if not job.thread_id:
            return None
        return _last_outbound_sender_user_id_sms(
            session,
            tenant_id=job.tenant_id,
            thread_id=job.thread_id,
        )

    if notification_type == NotificationType.EMAIL.value:
        root_id = _parse_thread_root_id(job.thread_id)
        if root_id is None or job.email_log_id is None:
            return None
        return _previous_outbound_sender_user_id_email(
            session,
            tenant_id=job.tenant_id,
            root_id=root_id,
            current_email_id=job.email_log_id,
        )

    return None


def _manual_reply_enabled_for_job(config: EffectiveConfig, job: DeliveryJob) -> bool:
    notification_type = (job.notification_type or "").lower()
    if notification_type == NotificationType.SMS.value:
        return config.is_manual_sms
    if notification_type == NotificationType.EMAIL.value:
        return config.is_manual_email
    return False


def channel_enabled_for_job(config: EffectiveConfig, job: DeliveryJob) -> bool:
    notification_type = (job.notification_type or "").lower()
    if notification_type == NotificationType.SMS.value:
        return config.is_sms_enable
    if notification_type == NotificationType.EMAIL.value:
        return config.is_email_enable
    return True


def conversation_paths_enabled(config: EffectiveConfig, job: DeliveryJob) -> bool:
    notification_type = (job.notification_type or "").lower()
    if notification_type == NotificationType.EMAIL.value:
        return config.is_manual_email
    if notification_type == NotificationType.SMS.value:
        return config.is_in_flagged or config.is_manual_sms
    return False


def load_conversation_match_context(
    session: Session,
    job: DeliveryJob,
) -> ConversationMatchContext:
    flagged_user_ids, manual_user_id = get_conversation_match_summary(session, job)
    return ConversationMatchContext(
        flagged_user_ids=flagged_user_ids,
        manual_user_id=manual_user_id,
    )


def build_candidate_reasons_from_match(
    match: ConversationMatchContext,
) -> Dict[UUID, List[str]]:
    reasons_by_user: Dict[UUID, List[str]] = {}
    for user_id in match.flagged_user_ids:
        reasons_by_user.setdefault(user_id, []).append("flagged")
    if match.manual_user_id is not None:
        reasons_by_user.setdefault(match.manual_user_id, []).append("manual_reply")
    return reasons_by_user


def get_user_include_reasons_cached(
    job: DeliveryJob,
    user_id: UUID,
    config: EffectiveConfig,
    match: ConversationMatchContext,
) -> List[str]:
    """Why this user is eligible, using pre-fetched conversation match."""
    reasons: List[str] = []
    notification_type = (job.notification_type or "").lower()

    if notification_type != NotificationType.EMAIL.value:
        if config.is_in_flagged and user_id in match.flagged_user_ids:
            reasons.append("flagged")

    if (
        match.manual_user_id is not None
        and match.manual_user_id == user_id
        and _manual_reply_enabled_for_job(config, job)
    ):
        reasons.append("manual_reply")

    return reasons


def get_user_include_reasons(
    session: Session,
    job: DeliveryJob,
    user_id: UUID,
    config: EffectiveConfig,
) -> List[str]:
    """Why this user is eligible, respecting their notification_config preferences."""
    match = load_conversation_match_context(session, job)
    return get_user_include_reasons_cached(job, user_id, config, match)


def get_conversation_match_summary(
    session: Session,
    job: DeliveryJob,
) -> Tuple[Set[UUID], Optional[UUID]]:
    """Raw conversation matches before per-user notification_config is applied."""
    notification_type = (job.notification_type or "").lower()
    flagged_user_ids: Set[UUID] = set()
    if notification_type != NotificationType.EMAIL.value:
        flagged_user_ids = get_flagged_user_ids_for_notification(session, job)
    return flagged_user_ids, get_manual_reply_user_id(session, job)


def get_conversation_candidate_reasons(
    session: Session,
    job: DeliveryJob,
) -> Dict[UUID, List[str]]:
    """Potential conversation matches used to discover device tokens (pre-config)."""
    return build_candidate_reasons_from_match(load_conversation_match_context(session, job))


def get_conversation_candidate_user_ids(
    session: Session,
    job: DeliveryJob,
) -> Set[UUID]:
    """Users who may receive a push based on conversation rules (before config gates)."""
    return set(get_conversation_candidate_reasons(session, job).keys())


def is_reply_to_user_manual_conversation(
    session: Session,
    job: DeliveryJob,
    user_id: UUID,
) -> bool:
    manual_user_id = get_manual_reply_user_id(session, job)
    return manual_user_id is not None and manual_user_id == user_id


def evaluate_conversation_eligibility_cached(
    session: Session,
    job: DeliveryJob,
    user_id: UUID,
    config: EffectiveConfig,
    match: ConversationMatchContext,
) -> Tuple[bool, Optional[str], List[str]]:
    if job.tenant_id is None:
        return False, "missing_tenant_id", []

    include_reasons = get_user_include_reasons_cached(job, user_id, config, match)
    if include_reasons:
        return True, None, include_reasons

    notification_type = (job.notification_type or "").lower()
    flagged_user_ids = (
        set()
        if notification_type == NotificationType.EMAIL.value
        else set(match.flagged_user_ids)
    )
    manual_user_id = match.manual_user_id
    manual_enabled = _manual_reply_enabled_for_job(config, job)

    if notification_type == NotificationType.EMAIL.value:
        if not job.thread_id:
            return False, "email_missing_thread_id", []
        if job.email_log_id is None:
            return False, "email_missing_email_log_id", []
        if (job.direction or "").lower() != MessageDirection.INBOUND.value:
            return False, f"email_not_inbound:direction={job.direction!r}", []
        if manual_user_id is None:
            return False, "email_no_prior_manual_outbound_sender_user_id", []
        if not manual_enabled:
            return False, "manual_reply_disabled_in_config", []
        return False, f"email_manual_reply_user_mismatch:expected={manual_user_id}", []

    lead_ids = resolve_lead_ids_for_notification(session, job)
    if not lead_ids:
        return False, (
            f"lead_not_found:thread_id={job.thread_id!r},sms_log_id={job.sms_log_id}"
        ), []

    if manual_user_id == user_id and not manual_enabled:
        return False, "manual_reply_disabled_in_config", []

    if user_id in flagged_user_ids and not config.is_in_flagged:
        return False, "flagged_disabled_in_config", []

    if (job.direction or "").lower() != MessageDirection.INBOUND.value:
        return False, (
            f"sms_conversation_not_eligible:"
            f"lead_ids={[str(lead_id) for lead_id in sorted(lead_ids, key=str)]},"
            f"flagged_users={[str(uid) for uid in sorted(flagged_user_ids, key=str)]},"
            f"direction={job.direction!r},"
            f"manual_reply_user={manual_user_id}"
        ), []

    return False, (
        f"sms_conversation_not_eligible:"
        f"lead_ids={[str(lead_id) for lead_id in sorted(lead_ids, key=str)]},"
        f"flagged_users={[str(uid) for uid in sorted(flagged_user_ids, key=str)]},"
        f"direction={job.direction!r},"
        f"manual_reply_user={manual_user_id}"
    ), []


def evaluate_conversation_eligibility(
    session: Session,
    job: DeliveryJob,
    user_id: UUID,
    config: EffectiveConfig,
) -> Tuple[bool, Optional[str], List[str]]:
    match = load_conversation_match_context(session, job)
    return evaluate_conversation_eligibility_cached(
        session, job, user_id, config, match
    )


def should_notify_user_for_conversation(
    session: Session,
    job: DeliveryJob,
    user_id: UUID,
    config: EffectiveConfig,
) -> bool:
    passed, _reason, _include_reasons = evaluate_conversation_eligibility(
        session, job, user_id, config
    )
    return passed
