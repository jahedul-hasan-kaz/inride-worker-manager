from __future__ import annotations

from uuid import UUID

from loguru import logger
from sqlalchemy import and_, case, exists, select
from sqlalchemy.orm import Session, aliased

from app.constants.notification_constants import MessageDirection, NotificationType, PlatformOs
from app.db.models import EmailLogInDB, LeadInDB, LeadUserFlagInDB, SMSLogInDB, TenantInDB
from app.eligibility.context import EligibilityContext, StepOutcome, StepResult

PLATFORM_ADMIN = "platform_admin"


def step_master_enable(ctx: EligibilityContext) -> StepResult:
    if not ctx.config.is_enable:
        return StepResult(outcome=StepOutcome.SKIP_USER, reason="notifications_disabled")
    return StepResult(outcome=StepOutcome.CONTINUE)


def step_tenant_scope(ctx: EligibilityContext) -> StepResult:
    if ctx.config.is_all_tenants:
        return StepResult(outcome=StepOutcome.CONTINUE)

    logger.debug(
        "Per-tenant notification config not implemented for user {} tenant {}; skipping",
        ctx.user_id,
        ctx.job.tenant_id,
    )
    return StepResult(outcome=StepOutcome.SKIP_USER, reason="tenant_config_not_implemented")


def step_channel_enable(ctx: EligibilityContext) -> StepResult:
    notification_type = ctx.notification_type
    if notification_type == NotificationType.SMS.value:
        if not ctx.config.is_sms_enable:
            return StepResult(outcome=StepOutcome.SKIP_USER, reason="sms_disabled")
    elif notification_type == NotificationType.EMAIL.value:
        if not ctx.config.is_email_enable:
            return StepResult(outcome=StepOutcome.SKIP_USER, reason="email_disabled")
    return StepResult(outcome=StepOutcome.CONTINUE)


def _parse_uuid(value: str | None) -> bool:
    if not value or not str(value).strip():
        return False
    try:
        UUID(str(value).strip())
        return True
    except (ValueError, TypeError):
        return False


def _has_prior_manual_outbound_sms(session: Session, job) -> bool:
    if not job.thread_id or job.tenant_id is None:
        return False
    rows = (
        session.query(SMSLogInDB.sent_by)
        .filter(
            SMSLogInDB.tenant_id == job.tenant_id,
            SMSLogInDB.thread_key == job.thread_id,
            SMSLogInDB.direction == MessageDirection.OUTBOUND.value,
            SMSLogInDB.sent_by.isnot(None),
        )
        .limit(20)
        .all()
    )
    return any(_parse_uuid(row[0]) for row in rows)


def _has_prior_manual_outbound_email(session: Session, job) -> bool:
    if job.tenant_id is None:
        return False

    lead_id = None
    if job.email_log_id is not None:
        current = (
            session.query(EmailLogInDB.lead_id)
            .filter(EmailLogInDB.id == job.email_log_id)
            .first()
        )
        if current:
            lead_id = current[0]

    query = session.query(EmailLogInDB.sent_by).filter(
        EmailLogInDB.tenant_id == job.tenant_id,
        EmailLogInDB.direction == MessageDirection.OUTBOUND.value,
        EmailLogInDB.sent_by.isnot(None),
    )
    if job.email_log_id is not None:
        query = query.filter(EmailLogInDB.id != job.email_log_id)
    if lead_id is not None:
        query = query.filter(EmailLogInDB.lead_id == lead_id)
    elif job.thread_id:
        return False

    rows = query.limit(20).all()
    return any(_parse_uuid(row[0]) for row in rows)


def step_manual_reply(ctx: EligibilityContext) -> StepResult:
    notification_type = ctx.notification_type
    manual_enabled = False
    if notification_type == NotificationType.SMS.value:
        manual_enabled = ctx.config.is_manual_sms
    elif notification_type == NotificationType.EMAIL.value:
        manual_enabled = ctx.config.is_manual_email

    if not manual_enabled:
        return StepResult(outcome=StepOutcome.CONTINUE)

    if ctx.direction != MessageDirection.INBOUND.value:
        return StepResult(outcome=StepOutcome.SKIP_USER, reason="not_inbound_for_manual_gate")

    session: Session = ctx.session
    if session is None:
        return StepResult(outcome=StepOutcome.SKIP_USER, reason="missing_session")

    has_prior = False
    if notification_type == NotificationType.SMS.value:
        has_prior = _has_prior_manual_outbound_sms(session, ctx.job)
    elif notification_type == NotificationType.EMAIL.value:
        has_prior = _has_prior_manual_outbound_email(session, ctx.job)

    if not has_prior:
        return StepResult(outcome=StepOutcome.SKIP_USER, reason="no_prior_manual_outbound")

    return StepResult(outcome=StepOutcome.CONTINUE)


def is_conversation_flagged(
    session: Session,
    *,
    tenant_id: UUID,
    thread_id: str | None,
    user_id: UUID,
    user_role: str,
) -> bool:
    if not thread_id:
        return False

    lead_alias = aliased(LeadInDB)
    tenant_alias = aliased(TenantInDB)
    use_dealership_flags = user_role != PLATFORM_ADMIN

    global_exists = exists(
        select(1).where(LeadUserFlagInDB.lead_id == lead_alias.id)
    )
    user_exists = exists(
        select(1).where(
            and_(
                LeadUserFlagInDB.lead_id == lead_alias.id,
                LeadUserFlagInDB.user_id == user_id,
            )
        )
    )
    if use_dealership_flags:
        flagged_expr = case(
            (tenant_alias.flag_level == "dealership", global_exists),
            else_=user_exists,
        )
    else:
        flagged_expr = user_exists

    stmt = (
        select(flagged_expr)
        .select_from(lead_alias)
        .join(tenant_alias, tenant_alias.id == lead_alias.tenant_id)
        .where(
            lead_alias.tenant_id == tenant_id,
            lead_alias.thread_id == thread_id,
        )
        .limit(1)
    )
    result = session.execute(stmt).scalar()
    return bool(result)


def step_flagged(ctx: EligibilityContext) -> StepResult:
    if not ctx.config.is_in_flagged:
        return StepResult(outcome=StepOutcome.CONTINUE)

    session: Session = ctx.session
    if session is None or ctx.job.tenant_id is None:
        return StepResult(outcome=StepOutcome.SKIP_USER, reason="missing_session_or_tenant")

    flagged = is_conversation_flagged(
        session,
        tenant_id=ctx.job.tenant_id,
        thread_id=ctx.job.thread_id,
        user_id=ctx.user_id,
        user_role=ctx.user_role,
    )
    if not flagged:
        return StepResult(outcome=StepOutcome.SKIP_USER, reason="conversation_not_flagged")

    return StepResult(outcome=StepOutcome.CONTINUE)


def _device_matches_platform(device_platform: str | None, config_platform: str) -> bool:
    if config_platform == PlatformOs.BOTH.value:
        return True
    if not device_platform:
        return True
    return device_platform.lower() == config_platform


def step_platform_filter(ctx: EligibilityContext) -> StepResult:
    config_platform = (ctx.config.platform_os or PlatformOs.BOTH.value).lower()
    ctx.eligible_devices = [
        device
        for device in ctx.devices
        if _device_matches_platform(getattr(device, "platform", None), config_platform)
    ]
    if not ctx.eligible_devices:
        return StepResult(outcome=StepOutcome.SKIP_USER, reason="no_matching_platform_devices")
    return StepResult(outcome=StepOutcome.CONTINUE)
