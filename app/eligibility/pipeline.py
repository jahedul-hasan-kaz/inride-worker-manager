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

STEP_REGISTRY_JOB_GATED: List[tuple[str, StepFn]] = [
    ("platform_filter", step_platform_filter),
]


def evaluate_user(ctx: EligibilityContext) -> StepResult:
    trace = ctx.trace
    device_count = len(ctx.devices)
    steps = STEP_REGISTRY_JOB_GATED if ctx.job_gated else STEP_REGISTRY

    for step_name, step_fn in steps:
        result = step_fn(ctx)
        if result.outcome == StepOutcome.SKIP_USER:
            ctx.skip_reason = result.reason or step_name
            if trace is not None:
                trace.ineligible_user(
                    user_id=ctx.user_id,
                    devices=device_count,
                    reason=ctx.skip_reason,
                )
            return result

    if trace is not None:
        trace.eligible_user(
            user_id=ctx.user_id,
            devices=device_count,
            active_devices=len(ctx.eligible_devices),
            reasons=list(ctx.include_reasons),
        )
    return StepResult(outcome=StepOutcome.CONTINUE)
