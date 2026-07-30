from __future__ import annotations

import pytest

from app.core.env import get_env, get_env_bool, get_env_float, get_env_int, get_project_id


def test_get_env_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_VAR", raising=False)
    assert get_env("MY_VAR", "fallback") == "fallback"


def test_get_env_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_VAR", "  value  ")
    assert get_env("MY_VAR") == "value"


def test_get_env_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "8080")
    assert get_env_int("PORT", 8000) == 8080


def test_get_env_float(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "10.5")
    assert get_env_float("POLL_INTERVAL_SECONDS", 60) == 10.5


def test_get_env_bool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SQLALCHEMY_ECHO", "1")
    assert get_env_bool("SQLALCHEMY_ECHO") is True


def test_get_project_id_prefers_project_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJECT_ID", "my-project")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "other")
    assert get_project_id() == "my-project"
