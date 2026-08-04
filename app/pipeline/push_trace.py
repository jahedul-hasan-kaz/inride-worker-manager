from __future__ import annotations

from typing import Any, Optional, Union
from uuid import UUID

from loguru import logger

IdLike = Union[UUID, str, None]


def _short_id(value: IdLike, length: int = 8) -> str:
    if value is None:
        return "-"
    text = str(value).replace("-", "")
    if len(text) <= length:
        return text
    return f"{text[:length]}…"


def _fmt_kv(**fields: Any) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            value = ",".join(str(item) for item in value)
        parts.append(f"{key}={value}")
    return " ".join(parts)


class PushTrace:
    """Human-friendly step-by-step trace for one notification push attempt."""

    TOTAL_STEPS = 6

    def __init__(self, notification_id: UUID) -> None:
        self.notification_id = notification_id
        self._tag = f"[{_short_id(notification_id)}]"

    def step(self, index: int, label: str) -> None:
        logger.info("{} [{}/{}] {}", self._tag, index, self.TOTAL_STEPS, label)

    def event(self, headline: str, **fields: Any) -> None:
        details = _fmt_kv(**fields)
        if details:
            logger.info("{} {} {}", self._tag, headline, details)
        else:
            logger.info("{} {}", self._tag, headline)

    def start(self, *, notification_type: str, direction: Optional[str], tenant_id: IdLike) -> None:
        self.event(
            "START",
            type=notification_type,
            direction=direction or "-",
            tenant=_short_id(tenant_id),
        )
        self.step(1, "Evaluate notification")

    def resolve_config(self) -> None:
        self.step(2, "Resolve config")

    def resolve_recipients(self) -> None:
        self.step(3, "Resolve recipients")

    def match(
        self,
        *,
        candidates: int,
        flagged: int,
        manual_reply: int,
        thread_id: Optional[str] = None,
    ) -> None:
        self.event(
            "MATCH",
            candidates=candidates,
            flagged=flagged,
            manual_reply=manual_reply,
            thread_id=thread_id,
        )

    def evaluate_eligibility(self) -> None:
        self.step(4, "Evaluate eligibility")

    def eligible_user(
        self,
        *,
        user_id: UUID,
        devices: int,
        active_devices: int,
        reasons: list[str],
    ) -> None:
        self.event(
            "ELIGIBLE",
            user=_short_id(user_id),
            devices=devices,
            active_devices=active_devices,
            reason=",".join(reasons) if reasons else "-",
        )

    def ineligible_user(self, *, user_id: UUID, devices: int, reason: str) -> None:
        self.event(
            "INELIGIBLE",
            user=_short_id(user_id),
            devices=devices,
            reason=reason,
        )

    def no_devices_user(self, *, user_id: UUID, reason: str = "no_active_tokens") -> None:
        self.event("NO_DEVICES", user=_short_id(user_id), reason=reason)

    def resolve_devices(self, *, resolved: int) -> None:
        self.event("RESOLVED", devices=resolved)

    def idempotency(self) -> None:
        self.step(5, "Idempotency")

    def idempotency_skip(self, *, prepared: int, skipped: int, total: int) -> None:
        if prepared == 0 and skipped == total and total > 0:
            self.event("SKIPPED", idempotency="all_targets", already_sent=skipped)
            return
        if skipped:
            self.event(
                "IDEMPOTENCY",
                prepared=prepared,
                skipped=skipped,
                total=total,
            )

    def idempotency_claim(self, *, device_token_id: UUID, claimed: bool) -> None:
        status = "claimed" if claimed else "already_sent"
        self.event("CLAIM", device=_short_id(device_token_id), status=status)

    def publish(self, *, count: int) -> None:
        self.event("PUBLISH", count=count)

    def complete(
        self,
        *,
        publish_count: int,
        resolved_devices: int,
        sent: int = 0,
        failed: int = 0,
        skipped: int = 0,
        status: Optional[str] = None,
    ) -> None:
        self.step(6, "Complete")
        self.event(
            "DONE",
            publish_count=publish_count,
            resolved_devices=resolved_devices,
            sent=sent,
            failed=failed,
            skipped=skipped,
            status=status,
        )

    def config_resolved(self, *, source: str, is_enable: bool, is_all_tenants: bool) -> None:
        self.event(
            "CONFIG",
            source=source,
            enabled=is_enable,
            is_all_tenants=is_all_tenants,
        )

    def skipped(self, *, reason: str, **fields: Any) -> None:
        self.event("SKIPPED", reason=reason, **fields)

    def policy_skip(self, *, status: str) -> None:
        self.skipped(reason="policy", status=status)

    def no_targets(self, *, lead_ids: Optional[list[str]] = None) -> None:
        self.skipped(reason="no_conversation_match", lead_ids=lead_ids)

    def no_eligible_devices(
        self,
        *,
        candidates: int,
        query_rows: int = 0,
        blank_tokens: int = 0,
    ) -> None:
        self.event(
            "RESOLVED",
            devices=0,
            candidates=candidates,
            query_rows=query_rows,
            blank_tokens=blank_tokens,
        )
        self.skipped(reason="no_eligible_devices")
