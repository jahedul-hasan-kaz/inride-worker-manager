from __future__ import annotations

from typing import Callable, List

from app.eligibility.context import EligibilityContext, StepOutcome, StepResult
from app.eligibility.steps import (
    step_channel_enable,
    step_flagged,
    step_manual_reply,
    step_master_enable,
    step_platform_filter,
    step_tenant_scope,
)

StepFn = Callable[[EligibilityContext], StepResult]

STEP_REGISTRY: List[tuple[str, StepFn]] = [
    ("master_enable", step_master_enable),
    ("tenant_scope", step_tenant_scope),
    ("channel_enable", step_channel_enable),
    ("manual_reply", step_manual_reply),
    ("flagged", step_flagged),
    ("platform_filter", step_platform_filter),
]


def evaluate_user(ctx: EligibilityContext) -> StepResult:
    for step_name, step_fn in STEP_REGISTRY:
        result = step_fn(ctx)
        if result.outcome == StepOutcome.SKIP_USER:
            ctx.skip_reason = result.reason or step_name
            return result
    return StepResult(outcome=StepOutcome.CONTINUE)
