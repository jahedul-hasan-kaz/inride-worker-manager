from __future__ import annotations

import threading
from collections import defaultdict
from typing import DefaultDict, Dict

from loguru import logger

try:
    from opentelemetry import metrics
except Exception:  # pragma: no cover
    metrics = None


class PushMetrics:
    """Low-cardinality counters. No hot-path DB writes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._local: DefaultDict[str, int] = defaultdict(int)
        self._otel_counters: Dict[str, object] = {}
        self._meter = None
        if metrics is not None:
            try:
                self._meter = metrics.get_meter("ai-agent-notification")
            except Exception:
                self._meter = None

    def _inc(self, name: str, labels: Dict[str, str], amount: int = 1) -> None:
        key = name + "|" + ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        with self._lock:
            self._local[key] += amount

        if self._meter is None:
            return
        try:
            counter = self._otel_counters.get(name)
            if counter is None:
                counter = self._meter.create_counter(name)
                self._otel_counters[name] = counter
            counter.add(amount, labels)  # type: ignore[attr-defined]
        except Exception:
            pass

    def notification_finalized(self, status: str) -> None:
        self._inc("expo_push.notifications_finalized", {"status": status})

    def device_attempted(self, result: str) -> None:
        self._inc("expo_push.devices_attempted", {"result": result})

    def claim(self, result: str, amount: int = 1) -> None:
        self._inc("expo_push.claim_total", {"result": result}, amount=amount)

    def reclaim(self, count: int) -> None:
        if count:
            self._inc("expo_push.reclaim_total", {}, amount=count)

    def ingress_disposition(self, disposition: str) -> None:
        self._inc("expo_push.ingress_disposition", {"disposition": disposition})

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._local)

    def snapshot_and_reset(self) -> Dict[str, int]:
        with self._lock:
            snap = dict(self._local)
            self._local.clear()
            return snap

    def log_summary(self) -> None:
        snap = self.snapshot_and_reset()
        if not snap:
            return
        logger.info("expo_push metrics summary {}", snap)


push_metrics = PushMetrics()
