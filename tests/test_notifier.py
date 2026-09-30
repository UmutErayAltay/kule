"""app/notifier.py testleri. httpx.Client sahte bir nesneyle değiştirilir —
gerçek Telegram API'sine HİÇBİR ZAMAN istek gitmez. Token/chat_id boşken
ağa çıkılmadığı ayrıca sayacla doğrulanır.
"""
from __future__ import annotations

import pytest

from app import notifier


class FakeResponse:
    def __init__(self, status_ok=True):
        self._status_ok = status_ok
        self.raised = False

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP 401 Unauthorized")

    def json(self):
        return {"ok": True}


class FakeHttpClient:
    """post çağrılarını kaydeder; responses yoksa istenen url için hata fırlatır."""

    def __init__(self, recorder, response=None, raise_on_connect=False):
        self._recorder = recorder
        self._response = response if response is not None else FakeResponse()
        self._raise_on_connect = raise_on_connect

    def __enter__(self):
        if self._raise_on_connect:
            raise ConnectionError("Telegram'a ulaşılamadı")
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None):
        self._recorder.append((url, json))
        if self._response is None:
            raise AssertionError(f"beklenmeyen url: {url}")
        return self._response


def _patch_client(monkeypatch, recorder, response=None, raise_on_connect=False):
    monkeypatch.setattr(
        notifier.httpx,
        "Client",
        lambda *a, **kw: FakeHttpClient(
            recorder, response=response, raise_on_connect=raise_on_connect
        ),
    )


# ---------------------------------------------------------------- send


def test_send_without_credentials_makes_no_request(monkeypatch):
    recorder = []
    _patch_client(monkeypatch, recorder)

    assert notifier.send_telegram_message({}, "selam") is False
    assert recorder == []  # hiç çıkılmadı


def test_send_with_empty_token_makes_no_request(monkeypatch):
    recorder = []
    _patch_client(monkeypatch, recorder)
    config = {"telegram": {"bot_token": "", "chat_id": "123"}}

    assert notifier.send_telegram_message(config, "selam") is False
    assert recorder == []


def test_send_with_empty_chat_id_makes_no_request(monkeypatch):
    recorder = []
    _patch_client(monkeypatch, recorder)
    config = {"telegram": {"bot_token": "tok", "chat_id": ""}}

    assert notifier.send_telegram_message(config, "selam") is False
    assert recorder == []


def test_send_with_non_dict_telegram_makes_no_request(monkeypatch):
    recorder = []
    _patch_client(monkeypatch, recorder)

    assert notifier.send_telegram_message({"telegram": "bozuk"}, "selam") is False
    assert recorder == []


def test_send_success_posts_expected_url_and_payload(monkeypatch):
    recorder = []
    _patch_client(monkeypatch, recorder)
    config = {"telegram": {"bot_token": "123:ABC", "chat_id": "-100200"}}

    assert notifier.send_telegram_message(config, "merhaba") is True

    assert len(recorder) == 1
    url, payload = recorder[0]
    assert url == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert payload == {"chat_id": "-100200", "text": "merhaba"}


def test_send_http_error_returns_false_without_raising(monkeypatch):
    recorder = []
    _patch_client(monkeypatch, recorder, response=FakeResponse(status_ok=False))
    config = {"telegram": {"bot_token": "tok", "chat_id": "123"}}

    assert notifier.send_telegram_message(config, "merhaba") is False
    assert len(recorder) == 1  # istek atıldı, cevap reddedildi


def test_send_connection_error_returns_false_without_raising(monkeypatch):
    recorder = []
    _patch_client(monkeypatch, recorder, raise_on_connect=True)
    config = {"telegram": {"bot_token": "tok", "chat_id": "123"}}

    assert notifier.send_telegram_message(config, "merhaba") is False
    assert recorder == []


