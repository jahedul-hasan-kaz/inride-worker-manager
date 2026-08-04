from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from app.pipeline.device_token_lookup import list_active_by_tenant


def test_list_active_by_tenant_filters_and_excludes_actor():
    session = MagicMock()
    query = MagicMock()
    query.join.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.all.return_value = []
    session.query.return_value = query

    tenant_id = uuid4()
    exclude_user_id = uuid4()
    list_active_by_tenant(session, tenant_id, exclude_user_id=exclude_user_id)

    assert session.query.called
    assert query.join.called
    assert query.filter.call_count >= 2
    assert query.order_by.called
