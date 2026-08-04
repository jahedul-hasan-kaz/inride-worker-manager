from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from app.constants.notification_constants import NotificationType
from app.domain.models import DeliveryJob, DeliveryTarget, JobKind
from app.eligibility.context import EffectiveConfig
from app.pipeline.aggregation import group_jobs_for_processing, merge_targets_for_group
from app.pipeline.push_message_builder import build_digest_body, build_digest_title


def _sms_job(*, thread_id: str | None = None, tenant_id=None) -> DeliveryJob:
    return DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        notification_type=NotificationType.SMS.value,
        thread_id=thread_id,
    )


def test_group_jobs_for_processing_groups_by_thread_id_only():
    tenant_id = uuid4()
    thread_id = "thread-a"
    jobs = [
        _sms_job(thread_id=thread_id, tenant_id=tenant_id),
        _sms_job(thread_id=thread_id, tenant_id=tenant_id),
    ]
    config = EffectiveConfig(aggregation_type="sms", aggregation_sec=30)
    groups = group_jobs_for_processing(jobs, config)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_group_jobs_for_processing_splits_different_threads():
    jobs = [
        _sms_job(thread_id="thread-a"),
        _sms_job(thread_id="thread-b"),
    ]
    config = EffectiveConfig(aggregation_type="sms", aggregation_sec=30)
    groups = group_jobs_for_processing(jobs, config)
    assert len(groups) == 2
    assert all(len(group) == 1 for group in groups)


def test_group_jobs_for_processing_null_thread_stays_singleton():
    jobs = [_sms_job(thread_id=None), _sms_job(thread_id=None)]
    config = EffectiveConfig(aggregation_type="sms", aggregation_sec=30)
    groups = group_jobs_for_processing(jobs, config)
    assert len(groups) == 2


def test_group_jobs_for_processing_sms_mode_leaves_email_singleton():
    jobs = [
        DeliveryJob(
            job_kind=JobKind.IMMEDIATE_SINGLE,
            notification_id=uuid4(),
            notification_type=NotificationType.EMAIL.value,
            thread_id="thread-a",
        ),
        _sms_job(thread_id="thread-a"),
        _sms_job(thread_id="thread-a"),
    ]
    config = EffectiveConfig(aggregation_type="sms", aggregation_sec=30)
    groups = group_jobs_for_processing(jobs, config)
    assert len(groups) == 2
    sms_group = next(group for group in groups if len(group) > 1)
    assert len(sms_group) == 2
    assert all(job.notification_type == NotificationType.SMS.value for job in sms_group)


def test_group_jobs_for_processing_both_mixes_channels_on_same_thread():
    thread_id = "thread-a"
    jobs = [
        _sms_job(thread_id=thread_id),
        DeliveryJob(
            job_kind=JobKind.IMMEDIATE_SINGLE,
            notification_id=uuid4(),
            notification_type=NotificationType.EMAIL.value,
            thread_id=thread_id,
        ),
    ]
    config = EffectiveConfig(aggregation_type="both", aggregation_sec=30)
    groups = group_jobs_for_processing(jobs, config)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_merge_targets_for_group_builds_one_plan_per_device():
    user_id = uuid4()
    device_id = uuid4()
    job_a = DeliveryJob(job_kind=JobKind.IMMEDIATE_SINGLE, notification_id=uuid4(), notification_type="sms")
    job_b = DeliveryJob(job_kind=JobKind.IMMEDIATE_SINGLE, notification_id=uuid4(), notification_type="sms")
    target = DeliveryTarget(device_token_id=device_id, push_token="tok", user_id=user_id)
    plans = merge_targets_for_group(
        {job_a.notification_id: [target], job_b.notification_id: [target]},
        [job_a, job_b],
    )
    assert len(plans) == 1
    assert len(plans[0].notification_ids) == 2


def test_build_digest_title_matches_single_notification():
    job = DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=uuid4(),
        notification_type="sms",
        preview_title="Alice Smith",
        sender="+15551234567",
    )
    assert build_digest_title(job, 3) == "Alice Smith"


def test_build_digest_title_email_matches_single():
    job = DeliveryJob(
        job_kind=JobKind.IMMEDIATE_SINGLE,
        notification_id=uuid4(),
        notification_type="email",
        preview_title="Re: Quote",
        sender="alice@example.com",
    )
    assert build_digest_title(job, 2) == "Re: Quote"


def test_build_digest_body_pluralizes_notifications():
    jobs = [
        DeliveryJob(
            job_kind=JobKind.IMMEDIATE_SINGLE,
            notification_id=uuid4(),
            notification_type="sms",
            preview_text="first",
            created_at=datetime(2026, 8, 4, 15, 42, 0),
        ),
        DeliveryJob(
            job_kind=JobKind.IMMEDIATE_SINGLE,
            notification_id=uuid4(),
            notification_type="sms",
            preview_text="latest hello",
            created_at=datetime(2026, 8, 4, 15, 50, 0),
        ),
    ]
    assert build_digest_body(jobs) == "2 new notifications"