def test_send_unexpected_error_returns_false(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("httpx patladı")

    monkeypatch.setattr(notifier.httpx, "Client", boom)
    config = {"telegram": {"bot_token": "tok", "chat_id": "123"}}

    assert notifier.send_telegram_message(config, "merhaba") is False


# ------------------------------------------------------- build_alert_message


def test_build_alert_message_all_healthy_returns_none():
    summary = {
        "git": [{"name": "kule", "dirty_count": 0}],
        "cor": {"reachable": True, "health": {"ok": True}},
        "borsasite": {"reachable": True, "health": {"ok": True}},
        "readbunny": {"reachable": True, "total_count": 10},
        "vault": {"broken_link_count": 0, "open_threads": 2},
        "collected_at": 1_700_000_000.0,
    }

    assert notifier.build_alert_message(summary) is None


def test_build_alert_message_empty_summary_returns_none():
    assert notifier.build_alert_message({}) is None


def test_build_alert_message_collected_at_alone_is_not_a_problem():
    """`collect_all` her zaman collected_at (float) ekliyor — bu bir kaynak
    değil, tek başına uyarı üretmemeli."""
    assert notifier.build_alert_message({"collected_at": 1_700_000_000.0}) is None


def test_build_alert_message_reports_unreachable_source():
    summary = {"cor": {"reachable": False, "error": "connection refused"}}

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "cor" in message
    assert "erişilemiyor" in message
    assert "connection refused" in message


def test_build_alert_message_reports_error_without_reachable_key():
    """vault collector'ı `reachable` alanı döndürmez, yalnızca `error`."""
    summary = {"vault": {"error": "config.vault.path tanımlı değil"}}

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "vault" in message
    assert "hata veriyor" in message
    assert "config.vault.path" in message


def test_build_alert_message_reports_error_on_reachable_source():
    """borsasite `reachable: False` + `error` birlikte dönebilir."""
    summary = {"borsasite": {"reachable": False, "health": None, "error": "health_url boş"}}

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "borsasite" in message
    assert "erişilemiyor" in message


def test_build_alert_message_reports_isolated_collector_failure():
    """aggregator bir collector patlarsa o anahtarı `{"error": ...}` yapar."""
    summary = {"git": {"error": "kaboom"}}

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "git" in message


def test_build_alert_message_reports_failed_git_repos():
    """git listesi döner; hata liste elemanında, listenin kendisinde değil."""
    summary = {
        "git": [
            {"name": "kule", "dirty_count": 0},
            {"name": "bozuk-repo", "error": "git yok"},
        ]
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "git" in message
    assert "bozuk-repo" in message


def test_build_alert_message_healthy_git_list_is_not_a_problem():
    summary = {"git": [{"name": "kule", "dirty_count": 3}]}

    assert notifier.build_alert_message(summary) is None


def test_build_alert_message_lists_every_broken_source():
    summary = {
        "cor": {"reachable": False, "error": "kapalı"},
        "borsasite": {"reachable": False, "error": "zaman aşımı"},
        "readbunny": {"reachable": True},
        "vault": {"error": "yol bulunamadı"},
        "collected_at": 1_700_000_000.0,
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    for source in ("cor", "borsasite", "vault"):
        assert source in message
    assert "readbunny" not in message
    assert message.startswith("⚠️ kule uyarısı:")


def test_build_alert_message_caps_sources_and_error_length():
    summary = {
        "cor": {"reachable": False, "error": "x" * 500},
        "borsasite": {"reachable": False, "error": "b" * 500},
        "readbunny": {"reachable": False, "error": "c" * 500},
        "vault": {"reachable": False, "error": "d" * 500},
        "git": {"error": "e" * 500},
        "ekstra": {"error": "f" * 500},
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "(+1 kaynak daha)" in message  # 6 kaynak, 5'i listelenir
    # Hiçbir hata metni kırpılmamış haliyle geçmemeli
    assert "x" * notifier.MAX_ERROR_CHARS not in message
    assert message.count("…") == 5
    assert len(message) < 1000


def test_build_alert_message_includes_timestamp_line():
    summary = {"cor": {"reachable": False, "error": "x"}, "collected_at": 1_700_000_000.0}

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "\n" in message
    assert message.splitlines()[0].startswith("⚠️")
    assert "🕐" in message


def test_build_alert_message_tolerates_odd_shapes():
    """Bilinmeyen/kırık bir kaynak yapısı paneli düşürmemeli."""
    summary = {"cor": {"reachable": False, "error": "x"}, "tuhaf": "metin", "bos": None}

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "cor" in message


# ------------------------------------------------------------- maintenance

HEALTHY_MAINTENANCE = {
    "disk": {"threshold_percent": 90.0, "full": []},
    "stale_processes": {"min_hours": 6.0, "items": []},
    "stale_git": {"min_days": 3.0, "items": []},
}


def test_build_alert_message_healthy_maintenance_is_not_a_problem():
    summary = {
        "git": [],
        "cor": {"reachable": True},
        "maintenance": HEALTHY_MAINTENANCE,
        "collected_at": 1_700_000_000.0,
    }

    assert notifier.build_alert_message(summary) is None


def test_build_alert_message_empty_maintenance_is_not_a_problem():
    """maintenance anahtarı hiç sorun döndürmezse (dict ama içi boş) mesaj yok."""
    assert notifier.build_alert_message({"maintenance": {}}) is None
    assert notifier.build_alert_message({"maintenance": None}) is None


def test_build_alert_message_reports_full_disk():
    summary = {
        "maintenance": {
            "disk": {"threshold_percent": 90.0, "full": [{"path": "/", "percent": 95.2, "free_gb": 8.1}]},
            "stale_processes": {"items": []},
            "stale_git": {"items": []},
        }
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "disk dolu" in message
    assert "95.2" in message
    assert "/" in message


def test_build_alert_message_reports_stale_processes_with_counts():
    summary = {
        "maintenance": {
            "disk": {"full": []},
            "stale_processes": {
                "items": [
                    {"pid": 1, "name": "ollama", "hours": 30.0},
                    {"pid": 2, "name": "ollama", "hours": 10.0},
                    {"pid": 3, "name": "uvicorn", "hours": 8.0},
                ]
            },
            "stale_git": {"items": []},
        }
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "süreç" in message
    # aynı ada sahip süreçler sayıyla gruplanır
    assert "ollamax2" in message
    assert "uvicorn" in message


def test_build_alert_message_reports_stale_git_repos():
    summary = {
        "maintenance": {
            "disk": {"full": []},
            "stale_processes": {"items": []},
            "stale_git": {"items": [{"name": "kule", "dirty_count": 2, "age_days": 9.0}]},
        }
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "kule" in message
    assert "commitlenmemiş" in message


def test_build_alert_message_lists_each_maintenance_finding_on_its_own_line():
    summary = {
        "maintenance": {
            "disk": {"full": [{"path": "/home", "percent": 97.0}]},
            "stale_processes": {"items": [{"pid": 1, "name": "node", "hours": 12.0}]},
            "stale_git": {"items": [{"name": "kule", "dirty_count": 1, "age_days": 5.0}]},
        },
        "collected_at": 1_700_000_000.0,
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    lines = message.splitlines()
    assert lines[0].startswith("⚠️ kule uyarısı")
    # üç bulgu = üç ayrı satır (+ zaman damgası)
    assert len(lines) == 5
    assert "disk dolu" in lines[1]
    assert "süreç" in lines[2]
    assert "commitlenmemiş" in lines[3]
    assert "🕐" in lines[4]


def test_build_alert_message_reports_maintenance_section_errors():
    summary = {
        "maintenance": {
            "disk": {"error": "izin yok"},
            "stale_processes": {"error": "psutil kurulu değil", "items": []},
            "stale_git": {"items": []},
        }
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "disk okunamadı" in message
    assert "izin yok" in message
    assert "süreçler okunamadı" in message
    assert "psutil kurulu değil" in message


def test_build_alert_message_combines_sources_and_maintenance():
    summary = {
        "cor": {"reachable": False, "error": "kapalı"},
        "maintenance": {
            "disk": {"full": []},
            "stale_processes": {"items": [{"pid": 1, "name": "ollama", "hours": 9.0}]},
            "stale_git": {"items": []},
        },
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    lines = message.splitlines()
    # kaynak cümlesi başlıkta, bakım bulgusu kendi satırında
    assert "cor erişilemiyor" in lines[0]
    assert "süreç" in lines[1]


def test_build_alert_message_maintenance_alone_has_generic_header():
    summary = {"maintenance": {"disk": {"full": [{"path": "/", "percent": 99.0}]}}}

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert message.splitlines()[0].startswith("⚠️ kule uyarısı")
    assert "disk dolu" in message


def test_build_alert_message_maintenance_does_not_consume_source_quota():
    """Bakım bulguları MAX_LISTED_SOURCES kotasına girmez — 6 kaynak sorunlu
    olsa bile "+1 kaynak daha" gizlemeye devam etmeli."""
    summary = {
        "git": {"error": "g"},
        "cor": {"error": "c"},
        "borsasite": {"error": "b"},
        "readbunny": {"error": "r"},
        "vault": {"error": "v"},
        "ekstra": {"error": "e"},
        "maintenance": {"disk": {"full": [{"path": "/", "percent": 99.0}]}},
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "(+1 kaynak daha)" in message
    assert "disk dolu" in message


def test_build_alert_message_tolerates_odd_maintenance_shapes():
    """Bilinmeyen/kırık maintenance yapısı paneli düşürmemeli."""
    summary = {
        "cor": {"reachable": False, "error": "x"},
        "maintenance": {"disk": "metin", "stale_processes": [1, 2], "stale_git": {"items": ["bozuk"]}},
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "cor" in message
    # bozuk alt bölümler uyarı üretmemeli, "?" gibi sahte isim de üretmemeli
    assert "bozuk" not in message


# ------------------------------------------------- güvenlik / sınır testleri


def test_maintenance_lines_are_newline_sanitized():
    """GÜVENLİK: repo adı/disk yolu collector'dan gelir ve `_describe` yolunda
    `_truncate` ile newline temizlenir. Bakım yolunda aynı temizlik şarttı —
    aksi halde sahte çok satırlı Telegram mesajı enjeksiyonu mümkündür."""
    injected = "kule\n⚠️ sahte ikinci uyarı\n💀"
    summary = {
        "maintenance": {
            "disk": {"full": [{"path": injected, "percent": 99.0}]},
            "stale_processes": {
                "items": [{"pid": 1, "name": injected, "hours": 9.0}]
            },
            "stale_git": {"items": [{"name": injected, "dirty_count": 1, "age_days": 4.0}]},
        }
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    # Enjeksiyon AYRI SATIR oluşturamadı: başlık + 3 bulgu, fazlası değil
    lines = message.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("⚠️ kule uyarısı")
    for line in lines:
        assert "\n" not in line
    # newline'ler boşlukla değiştirildi, içerik aynı satırda kaldı
    assert "kule ⚠️ sahte ikinci uyarı 💀" in lines[1]
    # üç satır da aynı kaçak değeri taşıyor (her bakım yolu temizlendi)
    assert sum("sahte ikinci uyarı" in line for line in lines[1:]) == 3


def test_clamp_message_cuts_at_telegram_limit():
    """Telegram sendMessage 4096 karakterlik tavanı reddeder ve bildirim
    sessizce kaybolur; mesaj ne kadar uzun olursa olsun altında kalmalı."""
    over = "x" * (notifier.MAX_MESSAGE_CHARS + 500)

    clamped = notifier._clamp_message(over)

    assert len(clamped) == notifier.MAX_MESSAGE_CHARS
    assert clamped.endswith("…")  # kesildiği belli olsun

    # tam sınırda dokunulmaz
    exact = "y" * notifier.MAX_MESSAGE_CHARS
    assert notifier._clamp_message(exact) == exact
    assert not notifier._clamp_message(exact).endswith("…")


def test_build_alert_message_never_exceeds_telegram_limit(monkeypatch):
    """Uçtan uca: kısaltma sınırı çok büyütülse bile (yani `_truncate`
    devre dışı kalmış gibi) mesaj 4096'nın altında kalmalı."""
    monkeypatch.setattr(notifier, "MAX_ERROR_CHARS", 10_000)
    summary = {
        "cor": {"reachable": False, "error": "x" * 5000},
        "borsasite": {"reachable": False, "error": "y" * 5000},
        "readbunny": {"error": "z" * 5000},
        "vault": {"error": "w" * 5000},
        "git": [
            {"name": "repo-" + "a" * 300, "error": "git yok"},
            {"name": "repo-" + "b" * 300, "error": "git yok"},
        ],
        "maintenance": {
            "disk": {"full": [{"path": "/mnt/" + "d" * 3000, "percent": 99.0}]},
            "stale_processes": {
                "items": [{"pid": i, "name": "node" * 200, "hours": 9.0} for i in range(10)]
            },
            "stale_git": {
                "items": [
                    {"name": "repo" * 300, "dirty_count": 1, "age_days": 4.0}
                    for _ in range(10)
                ]
            },
        },
        "collected_at": 1_700_000_000.0,
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert len(message) <= notifier.MAX_MESSAGE_CHARS
    assert message.endswith("…")  # kesildiği belli olsun


def test_build_alert_message_within_limit_is_untouched():
    """Kısa mesajlarda kesme yapılmamalı."""
    summary = {"cor": {"reachable": False, "error": "kapalı"}}

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert not message.endswith("…")


def test_send_restores_httpx_log_level(monkeypatch):
    """GÜVENLİK: token URL'de olduğu için httpx'in INFO log'u bastırılır,
    ama global logger seviyesi çağrıdan SONRA eski haline döner."""
    import logging

    logger = logging.getLogger("httpx")
    monkeypatch.setattr(logger, "level", logging.DEBUG)

    recorder = []
    _patch_client(monkeypatch, recorder)

    assert notifier.send_telegram_message(
        {"telegram": {"bot_token": "TOKEN", "chat_id": "42"}}, "merhaba"
    ) is True
    assert logger.level == logging.DEBUG

    # hata yolunda da seviye geri konmalı
    logger.setLevel(logging.DEBUG)
    _patch_client(monkeypatch, recorder, response=FakeResponse(status_ok=False))
    assert notifier.send_telegram_message(
        {"telegram": {"bot_token": "TOKEN", "chat_id": "42"}}, "merhaba"
    ) is False
    assert logger.level == logging.DEBUG


def test_send_silences_httpx_log_during_request(monkeypatch):
    """Gönderim sırasında httpx log'u WARNING'e çekilmiş olmalı — token'ın
    INFO loguna (örn. `HTTP Request: POST .../bot<TOKEN>/sendMessage`)
    düşmesi secret sızdırır."""
    import logging

    seen = []
    recorder = []
    _patch_client(monkeypatch, recorder)

    logger = logging.getLogger("httpx")

    class RecordingHandler(logging.Handler):
        def emit(self, record):
            seen.append(record.levelno)

    handler = RecordingHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        notifier.send_telegram_message(
            {"telegram": {"bot_token": "TOKEN", "chat_id": "42"}}, "merhaba"
        )
    finally:
        logger.removeHandler(handler)

    assert logging.INFO not in seen


# -------------------------------------------- durum --json kaynakları (atlas/
# orkestra/harita) — sözleşmedeki DAR uyarı koşulları.
#
# Kural (sözleşme.md): uyarı koşulları spam olmasın diye dardır. Sürekli >0
# olan sayaçlar (bayat_readme, kirik_link, yetim_not, kirli_repo) Telegram'a
# GİTMEZ; yalnızca panelde görünür. Aşağıdaki testler hem "gider" hem
# "gitmez" taraflarını, hem de 0/1 sınırlarını kilitler.

GECERLI_ATLAS = {
    "reachable": True,
    "son_tarama": "2026-09-30T08:00:00+00:00",
    "veri_bayat": False,
    "repo_sayisi": 12,
    "kirli_repo": 3,
    "push_bekleyen": 2,
    "push_bilinmeyen": 1,
    "bayat_readme": 4,
    "bulgu_toplam": 5,
    "bulgu_onem": {"guvenlik": 2},
    "todo_toplam": 40,
}
GECERLI_ORKESTRA = {
    "reachable": True,
    "gorev_toplam": 14,
    "gorev_durum": {"onay-bekliyor": 1, "tamamlandi": 13},
    "onay_bekleyen": 1,
    "kanitsiz_ya_da_supheli": 2,
    "basarisiz": 1,
    "kota": {"gun": "2026-09-30", "toplam_istek": 87, "uyari_sayisi": 1, "veri_var": True},
}
GECERLI_HARITA = {
    "reachable": True,
    "son_indeks": "2026-09-30T07:55:00+00:00",
    "indeks_bayat": False,
    "not_sayisi": 1234,
    "kirik_link": 3,
    "yetim_not": 21,
    "tutarlilik_uyari": None,
}


def test_atlas_saglikliyken_uyari_uretmez():
    """Sıfırla sıfır ama BIRÇOK pozitif sayaç var (kirli repo, push bekleyen,
    bayat README) — yine de mesaj ÜRETİLMEZ. Spam korumasının asıl testi."""
    summary = {"atlas": GECERLI_ATLAS, "collected_at": 1_700_000_000.0}

    assert notifier.build_alert_message(summary) is None


def test_atlas_veri_bayat_telegrama_gitmez():
    """Atlas taraması elle yenilenir: bayatlık kalıcı bir durum olabilir ve
    her cron çalışmasında mesaj üretirdi. Panelde sarı, Telegram'da sessiz."""
    summary = {"atlas": dict(GECERLI_ATLAS, veri_bayat=True), "collected_at": 1_700_000_000.0}

    assert notifier.build_alert_message(summary) is None


def test_atlas_sayilari_telegram_mesajina_girmez():
    """`bayat_readme`/`kirli_repo`/`push_bekleyen` panelde görünür ama
    Telegram mesajına GİRMEZ — başka bir kaynağın uyarısı mesaj üretse bile."""
    message = notifier.build_alert_message(
        {
            "atlas": dict(GECERLI_ATLAS, veri_bayat=True),
            "orkestra": dict(GECERLI_ORKESTRA, onay_bekleyen=1),
            "collected_at": 1_700_000_000.0,
        }
    )

    assert message is not None
    assert "atlas" not in message
    for sayi_alan_adi in ("bayat_readme", "kirli_repo", "push_bekleyen", "bulgu_toplam", "todo_toplam"):
        assert sayi_alan_adi not in message


@pytest.mark.parametrize("kaynak", ["atlas", "orkestra", "harita"])
def test_yapilandirilmamis_kaynak_uyari_uretmez(kaynak):
    """`config_yok`: opsiyonel kaynak hiç kurulmamış, "erişilemiyor" değil
    "izlenmiyor" — her cron çalışmasında mesaj gitmemeli."""
    summary = {kaynak: {"reachable": False, "error": "config_yok"}, "collected_at": 1.0}

    assert notifier.build_alert_message(summary) is None


def test_yapilandirilmis_ama_erisilemeyen_kaynak_yine_uyarir():
    """`config_yok` muafiyeti yalnızca o koda özgü: komut yok/zaman aşımı uyarır."""
    for kod in ("komut_yok", "zaman_asimi", "cikti_gecersiz", "db_yok"):
        message = notifier.build_alert_message(
            {"harita": {"reachable": False, "error": kod}, "collected_at": 1.0}
        )

        assert message is not None
        assert kod in message


def test_atlas_erisilemezse_uyari_uretir():
    message = notifier.build_alert_message(
        {"atlas": {"reachable": False, "error": "komut_yok"}, "collected_at": 1.0}
    )

    assert message is not None
    assert "atlas erişilemiyor" in message
    assert "komut_yok" in message


def test_atlas_sabit_hata_kodu_telif_edilir():
    message = notifier.build_alert_message(
        {"atlas": {"reachable": False, "error": "db_yok"}, "collected_at": 1.0}
    )

    assert message is not None
    assert "db_yok" in message


@pytest.mark.parametrize(
    "sabit_kod",
    [
        "komut_yok",
        "zaman_asimi",
        "cikti_gecersiz",
        "calistirilamadi",
        "bilinmeyen_hata",
        "db_yok",
        "indeks_yok",
        "okunamadi",
    ],
)
def test_durum_hata_kodu_oldugu_gibi_iletilir(sabit_kod):
    """Collector'ın ürettiği SABİT hata kodu mesajda olduğu gibi görünür —
    panelde gördüğü kod Telegram'da da aynıdır, teşhis için şart.
    (`config_yok` hariç: yapılandırılmamış kaynak izlenmiyor sayılır, bkz.
    `test_yapilandirilmamis_kaynak_uyari_uretmez`.)"""
    message = notifier.build_alert_message(
        {"atlas": {"reachable": False, "error": sabit_kod}, "collected_at": 1.0}
    )

    assert message is not None
    assert sabit_kod in message
    assert "atlas erişilemiyor" in message


def test_orkestra_her_ikisi_sifirken_uyari_yok():
    summary = {
        "orkestra": dict(
            GECERLI_ORKESTRA, onay_bekleyen=0, basarisiz=0, gorev_durum={"tamamlandi": 14}
        ),
        "collected_at": 1.0,
    }

    assert notifier.build_alert_message(summary) is None


def test_orkestra_onay_bekleyen_bir_kenar_deger():
    message = notifier.build_alert_message(
        {"orkestra": dict(GECERLI_ORKESTRA, onay_bekleyen=1, basarisiz=0), "collected_at": 1.0}
    )

    assert message is not None
    assert "orkestra onay bekleyen görev: 1" in message


def test_orkestra_basarisiz_tek_basina_uyari_uretmez():
    """`basarisiz` biriken bir sayaç (son koşusu başarısız her görev, iptal
    edilene dek): uyarı koşulu olsaydı tek bir eski görev her cron
    çalışmasında sonsuza dek mesaj üretirdi."""
    for sayi in (1, 5, 10**6):
        summary = {
            "orkestra": dict(GECERLI_ORKESTRA, onay_bekleyen=0, basarisiz=sayi),
            "collected_at": 1.0,
        }

        assert notifier.build_alert_message(summary) is None


def test_orkestra_onay_bekleyenle_birlikte_basarisiz_mesaja_girmez():
    message = notifier.build_alert_message(
        {"orkestra": GECERLI_ORKESTRA, "collected_at": 1.0}
    )

    assert message is not None
    assert "orkestra onay bekleyen görev: 1" in message
    assert "başarısız" not in message


def test_orkestra_kanitsiz_ve_kota_telegrama_gitmez():
    """`kanitsiz_ya_da_supheli` bir inceleme bulgusu, `kota.uyari_sayisi`
    bir uyarı sayacı — ikisi de sürekli >0 olabilir, mesaja GİRMEZ."""
    summary = {
        "orkestra": dict(
            GECERLI_ORKESTRA, onay_bekleyen=0, basarisiz=0, kanitsiz_ya_da_supheli=7
        ),
        "collected_at": 1.0,
    }

    assert notifier.build_alert_message(summary) is None


def test_orkestra_gorev_durum_etiketleri_telegrama_gitmez():
    """`gorev_durum` haritası görev durumlarını taşır; mesajda yer almaz
    (yalnızca sayı konuşulur)."""
    summary = {
        "orkestra": dict(
            GECERLI_ORKESTRA, gorev_durum={"onay-bekliyor": 4}, onay_bekleyen=0, basarisiz=0
        ),
        "collected_at": 1.0,
    }

    assert notifier.build_alert_message(summary) is None


def test_orkestra_erisilemezse_uyari_uretir():
    message = notifier.build_alert_message(
        {"orkestra": {"reachable": False, "error": "zaman_asimi"}, "collected_at": 1.0}
    )

    assert message is not None
    assert "orkestra erişilemiyor" in message
    assert "zaman_asimi" in message


def test_harita_saglikliyken_uyari_yok():
    """kırık link 3 / yetim not 21 — sürekli pozitif, mesaj YOK."""
    assert notifier.build_alert_message({"harita": GECERLI_HARITA, "collected_at": 1.0}) is None


def test_harita_indeks_bayat_telegrama_gitmez():
    """Vault'a her oturumda makine günlük yazar, indeks elle yenilenir:
    bayatlık neredeyse kalıcıdır, uyarı olsaydı sürekli mesaj üretirdi."""
    for bayat in (True, False, None):
        summary = {"harita": dict(GECERLI_HARITA, indeks_bayat=bayat), "collected_at": 1.0}

        assert notifier.build_alert_message(summary) is None


def test_harita_kirik_link_ve_yetim_not_telegrama_gitmez():
    message = notifier.build_alert_message(
        {
            "harita": dict(GECERLI_HARITA, indeks_bayat=True),
            "orkestra": dict(GECERLI_ORKESTRA, onay_bekleyen=1),
            "collected_at": 1.0,
        }
    )

    assert message is not None
    assert "kırık link" not in message
    assert "yetim" not in message
    assert "1234" not in message
    assert "harita" not in message


def test_harita_indeks_yok_uyarisi_uretir():
    message = notifier.build_alert_message(
        {"harita": {"reachable": False, "error": "indeks_yok"}, "collected_at": 1.0}
    )

    assert message is not None
    assert "harita erişilemiyor" in message
    assert "indeks_yok" in message


def test_harita_erisilemezlik_ve_bulgu_birlikte_iki_kaynakli_mesaj():
    summary = {
        "harita": {"reachable": False, "error": "indeks_yok"},
        "atlas": {"reachable": False, "error": "db_yok"},
        "collected_at": 1_700_000_000.0,
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert "harita erişilemiyor" in message
    assert "atlas erişilemiyor" in message


# ---- sınır durumları: bool sayı DEĞİLDİR, null sayı DEĞİLDİR ----------


@pytest.mark.parametrize(
    "deger",
    [True, False, None, "3", 1.5, -1, [], {}],
)
def test_orkestra_bool_null_ve_bozuk_sayilar_uyari_uretmez(deger):
    """`onay_bekleyen: true` "1 görev" demek değildir; `null` "bilinmiyor"
    demektir — ikisi de eşiği aşmış sayılmaz."""
    summary = {
        "orkestra": dict(GECERLI_ORKESTRA, onay_bekleyen=deger, basarisiz=0),
        "collected_at": 1.0,
    }

    assert notifier.build_alert_message(summary) is None


def test_orkestra_bool_ve_null_basarisiz_uyari_uretmez():
    summary = {
        "orkestra": dict(GECERLI_ORKESTRA, onay_bekleyen=0, basarisiz=True),
        "collected_at": 1.0,
    }

    assert notifier.build_alert_message(summary) is None


def test_durum_kaynaklari_reachable_anahtari_yoksa_uyari_uretmez():
    """`reachable` alanı yoksa "erişilemiyor" varsayılmamalı — sözleşme
    başarıda `reachable: True` döner; eksikse belirsizliktir, uyarı değil."""
    summary = {"atlas": {"veri_bayat": True}, "collected_at": 1.0}

    assert notifier.build_alert_message(summary) is None


# ---- mesaj tavanı: durum satırları da sınırlanmalı ---------------------


def test_durum_lines_with_both_sources_keep_message_within_limit():
    """Üç kaynak da sorunlu — mesaj yine de MAX_MESSAGE_CHARS altında
    kalmalı (durum satırları da tavana tabidir)."""
    summary = {
        "atlas": {"reachable": False, "error": "db_yok"},
        "orkestra": dict(GECERLI_ORKESTRA, onay_bekleyen=10**6, basarisiz=10**6),
        "harita": {"reachable": False, "error": "indeks_yok"},
        "collected_at": 1_700_000_000.0,
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    assert len(message) <= notifier.MAX_MESSAGE_CHARS


def test_durum_lines_alone_produce_valid_header():
    """Yalnızca durum kaynakları sorunluysa header düzgün üretilir."""
    message = notifier.build_alert_message({"orkestra": dict(GECERLI_ORKESTRA, onay_bekleyen=1)})

    assert message is not None
    assert message.startswith("⚠️ kule uyarısı")


def test_durum_lines_dont_enter_source_quota():
    """Durum satırları bakım gibi kaynak kotasına girmez — ayrı satır
    listesi olarak eklenir, `MAX_LISTED_SOURCES` sayımına karışmaz."""
    summary = {
        "atlas": dict(GECERLI_ATLAS, veri_bayat=True),
        "orkestra": dict(GECERLI_ORKESTRA, onay_bekleyen=2, basarisiz=1),
        "harita": {"reachable": False, "error": "indeks_yok"},
        "collected_at": 1_700_000_000.0,
    }

    message = notifier.build_alert_message(summary)

    assert message is not None
    lines = message.split("\n")
    # header (erişilemeyen kaynak) + orkestra satırı + zaman damgası
    assert lines[0].startswith("⚠️ kule uyarısı")
    assert "harita erişilemiyor" in lines[0]
    assert "orkestra onay bekleyen görev: 2" in lines
    assert not any("başarısız" in satir for satir in lines)
    assert not any("bayat" in satir for satir in lines)
