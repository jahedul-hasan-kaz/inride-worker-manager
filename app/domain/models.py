from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID


class JobKind(str, Enum):
    IMMEDIATE_SINGLE = "immediate_single"
    # Future: AGGREGATE = "aggregate"


class PushStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    AGGREGATED_PROCESSING = "aggregated_processing"
    SENT = "sent"
    FAILED = "failed"
    WINDOW_SKIPPED = "window_skipped"


@dataclass(frozen=True)
class DeliveryTarget:
    device_token_id: UUID
    push_token: str
    user_id: Optional[UUID] = None
    notification_priority: int = 0


@dataclass
class DeliveryJob:
    """Extensible work unit. v1 = one notification; future = aggregate digest."""

    job_kind: JobKind
    notification_id: UUID
    tenant_id: Optional[UUID] = None
    actor_user_id: Optional[UUID] = None
    notification_type: str = ""
    preview_text: Optional[str] = None
    preview_title: Optional[str] = None
    sender: Optional[str] = None
    to: Optional[str] = None
    thread_id: Optional[str] = None
    direction: Optional[str] = None
    email_log_id: Optional[UUID] = None
    sms_log_id: Optional[int] = None
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None  # computed from ttl_sec at claim time
    aggregate_id: Optional[UUID] = None  # digest primary notification id
    source_notification_ids: List[UUID] = field(default_factory=list)
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    @property
    def ledger_notification_id(self) -> UUID:
        """Idempotency scope key; aggregates can override via aggregate_id later."""
        return self.aggregate_id or self.notification_id


@dataclass
class DeviceSendOutcome:
    device_token_id: UUID
    result: str  # sent | failed | skipped
    error: Optional[str] = None


@dataclass
class DeliveryResult:
    job: DeliveryJob
    notification_status: PushStatus
    device_outcomes: List[DeviceSendOutcome] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def any_device_sent(self) -> bool:
        return any(o.result == "sent" for o in self.device_outcomes)
