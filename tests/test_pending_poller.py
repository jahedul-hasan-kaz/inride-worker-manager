from __future__ import annotations

from app.eligibility.context import EffectiveConfig
from app.ingress.pending_poller import poll_interval_seconds, sleep_after_tick_seconds


def test_poll_interval_uses_aggregation_sec_when_enabled(monkeypatch):
    monkeypatch.setattr("app.ingress.pending_poller.config.POLL_INTERVAL_SECONDS", 5)
    cfg = EffectiveConfig(aggregation_type="sms", aggregation_sec=30)
    assert poll_interval_seconds(cfg) == 30.0


def test_poll_interval_uses_env_when_aggregation_disabled(monkeypatch):
    monkeypatch.setattr("app.ingress.pending_poller.config.POLL_INTERVAL_SECONDS", 5)
    cfg = EffectiveConfig(aggregation_type="none", aggregation_sec=30)
    assert poll_interval_seconds(cfg) == 5.0


def test_sleep_after_tick_compensates_for_tick_work():
    assert sleep_after_tick_seconds(10.0, 4.5) == 5.5
    assert sleep_after_tick_seconds(10.0, 10.0) == 0.0
    assert sleep_after_tick_seconds(10.0, 12.0) == 0.0
    assert sleep_after_tick_seconds(10.0, 0.0) == 10.0
