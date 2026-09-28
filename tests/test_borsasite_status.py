"""app/collectors/borsasite_status.py testleri. Gerçek ağ/DB'ye gidilmez:
httpx.Client ve (lazy import edilen) psycopg sahte nesnelerle değiştirilir.
"""
from __future__ import annotations

import sys
import types

import pytest

from app.collectors import borsasite_status


class FakeResponse:
    def __init__(self, json_data, status_ok=True):
        self._json_data = json_data
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP hata")

    def json(self):
        return self._json_data


class FakeHttpClient:
    def __init__(self, responses=None, raise_on_connect=False, timeout=None):
        self._responses = responses or {}
        self._raise_on_connect = raise_on_connect

    def __enter__(self):
        if self._raise_on_connect:
            raise ConnectionError("bağlanamadı")
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        if url not in self._responses:
            raise AssertionError(f"beklenmeyen url: {url}")
        return self._responses[url]


def _patch_httpx_client(monkeypatch, responses=None, raise_on_connect=False):
    def factory(*args, **kwargs):
        return FakeHttpClient(responses=responses, raise_on_connect=raise_on_connect)

    monkeypatch.setattr(borsasite_status.httpx, "Client", factory)


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self._call = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        row = self._rows[self._call] if self._call < len(self._rows) else (None,)
        self._call += 1
        return _FakeCursor(row)


def _install_fake_psycopg(monkeypatch, rows=None, raise_on_connect=False):
    fake_module = types.ModuleType("psycopg")

    def connect(database_url, connect_timeout=5):
        if raise_on_connect:
            raise ConnectionError("db bağlantısı yok")
        return _FakeConn(rows or [(None,), (None,)])

    fake_module.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", fake_module)


def test_collect_no_health_url_sets_error(monkeypatch):
    config = {"borsasite": {"health_url": "", "database_url": ""}}

    result = borsasite_status.collect(config)

    assert result["reachable"] is False
    assert result["health"] is None
    assert result["db"] is None
    assert "health_url" in result["error"]


def test_collect_health_url_success(monkeypatch):
    responses = {"http://x/health": FakeResponse({"status": "ok"})}
    _patch_httpx_client(monkeypatch, responses=responses)
    config = {"borsasite": {"health_url": "http://x/health", "database_url": ""}}

    result = borsasite_status.collect(config)

    assert result["reachable"] is True
    assert result["health"] == {"status": "ok"}
    assert result["db"] is None
    assert "error" not in result


def test_collect_health_url_http_error_sets_error_and_unreachable(monkeypatch):
    responses = {"http://x/health": FakeResponse(None, status_ok=False)}
    _patch_httpx_client(monkeypatch, responses=responses)
    config = {"borsasite": {"health_url": "http://x/health", "database_url": ""}}

    result = borsasite_status.collect(config)

    assert result["reachable"] is False
    assert "error" in result


def test_collect_health_url_connection_failure(monkeypatch):
    _patch_httpx_client(monkeypatch, raise_on_connect=True)
    config = {"borsasite": {"health_url": "http://x/health", "database_url": ""}}

    result = borsasite_status.collect(config)

    assert result["reachable"] is False
    assert "error" in result


def test_collect_with_database_url_fetches_signals(monkeypatch):
    responses = {"http://x/health": FakeResponse({"status": "ok"})}
    _patch_httpx_client(monkeypatch, responses=responses)

    import datetime

    ts = datetime.datetime(2026, 1, 1)
    _install_fake_psycopg(monkeypatch, rows=[(ts,), (ts,)])

    config = {
        "borsasite": {
            "health_url": "http://x/health",
            "database_url": "postgresql://fake",
        }
    }

    result = borsasite_status.collect(config)

    assert result["reachable"] is True
    assert result["db"]["last_trade_decision"] == ts.isoformat()
    assert result["db"]["last_prediction"] == ts.isoformat()


def test_collect_database_connect_failure_leaves_fields_none(monkeypatch):
    _patch_httpx_client(monkeypatch, responses={"http://x/health": FakeResponse({"status": "ok"})})
    _install_fake_psycopg(monkeypatch, raise_on_connect=True)

    config = {
        "borsasite": {
            "health_url": "http://x/health",
            "database_url": "postgresql://fake",
        }
    }

    result = borsasite_status.collect(config)

    assert result["reachable"] is True  # health hala calisti
    assert result["db"] == {"last_trade_decision": None, "last_prediction": None}


def test_collect_no_health_url_but_database_url_still_queries_db(monkeypatch):
    import datetime

    ts = datetime.datetime(2026, 2, 2)
    _install_fake_psycopg(monkeypatch, rows=[(ts,), (None,)])

    config = {"borsasite": {"health_url": "", "database_url": "postgresql://fake"}}

    result = borsasite_status.collect(config)

    assert result["reachable"] is False
    assert result["db"]["last_trade_decision"] == ts.isoformat()
    assert result["db"]["last_prediction"] is None
