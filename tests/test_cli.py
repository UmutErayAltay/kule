"""app/cli.py testleri: `--notify-once` akışı.

Subprocess ÇALIŞTIRILMAZ; `app.cli.main` doğrudan çağrılır, `sys.argv`
monkeypatch'lenir. Gerçek collector'lar, gerçek Telegram yok — hepsi
sahte fonksiyonlarla değiştirilir.
"""
from __future__ import annotations

import pytest

from app import aggregator, cli


@pytest.fixture
def argv(monkeypatch):
    def _set(*args):
        monkeypatch.setattr("sys.argv", ["kule", *args])
    return _set


@pytest.fixture
def no_server(monkeypatch):
    """Sunucunun gerçekten başlamadığını doğrulamak için uvicorn.run'i
    sahte bir nesneyle değiştirir."""
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    return calls


@pytest.fixture(autouse=True)
def _reset_cache():
    """cli.get_cached_summary gerçek collector'ları tetiklemesin diye cache
    sıfırlanır (test_aggregator.py'deki desenin aynısı)."""
    aggregator._cache["summary"] = None
    aggregator._cache["fetched_at"] = 0.0
    yield
    aggregator._cache["summary"] = None
    aggregator._cache["fetched_at"] = 0.0


HEALTHY = {
    "git": [],
    "cor": {"reachable": True},
    "borsasite": {"reachable": True},
    "readbunny": {"reachable": True},
    "vault": {"broken_link_count": 0},
    "collected_at": 1_700_000_000.0,
}

BROKEN = {
    "git": [],
    "cor": {"reachable": False, "error": "kapalı"},
    "borsasite": {"reachable": True},
    "readbunny": {"reachable": True},
    "vault": {"broken_link_count": 0},
    "collected_at": 1_700_000_000.0,
}


# ------------------------------------------------------------------ parser


def test_parser_accepts_notify_once():
    args = cli.build_parser().parse_args(["--notify-once"])

    assert args.notify_once is True


def test_parser_defaults_to_serving():
    args = cli.build_parser().parse_args([])

    assert args.notify_once is False
    assert args.host == cli.DEFAULT_HOST
    assert args.port == cli.DEFAULT_PORT
    assert args.reload is False


# ------------------------------------------------------------ notify-once


def test_notify_once_does_not_start_server(monkeypatch, argv, no_server, capsys):
    argv("--notify-once")
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "get_cached_summary", lambda cfg: HEALTHY)
    monkeypatch.setattr(cli, "send_telegram_message", lambda cfg, text: True)

    cli.main()

    assert no_server == []
    assert "her şey yolunda" in capsys.readouterr().out


def test_notify_once_healthy_does_not_send(monkeypatch, argv, capsys):
    sent = []
    argv("--notify-once")
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "get_cached_summary", lambda cfg: HEALTHY)
    monkeypatch.setattr(cli, "send_telegram_message", lambda cfg, text: sent.append(text))

    cli.main()

    assert sent == []
    assert "her şey yolunda" in capsys.readouterr().out


def test_notify_once_sends_and_prints_alert(monkeypatch, argv, capsys):
    sent = []
    argv("--notify-once")
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "get_cached_summary", lambda cfg: BROKEN)
    monkeypatch.setattr(
        cli,
        "send_telegram_message",
        lambda cfg, text: (sent.append((cfg, text)) or True),
    )

    cli.main()

    assert len(sent) == 1
    config, text = sent[0]
    assert config == {}
    assert "cor" in text
    out = capsys.readouterr().out
    assert text in out
    assert "gönderildi" in out


def test_notify_once_reports_send_failure(monkeypatch, argv, capsys):
    argv("--notify-once")
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "get_cached_summary", lambda cfg: BROKEN)
    monkeypatch.setattr(cli, "send_telegram_message", lambda cfg, text: False)

    cli.main()

    out = capsys.readouterr().out
    assert "cor" in out  # uyarı yine de stdout'a basılır
    assert "gönderilemedi" in out


def test_notify_once_with_missing_config_warns_without_sending(monkeypatch, argv, capsys):
    sent = []

    def raise_config_error():
        raise cli.ConfigError("config.yaml bulunamadı")

    argv("--notify-once")
    monkeypatch.setattr(cli, "load_config", raise_config_error)
    monkeypatch.setattr(cli, "get_cached_summary", lambda cfg: HEALTHY)
    monkeypatch.setattr(cli, "send_telegram_message", lambda cfg, text: sent.append(text))

    cli.main()  # raise etmemeli

    assert sent == []
    out = capsys.readouterr().out
    assert "config.yaml bulunamadı" in out


def test_notify_once_ignores_server_flags(monkeypatch, argv, no_server, capsys):
    """--host/--port verilse bile sunucu başlamaz; --notify-once kazanır."""
    argv("--host", "0.0.0.0", "--port", "1234", "--notify-once")
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "get_cached_summary", lambda cfg: HEALTHY)

    cli.main()

    assert no_server == []
    assert "her şey yolunda" in capsys.readouterr().out


# ------------------------------------------------------------ normal serve


def test_without_notify_once_starts_server(monkeypatch, argv, no_server):
    argv("--port", "9999")
    monkeypatch.setattr(cli, "_warn_if_config_missing", lambda: None)

    cli.main()

    assert len(no_server) == 1
    args, kwargs = no_server[0]
    assert args[0] == "app.main:app"
    assert kwargs["port"] == 9999
