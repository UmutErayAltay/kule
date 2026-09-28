"""app/collectors/cor_status.py testleri. httpx.Client sahte bir nesneyle
değiştirilir, gerçek ağa gidilmez.
"""
from __future__ import annotations

from app.collectors import cor_status


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
    def __init__(self, responses):
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        if url not in self._responses:
            raise AssertionError(f"beklenmeyen url: {url}")
        return self._responses[url]


def _patch_client(monkeypatch, responses):
    monkeypatch.setattr(cor_status.httpx, "Client", lambda *a, **kw: FakeHttpClient(responses))


def test_collect_no_base_url_returns_unreachable():
    result = cor_status.collect({"cor": {"base_url": ""}})

    assert result == {"reachable": False, "error": "config.cor.base_url tanımlı değil"}


def test_collect_missing_cor_key_returns_unreachable():
    result = cor_status.collect({})

    assert result["reachable"] is False
    assert "error" in result


def test_collect_full_success(monkeypatch):
    responses = {
        "http://x/healthz": FakeResponse({"status": "ok"}),
        "http://x/dashboard/api/health": FakeResponse({"dash": "ok"}),
        "http://x/dashboard/api/metrics-summary": FakeResponse({"m": 1}),
    }
    _patch_client(monkeypatch, responses)

    result = cor_status.collect({"cor": {"base_url": "http://x"}})

    assert result["reachable"] is True
    assert result["health"] == {"status": "ok"}
    assert result["dashboard_health"] == {"dash": "ok"}
    assert result["metrics"] == {"m": 1}


def test_collect_strips_trailing_slash_from_base_url(monkeypatch):
    responses = {
        "http://x/healthz": FakeResponse({"status": "ok"}),
        "http://x/dashboard/api/health": FakeResponse({"dash": "ok"}),
        "http://x/dashboard/api/metrics-summary": FakeResponse({"m": 1}),
    }
    _patch_client(monkeypatch, responses)

    result = cor_status.collect({"cor": {"base_url": "http://x/"}})

    assert result["reachable"] is True


def test_collect_main_healthz_failure_marks_unreachable(monkeypatch):
    responses = {"http://x/healthz": FakeResponse(None, status_ok=False)}
    _patch_client(monkeypatch, responses)

    result = cor_status.collect({"cor": {"base_url": "http://x"}})

    assert result["reachable"] is False
    assert "error" in result


def test_collect_optional_dashboard_health_failure_does_not_break_main(monkeypatch):
    class PartialClient(FakeHttpClient):
        def get(self, url):
            if url == "http://x/healthz":
                return FakeResponse({"status": "ok"})
            raise RuntimeError("opsiyonel uc patladi")

    monkeypatch.setattr(cor_status.httpx, "Client", lambda *a, **kw: PartialClient({}))

    result = cor_status.collect({"cor": {"base_url": "http://x"}})

    assert result["reachable"] is True
    assert result["health"] == {"status": "ok"}
    assert result["dashboard_health"] is None
    assert result["metrics"] is None


def test_collect_connection_error_returns_dict_not_raise(monkeypatch):
    def raiser(*a, **kw):
        raise ConnectionError("cor kapalı")

    monkeypatch.setattr(cor_status.httpx, "Client", raiser)

    result = cor_status.collect({"cor": {"base_url": "http://x"}})

    assert result == {"reachable": False, "error": "cor kapalı"}
