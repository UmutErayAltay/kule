"""app/collectors/readbunny_status.py testleri. Lazy import edilen `psycopg`
modülü sahte bir modülle değiştirilir, gerçek DB'ye gidilmez.
"""
from __future__ import annotations

import datetime
import sys
import types

from app.collectors import readbunny_status


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
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._row)


def _install_fake_psycopg(monkeypatch, row=None, raise_on_connect=False, raise_on_execute=False):
    fake_module = types.ModuleType("psycopg")

    def connect(database_url, connect_timeout=5):
        if raise_on_connect:
            raise ConnectionError("db yok")
        if raise_on_execute:
            class BoomCursor(_FakeCursor):
                def execute(self, *a, **kw):
                    raise RuntimeError("tablo yok")

            class BoomConn(_FakeConn):
                def cursor(self):
                    return BoomCursor(self._row)

            return BoomConn(row)
        return _FakeConn(row)

    fake_module.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", fake_module)


def test_collect_no_database_url_returns_unreachable():
    result = readbunny_status.collect({"readbunny": {"database_url": ""}})

    assert result == {"reachable": False, "error": "config.readbunny.database_url tanımlı değil"}


def test_collect_missing_readbunny_key_returns_unreachable():
    result = readbunny_status.collect({})

    assert result["reachable"] is False
    assert "error" in result


def test_collect_success(monkeypatch):
    ts = datetime.datetime(2026, 3, 4, 12, 0, 0)
    _install_fake_psycopg(monkeypatch, row=(ts, 2, 5, 100))

    config = {"readbunny": {"database_url": "postgresql://fake"}}
    result = readbunny_status.collect(config)

    assert result["reachable"] is True
    assert result["last_updated"] == ts.isoformat()
    assert result["error_count"] == 2
    assert result["pending_count"] == 5
    assert result["total_count"] == 100


def test_collect_success_with_null_last_updated(monkeypatch):
    _install_fake_psycopg(monkeypatch, row=(None, 0, 0, 0))

    config = {"readbunny": {"database_url": "postgresql://fake"}}
    result = readbunny_status.collect(config)

    assert result["reachable"] is True
    assert result["last_updated"] is None


def test_collect_connection_failure_returns_dict_not_raise(monkeypatch):
    _install_fake_psycopg(monkeypatch, raise_on_connect=True)

    config = {"readbunny": {"database_url": "postgresql://fake"}}
    result = readbunny_status.collect(config)

    assert result["reachable"] is False
    assert "error" in result


def test_collect_query_failure_returns_dict_not_raise(monkeypatch):
    _install_fake_psycopg(monkeypatch, row=(None, 0, 0, 0), raise_on_execute=True)

    config = {"readbunny": {"database_url": "postgresql://fake"}}
    result = readbunny_status.collect(config)

    assert result["reachable"] is False
    assert "error" in result
