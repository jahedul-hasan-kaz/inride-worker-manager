from __future__ import annotations

from app.constants.notification_constants import NotificationType, PlatformOs
from app.eligibility.context import EligibilityContext, StepOutcome, StepResult
from app.eligibility.conversation_eligibility import should_notify_user_for_conversation
from sqlalchemy.orm import Session


def step_master_enable(ctx: EligibilityContext) -> StepResult:
    if not ctx.config.is_enable:
        return StepResult(outcome=StepOutcome.SKIP_USER, reason="notifications_disabled")
    return StepResult(outcome=StepOutcome.CONTINUE)


# def step_tenant_scope(ctx: EligibilityContext) -> StepResult:
#     """Hierarchy — disabled: per-tenant notification_tenants_config overrides."""
#     if ctx.config.is_all_tenants:
#         return StepResult(outcome=StepOutcome.CONTINUE)
#
#     if ctx.job.tenant_id is None:
#         return StepResult(outcome=StepOutcome.SKIP_USER, reason="missing_tenant_id")
#
#     tenant_config = tenant_config_from_row(ctx.tenant_config_row)
#     if tenant_config.is_block:
#         return StepResult(outcome=StepOutcome.SKIP_USER, reason="tenant_blocked")
#
#     ctx.config = EffectiveConfig(
#         is_enable=ctx.config.is_enable,
#         is_all_tenants=False,
#         is_in_flagged=tenant_config.is_in_flagged,
#         is_manual_sms=tenant_config.is_manual_sms,
#         is_manual_email=tenant_config.is_manual_email,
#         is_sms_enable=tenant_config.is_sms_enable,
#         is_email_enable=tenant_config.is_email_enable,
#         platform_os=tenant_config.platform_os,
#         priority=tenant_config.priority,
#         is_block=False,
#     )
#     return StepResult(outcome=StepOutcome.CONTINUE)


def step_channel_enable(ctx: EligibilityContext) -> StepResult:
    notification_type = ctx.notification_type
    if notification_type == NotificationType.SMS.value:
        if not ctx.config.is_sms_enable:
            return StepResult(outcome=StepOutcome.SKIP_USER, reason="sms_disabled")
    elif notification_type == NotificationType.EMAIL.value:
        if not ctx.config.is_email_enable:
            return StepResult(outcome=StepOutcome.SKIP_USER, reason="email_disabled")
    return StepResult(outcome=StepOutcome.CONTINUE)


def step_conversation_eligibility(ctx: EligibilityContext) -> StepResult:
    session: Session = ctx.session
    if session is None or ctx.job.tenant_id is None:
        return StepResult(outcome=StepOutcome.SKIP_USER, reason="missing_session_or_tenant")

    if should_notify_user_for_conversation(session, ctx.job, ctx.user_id):
        return StepResult(outcome=StepOutcome.CONTINUE)

    return StepResult(outcome=StepOutcome.SKIP_USER, reason="not_flagged_or_manual_reply")


# def _parse_uuid(value: str | None) -> bool:
#     if not value or not str(value).strip():
#         return False
#     try:
#         UUID(str(value).strip())
#         return True
#     except (ValueError, TypeError):
#         return False
#
#
# def _has_prior_manual_outbound_sms(session: Session, job) -> bool:
#     if not job.thread_id or job.tenant_id is None:
#         return False
#     rows = (
#         session.query(SMSLogInDB.sent_by)
#         .filter(
#             SMSLogInDB.tenant_id == job.tenant_id,
#             SMSLogInDB.thread_key == job.thread_id,
#             SMSLogInDB.direction == MessageDirection.OUTBOUND.value,
#             SMSLogInDB.sent_by.isnot(None),
#         )
#         .limit(20)
#         .all()
#     )
#     return any(_parse_uuid(row[0]) for row in rows)
#
#
# def _has_prior_manual_outbound_email(session: Session, job) -> bool:
#     if job.tenant_id is None:
#         return False
#
#     lead_id = None
#     if job.email_log_id is not None:
#         current = (
#             session.query(EmailLogInDB.lead_id)
#             .filter(EmailLogInDB.id == job.email_log_id)
#             .first()
#         )
#         if current:
#             lead_id = current[0]
#
#     query = session.query(EmailLogInDB.sent_by).filter(
#         EmailLogInDB.tenant_id == job.tenant_id,
#         EmailLogInDB.direction == MessageDirection.OUTBOUND.value,
#         EmailLogInDB.sent_by.isnot(None),
#     )
#     if job.email_log_id is not None:
#         query = query.filter(EmailLogInDB.id != job.email_log_id)
#     if lead_id is not None:
#         query = query.filter(EmailLogInDB.lead_id == lead_id)
#     elif job.thread_id:
#         return False
#
#     rows = query.limit(20).all()
#     return any(_parse_uuid(row[0]) for row in rows)
#
#
# def step_manual_reply(ctx: EligibilityContext) -> StepResult:
#     """Replaced by step_conversation_eligibility."""
#     ...
#
#
# def is_conversation_flagged(
#     session: Session,
#     *,
#     tenant_id: UUID,
#     thread_id: str | None,
#     user_id: UUID,
#     user_role: str,
# ) -> bool:
#     """Hierarchy flagged check — disabled (dealership vs user flag_level)."""
#     ...
#
#
# def step_flagged(ctx: EligibilityContext) -> StepResult:
#     """Replaced by step_conversation_eligibility."""
#     ...


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
