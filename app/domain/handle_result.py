from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.domain.models import DeliveryResult


class HandleDisposition(str, Enum):
    """Pub/Sub / ingress ack policy."""

    ACK_PROCESSED = "ack_processed"
    ACK_ALREADY_DONE = "ack_already_done"
    ACK_IN_FLIGHT = "ack_in_flight"
    ACK_EXHAUSTED = "ack_exhausted"  # max deliveries / poison — ack to stop retry loop (no DLQ)
    NACK_RETRY = "nack_retry"
    NACK_ERROR = "nack_error"

    @property
    def should_ack(self) -> bool:
        return self in {
            HandleDisposition.ACK_PROCESSED,
            HandleDisposition.ACK_ALREADY_DONE,
            HandleDisposition.ACK_IN_FLIGHT,
            HandleDisposition.ACK_EXHAUSTED,
        }


@dataclass
class HandleResult:
    disposition: HandleDisposition
    delivery: Optional[DeliveryResult] = None
    detail: Optional[str] = None
