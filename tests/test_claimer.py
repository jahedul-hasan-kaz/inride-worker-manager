from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.constants.notification_constants import NotificationType
from app.eligibility.context import EffectiveConfig
from app.pipeline.claimer import NotificationClaimer
from app.pipeline.delivery_timing import AGGREGATION_WINDOW_GRACE_SEC


def test_claim_pending_batch_without_aggregation_uses_single_claim_only():
    session = MagicMock()
    cfg = EffectiveConfig(aggregation_type="none", aggregation_sec=30)
    claimed_id = uuid4()

    with patch.object(
        NotificationClaimer,
        "_claim_singles_batch",
        return_value=[claimed_id],
    ) as singles_mock, patch.object(
        NotificationClaimer,
        "_claim_digest_threads",
    ) as digest_mock, patch.object(
        NotificationClaimer,
        "_mark_window_skipped",
    ) as skip_mock, patch.object(
        NotificationClaimer,
        "_load_jobs",
        return_value=[],
    ):
        NotificationClaimer.claim_pending_batch(session, 5, config=cfg)

    singles_mock.assert_called_once_with(session, 5)
    digest_mock.assert_not_called()
    skip_mock.assert_not_called()
    session.commit.assert_called_once()


def test_claim_pending_batch_with_aggregation_runs_window_digest_and_singles():
    session = MagicMock()
    cfg = EffectiveConfig(aggregation_type="sms", aggregation_sec=30)
    digest_id = uuid4()
    single_id = uuid4()
    call_order: list[str] = []

    def digest(*_args, **_kwargs):
        call_order.append("digest")
        return [digest_id]

    def singles(*_args, **_kwargs):
        call_order.append("singles")
        if call_order.count("singles") == 1:
            return [single_id]
        return []

    def skip(*_args, **_kwargs):
        call_order.append("skip")
        return 2

    with patch.object(
        NotificationClaimer,
        "_mark_window_skipped",
        side_effect=skip,
    ) as skip_mock, patch.object(
        NotificationClaimer,
        "_claim_digest_threads",
        side_effect=digest,
    ) as digest_mock, patch.object(
        NotificationClaimer,
        "_claim_singles_batch",
        side_effect=singles,
    ) as singles_mock, patch.object(
        NotificationClaimer,
        "_load_jobs",
        return_value=[],
    ) as load_mock:
        NotificationClaimer.claim_pending_batch(session, 3, config=cfg)

    digest_mock.assert_called_once()
    assert digest_mock.call_args.kwargs["thread_limit"] == 3
    assert digest_mock.call_args.kwargs["agg_sec"] == 30
    assert digest_mock.call_args.kwargs["eligible_types"] == [NotificationType.SMS.value]
    assert singles_mock.call_count == 2
    skip_mock.assert_called_once()
    assert call_order == ["digest", "singles", "singles", "skip"]
    load_mock.assert_called_once_with(session, [digest_id, single_id])
    session.commit.assert_called_once()


def test_mark_window_skipped_updates_stranded_rows():
    session = MagicMock()
    result = MagicMock()
    result.rowcount = 4
    session.execute.return_value = result

    count = NotificationClaimer._mark_window_skipped(
        session,
        agg_sec=30,
        eligible_types=[NotificationType.SMS.value],
    )

    assert count == 4
    params = session.execute.call_args.args[1]
    assert params["skipped_status"] == "window_skipped"
    assert params["eligible_types"] == [NotificationType.SMS.value]
    assert params["window_sec"] == 30 + AGGREGATION_WINDOW_GRACE_SEC
    sql = str(session.execute.call_args.args[0])
    assert ":window_sec" in sql
    assert "make_interval(secs => :window_sec)" in sql


def test_claim_digest_threads_uses_grace_window():
    session = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = []
    session.execute.return_value = result

    NotificationClaimer._claim_digest_threads(
        session,
        thread_limit=2,
        agg_sec=10,
        eligible_types=[NotificationType.SMS.value],
    )

    params = session.execute.call_args.args[1]
    assert params["window_sec"] == 10 + AGGREGATION_WINDOW_GRACE_SEC
    sql = str(session.execute.call_args.args[0])
    assert "make_interval(secs => :window_sec)" in sql
    assert ":agg_sec" not in sql