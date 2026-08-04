from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Sequence
from uuid import UUID

from app.domain.models import DeliveryJob, DeliveryTarget
from app.eligibility.context import EffectiveConfig
from app.pipeline.delivery_timing import aggregation_applies
from app.pipeline.push_message_builder import build_digest_push_item


@dataclass(frozen=True)
class AggregatePushPlan:
    target: DeliveryTarget
    jobs: List[DeliveryJob]
    notification_ids: List[UUID]


def _thread_id_key(job: DeliveryJob) -> str:
    return (job.thread_id or "").strip()


def _is_digest_eligible(job: DeliveryJob, config: EffectiveConfig) -> bool:
    if not aggregation_applies(config, job.notification_type):
        return False
    return bool(_thread_id_key(job))


def group_jobs_for_processing(
    jobs: Sequence[DeliveryJob],
    config: EffectiveConfig,
) -> List[List[DeliveryJob]]:
    """Group claimed jobs into digest batches (thread_id) or singletons."""
    grouped: Dict[str, List[DeliveryJob]] = defaultdict(list)
    singles: List[List[DeliveryJob]] = []

    for job in sorted(jobs, key=lambda j: (j.created_at or 0, str(j.notification_id))):
        if _is_digest_eligible(job, config):
            grouped[_thread_id_key(job)].append(job)
        else:
            singles.append([job])

    batches: List[List[DeliveryJob]] = list(grouped.values())
    batches.extend(singles)
    return batches


def merge_targets_for_group(
    targets_by_notification: Dict[UUID, List[DeliveryTarget]],
    jobs: Sequence[DeliveryJob],
) -> List[AggregatePushPlan]:
    """Union device targets across jobs in a digest group (dedupe by device_token_id)."""
    by_device: Dict[UUID, DeliveryTarget] = {}
    notification_ids: List[UUID] = []
    for job in jobs:
        notification_ids.append(job.notification_id)
        for target in targets_by_notification.get(job.notification_id, []):
            if target.device_token_id not in by_device:
                by_device[target.device_token_id] = target
    if not by_device:
        return []
    return [
        AggregatePushPlan(
            target=target,
            jobs=list(jobs),
            notification_ids=notification_ids,
        )
        for target in by_device.values()
    ]


def build_aggregate_payload(
    plan: AggregatePushPlan,
    *,
    ttl_sec: int | None = None,
) -> dict:
    return build_digest_push_item(
        plan.jobs[-1],
        plan.target,
        plan.jobs,
        ttl_sec=ttl_sec,
    )
