"""Tests for launcher._http_status logging behavior.

Uses monkeypatch to replace launcher.httpx.get; no real network.
"""
from __future__ import annotations

import pytest

from app import launcher


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_http_status_success_returns_code_and_no_warning(monkeypatch, caplog):
    """Successful response returns status code and logs nothing at WARNING."""
    monkeypatch.setattr(launcher.httpx, "get", lambda url, timeout=None: _FakeResponse(200))

    with caplog.at_level("WARNING"):
        result = launcher._http_status(8770)

    assert result == 200
    # No WARNING logs should be emitted on success
    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warning_records == []


def test_http_status_connection_refused_logs_type_only(monkeypatch, caplog):
    """ConnectionRefusedError logs exception type name only, not the message."""
    def raise_conn_refused(url, timeout=None):
        raise ConnectionRefusedError("secret-detail-xyz")

    monkeypatch.setattr(launcher.httpx, "get", raise_conn_refused)

    with caplog.at_level("WARNING"):
        result = launcher._http_status(8780)

    assert result is None
    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warning_records) == 1
    msg = warning_records[0].message
    assert "ConnectionRefusedError" in msg
    assert "8780" in msg
    assert "secret-detail-xyz" not in msg


def test_http_status_module_not_found_logs_type_name(monkeypatch, caplog):
    """ModuleNotFoundError logs the exception type name."""
    def raise_mod_not_found(url, timeout=None):
        raise ModuleNotFoundError("certifi")

    monkeypatch.setattr(launcher.httpx, "get", raise_mod_not_found)

    with caplog.at_level("WARNING"):
        result = launcher._http_status(8900)

    assert result is None
    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warning_records) == 1
    msg = warning_records[0].message
    assert "ModuleNotFoundError" in msg
    assert "8900" in msg
    assert "certifi" not in msg
