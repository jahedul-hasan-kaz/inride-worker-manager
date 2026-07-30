from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.core.secrets import get_secret


def test_get_secret_prefers_plaintext_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DB_URL", "postgresql://local")
    monkeypatch.setenv("PG_DB_URL_NAME", "pg-db-url")
    assert get_secret("PG_DB_URL") == "postgresql://local"


def test_get_secret_falls_back_to_secret_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_DB_URL", raising=False)
    monkeypatch.setenv("PG_DB_URL_NAME", "pg-db-url")
    monkeypatch.setenv("PROJECT_NUMBER", "123456789")

    with patch(
        "app.services.gcp.secret_manager_client.access_secret_version",
        return_value="postgresql://from-gcp",
    ) as mock_access:
        assert get_secret("PG_DB_URL") == "postgresql://from-gcp"
        mock_access.assert_called_once_with("pg-db-url")


def test_get_secret_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_DB_URL", raising=False)
    monkeypatch.delenv("PG_DB_URL_NAME", raising=False)
    assert get_secret("PG_DB_URL", default="") == ""


def test_get_secret_custom_name_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPO_ACCESS_TOKEN_KEY", raising=False)
    monkeypatch.setenv("CUSTOM_EXPO_NAME", "expo-access-token")
    monkeypatch.setenv("PROJECT_NUMBER", "123456789")

    with patch(
        "app.services.gcp.secret_manager_client.access_secret_version",
        return_value="token-from-gcp",
    ) as mock_access:
        assert get_secret("EXPO_ACCESS_TOKEN_KEY", name_key="CUSTOM_EXPO_NAME") == "token-from-gcp"
        mock_access.assert_called_once_with("expo-access-token")
