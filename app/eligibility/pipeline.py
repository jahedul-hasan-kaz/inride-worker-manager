from __future__ import annotations

from typing import Callable, List

from app.eligibility.context import EligibilityContext, StepOutcome, StepResult
from app.eligibility.steps import (
    step_channel_enable,
    step_conversation_eligibility,
    step_master_enable,
    step_platform_filter,
)

StepFn = Callable[[EligibilityContext], StepResult]

STEP_REGISTRY: List[tuple[str, StepFn]] = [
    ("master_enable", step_master_enable),
    # ("tenant_scope", step_tenant_scope),  # hierarchy — disabled
    ("channel_enable", step_channel_enable),
    ("conversation_eligibility", step_conversation_eligibility),
    # ("manual_reply", step_manual_reply),  # replaced
    # ("flagged", step_flagged),  # replaced
    ("platform_filter", step_platform_filter),
]


def evaluate_user(ctx: EligibilityContext) -> StepResult:
    from loguru import logger

    for step_name, step_fn in STEP_REGISTRY:
        result = step_fn(ctx)
        if result.outcome == StepOutcome.SKIP_USER:
            ctx.skip_reason = result.reason or step_name
            logger.info(
                "Eligibility skip notification={} user={} step={} reason={}",
                ctx.job.notification_id,
                ctx.user_id,
                step_name,
                ctx.skip_reason,
            )
            return result
    logger.info(
        "Eligibility pass notification={} user={} devices={} include_reasons={}",
        ctx.job.notification_id,
        ctx.user_id,
        len(ctx.eligible_devices),
        ctx.include_reasons,
    )
    return StepResult(outcome=StepOutcome.CONTINUE)
