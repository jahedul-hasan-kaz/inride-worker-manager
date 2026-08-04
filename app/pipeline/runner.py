from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from loguru import logger

from app.db.session import session_scope
from app.domain.handle_result import HandleDisposition, HandleResult
from app.domain.models import DeliveryJob, DeliveryResult, DeviceSendOutcome, JobKind, PushStatus
from app.domain.policies import DeliveryPolicy, ImmediateSingleNotificationPolicy
from app.eligibility.context import EffectiveConfig
from app.eligibility.notification_config_resolver import load_global_effective_config
from app.monitoring.metrics import push_metrics
from app.pipeline.aggregation import (
    AggregatePushPlan,
    build_aggregate_payload,
    group_jobs_for_processing,
    merge_targets_for_group,
)
from app.pipeline.notification_pubsub_client import NotificationPubSubClient
from app.pipeline.claimer import NotificationClaimer
from app.pipeline.device_idempotency import DeviceIdempotency
from app.pipeline.finalizer import NotificationFinalizer
from app.pipeline.push_message_builder import build_push_item
from app.pipeline.push_types import PreparedPush
from app.pipeline.push_trace import PushTrace
from app.pipeline.recipient_resolver import RecipientResolver


class DeliveryRunner:
    """Orchestrates eligibility, tracking, and push delivery via notification Pub/Sub."""

    def __init__(
        self,
        *,
        policy: Optional[DeliveryPolicy] = None,
        sender: Optional[NotificationPubSubClient] = None,
    ) -> None:
        self.policy = policy or ImmediateSingleNotificationPolicy()
        self.sender = sender or NotificationPubSubClient()

    def handle_notification_id(self, notification_id: UUID) -> HandleResult:
        try:
            with session_scope() as session:
                config = load_global_effective_config(session)
                job, miss_status = NotificationClaimer.claim_by_id(
                    session,
                    notification_id,
                    effective_config=config,
                )

            if job is None:
                if miss_status in (PushStatus.SENT.value, PushStatus.FAILED.value):
                    logger.info(
                        "Notification {} already done status={}",
                        notification_id,
                        miss_status,
                    )
                    return HandleResult(
                        disposition=HandleDisposition.ACK_ALREADY_DONE,
                        detail=miss_status,
                    )
                if miss_status in (
                    PushStatus.PROCESSING.value,
                    PushStatus.AGGREGATED_PROCESSING.value,
                ):
                    return HandleResult(
                        disposition=HandleDisposition.ACK_IN_FLIGHT,
                        detail=miss_status,
                    )
                return HandleResult(
                    disposition=HandleDisposition.NACK_RETRY,
                    detail=miss_status or "missing",
                )

            results = self.process_batch([job])
            delivery = results[0] if results else None
            return HandleResult(
                disposition=HandleDisposition.ACK_PROCESSED,
                delivery=delivery,
            )
        except Exception as exc:
            logger.exception("Unhandled error processing notification {}: {}", notification_id, exc)
            return HandleResult(
                disposition=HandleDisposition.NACK_ERROR,
                detail=str(exc),
            )

    def process_job(self, job: DeliveryJob) -> DeliveryResult:
        results = self.process_batch([job])
        return results[0]

    def process_batch(self, jobs: List[DeliveryJob]) -> List[DeliveryResult]:
        logger.info("Processing push batch size={}", len(jobs))
        prepared: List[PreparedPush] = []
        early_results: dict[UUID, DeliveryResult] = {}
        traces: dict[UUID, PushTrace] = {}

        with session_scope() as session:
            delivery_config = load_global_effective_config(session)

        job_groups = group_jobs_for_processing(jobs, delivery_config)
        for group in job_groups:
            try:
                self._prepare_group(
                    group,
                    delivery_config,
                    prepared,
                    early_results,
                    traces,
                )
            except Exception as exc:
                for job in group:
                    logger.exception(
                        "Failed preparing notification={} tenant={}: {}",
                        job.notification_id,
                        job.tenant_id,
                        exc,
                    )
                    with session_scope() as session:
                        NotificationFinalizer.finalize(
                            session,
                            job,
                            status=PushStatus.FAILED,
                            error=str(exc)[:500],
                        )
                    early_results[job.notification_id] = DeliveryResult(
                        job=job,
                        notification_status=PushStatus.FAILED,
                        error=str(exc),
                    )

        if not prepared:
            logger.info(
                "Push batch has no items to send (jobs={} early_results={})",
                len(jobs),
                len(early_results),
            )
            return self._finalize_early_only(jobs, early_results)

        logger.info(
            "Publishing push batch to notification Pub/Sub items={} notifications={}",
            len(prepared),
            len({item.job.notification_id for item in prepared}),
        )
        batch_response = self.sender.send_push_batch([item.payload for item in prepared])
        results_by_device = batch_response.results_by_device
        logger.info(
            "Notification Pub/Sub batch result sent={} failed={}",
            batch_response.sent_count,
            batch_response.failed_count,
        )

        outcomes_by_job: dict[UUID, list[DeviceSendOutcome]] = {}
        for job_id, early in early_results.items():
            outcomes_by_job[job_id] = list(early.device_outcomes)

        deactivated_ids: list[UUID] = []

        for item in prepared:
            notification_ids = item.job.source_notification_ids or [item.job.notification_id]
            send_result = results_by_device.get(item.target.device_token_id)

            with session_scope() as session:
                if send_result and send_result.status == "sent":
                    for notification_id in notification_ids:
                        claim_job = DeliveryJob(
                            job_kind=JobKind.IMMEDIATE_SINGLE,
                            notification_id=notification_id,
                            tenant_id=item.job.tenant_id,
                        )
                        DeviceIdempotency.mark_sent(session, claim_job, item.target.device_token_id)
                        outcomes_by_job.setdefault(notification_id, []).append(
                            DeviceSendOutcome(
                                device_token_id=item.target.device_token_id,
                                result="sent",
                            )
                        )
                    push_metrics.device_attempted("sent")
                else:
                    for notification_id in notification_ids:
                        claim_job = DeliveryJob(
                            job_kind=JobKind.IMMEDIATE_SINGLE,
                            notification_id=notification_id,
                            tenant_id=item.job.tenant_id,
                        )
                        DeviceIdempotency.release_claim(
                            session,
                            claim_job,
                            item.target.device_token_id,
                        )
                        outcomes_by_job.setdefault(notification_id, []).append(
                            DeviceSendOutcome(
                                device_token_id=item.target.device_token_id,
                                result="failed",
                                error=send_result.error if send_result else "agent_push_batch_failed",
                            )
                        )
                    push_metrics.device_attempted("failed")
                    if send_result and send_result.deactivate:
                        deactivated_ids.append(item.target.device_token_id)

        delivery_results: List[DeliveryResult] = []

        for job in jobs:
            trace = traces.get(job.notification_id)
            prepared_ids = {
                notification_id
                for item in prepared
                for notification_id in (item.job.source_notification_ids or [item.job.notification_id])
            }
            if job.notification_id in early_results and job.notification_id not in prepared_ids:
                early = early_results[job.notification_id]
                if early.notification_status == PushStatus.SENT and not any(
                    o.result == "sent" for o in early.device_outcomes
                ):
                    with session_scope() as session:
                        DeviceIdempotency.seal_open_sending(session, job)
                        NotificationFinalizer.finalize(session, job, status=PushStatus.SENT)
                delivery_results.append(early)
                continue

            outcomes = outcomes_by_job.get(job.notification_id, [])
            job_prepared = [
                item
                for item in prepared
                if job.notification_id
                in (item.job.source_notification_ids or [item.job.notification_id])
            ]
            job_deactivated = [
                token_id
                for token_id in deactivated_ids
                if any(o.device_token_id == token_id and o.result == "failed" for o in outcomes)
            ]

            with session_scope() as session:
                if job_deactivated:
                    NotificationFinalizer.deactivate_tokens(session, job_deactivated)

                DeviceIdempotency.seal_open_sending(session, job)
                any_sent = any(o.result == "sent" for o in outcomes) or DeviceIdempotency.has_any_sent(
                    session, job
                )
                status = PushStatus.SENT if any_sent else PushStatus.FAILED
                finalize_error = None if any_sent else "all_device_sends_failed"
                NotificationFinalizer.finalize(session, job, status=status, error=finalize_error)

            sent = sum(1 for o in outcomes if o.result == "sent")
            failed = sum(1 for o in outcomes if o.result == "failed")
            skipped = sum(1 for o in outcomes if o.result == "skipped")

            if trace is not None and job_prepared:
                trace.publish(count=len(job_prepared))
                trace.complete(
                    publish_count=len(job_prepared),
                    resolved_devices=len(outcomes) or len(job_prepared),
                    sent=sent,
                    failed=failed,
                    skipped=skipped,
                    status=status.value,
                )

            if status == PushStatus.FAILED and outcomes:
                logger.warning(
                    "Notification {} finalized failed after {} device attempt(s)",
                    job.notification_id,
                    len(outcomes),
                )

            delivery_results.append(
                DeliveryResult(
                    job=job,
                    notification_status=status,
                    device_outcomes=outcomes,
                    error=finalize_error,
                )
            )

        return delivery_results

    def _prepare_group(
        self,
        jobs: List[DeliveryJob],
        delivery_config: EffectiveConfig,
        prepared: List[PreparedPush],
        early_results: dict[UUID, DeliveryResult],
        traces: dict[UUID, PushTrace],
    ) -> None:
        active_jobs: List[DeliveryJob] = []
        for job in jobs:
            trace = PushTrace(job.notification_id)
            traces[job.notification_id] = trace
            trace.start(
                notification_type=job.notification_type,
                direction=job.direction,
                tenant_id=job.tenant_id,
            )

            if not self.policy.should_deliver(job):
                skip = self.policy.skip_status(job) or PushStatus.FAILED
                reason = getattr(self.policy, "skip_reason", lambda _job: "skipped_by_policy")(job)
                with session_scope() as session:
                    NotificationFinalizer.finalize(session, job, status=skip, error=reason)
                early_results[job.notification_id] = DeliveryResult(
                    job=job,
                    notification_status=skip,
                    error=reason,
                )
                trace.policy_skip(status=skip.value)
                trace.complete(publish_count=0, resolved_devices=0, status=skip.value)
                continue

            active_jobs.append(job)

        if not active_jobs:
            return

        targets_by_job: dict[UUID, list] = {}
        for job in active_jobs:
            trace = traces[job.notification_id]
            with session_scope() as session:
                targets_by_job[job.notification_id] = RecipientResolver.resolve(
                    session,
                    job,
                    trace=trace,
                )

        plans = merge_targets_for_group(targets_by_job, active_jobs)
        if not plans:
            with session_scope() as session:
                NotificationFinalizer.finalize_many(
                    session,
                    [job.notification_id for job in active_jobs],
                    status=PushStatus.SENT,
                )
            for job in active_jobs:
                early_results[job.notification_id] = DeliveryResult(
                    job=job,
                    notification_status=PushStatus.SENT,
                    device_outcomes=[],
                )
                traces[job.notification_id].complete(
                    publish_count=0,
                    resolved_devices=0,
                    status=PushStatus.SENT.value,
                )
            return

        primary_trace = traces[active_jobs[-1].notification_id]
        primary_trace.idempotency()

        skipped_targets = 0
        prepared_before = len(prepared)
        for plan in plans:
            assert plan.target is not None
            claimed_all = True
            claim_jobs = [
                DeliveryJob(
                    job_kind=JobKind.IMMEDIATE_SINGLE,
                    notification_id=notification_id,
                    tenant_id=plan.jobs[-1].tenant_id,
                )
                for notification_id in plan.notification_ids
            ]
            with session_scope() as session:
                for claim_job in claim_jobs:
                    if not DeviceIdempotency.try_claim(session, claim_job, plan.target):
                        claimed_all = False
                        break

            primary_trace.idempotency_claim(
                device_token_id=plan.target.device_token_id,
                claimed=claimed_all,
            )

            if not claimed_all:
                skipped_targets += 1
                push_metrics.device_attempted("skipped")
                for notification_id in plan.notification_ids:
                    early_results.setdefault(
                        notification_id,
                        DeliveryResult(
                            job=plan.jobs[-1],
                            notification_status=PushStatus.SENT,
                        ),
                    )
                    early_results[notification_id].device_outcomes.append(
                        DeviceSendOutcome(
                            device_token_id=plan.target.device_token_id,
                            result="skipped",
                        )
                    )
                continue

            digest_job = plan.jobs[-1]
            if len(plan.jobs) > 1:
                digest_job = plan.jobs[-1]
                digest_job.source_notification_ids = list(plan.notification_ids)

            prepared.append(
                PreparedPush(
                    job=digest_job,
                    target=plan.target,
                    payload=build_aggregate_payload(
                        plan,
                        ttl_sec=delivery_config.ttl_sec,
                    ),
                )
            )

        prepared_for_group = len(prepared) - prepared_before
        primary_trace.idempotency_skip(
            prepared=prepared_for_group,
            skipped=skipped_targets,
            total=len(plans),
        )

        if prepared_for_group == 0:
            primary_trace.complete(
                publish_count=0,
                resolved_devices=len(plans),
                skipped=skipped_targets,
                status=PushStatus.SENT.value,
            )

    def _prepare_job(
        self,
        job: DeliveryJob,
        prepared: List[PreparedPush],
        early_results: dict[UUID, DeliveryResult],
        traces: dict[UUID, PushTrace],
    ) -> None:
        trace = PushTrace(job.notification_id)
        traces[job.notification_id] = trace
        trace.start(
            notification_type=job.notification_type,
            direction=job.direction,
            tenant_id=job.tenant_id,
        )

        if not self.policy.should_deliver(job):
            skip = self.policy.skip_status(job) or PushStatus.FAILED
            with session_scope() as session:
                NotificationFinalizer.finalize(
                    session,
                    job,
                    status=skip,
                    error="skipped_by_policy",
                )
            early_results[job.notification_id] = DeliveryResult(
                job=job,
                notification_status=skip,
                error="skipped_by_policy",
            )
            trace.policy_skip(status=skip.value)
            trace.complete(publish_count=0, resolved_devices=0, status=skip.value)
            return

        with session_scope() as session:
            targets = RecipientResolver.resolve(session, job, trace=trace)

        if not targets:
            with session_scope() as session:
                NotificationFinalizer.finalize(session, job, status=PushStatus.SENT)
            early_results[job.notification_id] = DeliveryResult(
                job=job,
                notification_status=PushStatus.SENT,
                device_outcomes=[],
            )
            trace.complete(publish_count=0, resolved_devices=0, status=PushStatus.SENT.value)
            return

        trace.idempotency()
        skipped_targets = 0
        prepared_before = len(prepared)
        for target in targets:
            with session_scope() as session:
                claimed = DeviceIdempotency.try_claim(session, job, target)

            trace.idempotency_claim(device_token_id=target.device_token_id, claimed=claimed)

            if not claimed:
                skipped_targets += 1
                push_metrics.device_attempted("skipped")
                early_results.setdefault(
                    job.notification_id,
                    DeliveryResult(job=job, notification_status=PushStatus.SENT),
                )
                early_results[job.notification_id].device_outcomes.append(
                    DeviceSendOutcome(device_token_id=target.device_token_id, result="skipped")
                )
                continue

            prepared.append(
                PreparedPush(
                    job=job,
                    target=target,
                    payload=build_push_item(job, target),
                )
            )

        prepared_for_job = len(prepared) - prepared_before
        trace.idempotency_skip(
            prepared=prepared_for_job,
            skipped=skipped_targets,
            total=len(targets),
        )

        if prepared_for_job == 0:
            trace.complete(
                publish_count=0,
                resolved_devices=len(targets),
                skipped=skipped_targets,
                status=PushStatus.SENT.value,
            )

    def _finalize_early_only(
        self,
        jobs: List[DeliveryJob],
        early_results: dict[UUID, DeliveryResult],
    ) -> List[DeliveryResult]:
        delivery_results: List[DeliveryResult] = []
        for job in jobs:
            early = early_results.get(job.notification_id)
            if early is None:
                continue
            if early.notification_status == PushStatus.SENT and not any(
                o.result == "sent" for o in early.device_outcomes
            ):
                with session_scope() as session:
                    DeviceIdempotency.seal_open_sending(session, job)
            delivery_results.append(early)
        return delivery_results
