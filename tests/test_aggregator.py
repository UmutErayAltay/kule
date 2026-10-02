"""app/aggregator.py testleri: collect_all izolasyonu ve get_cached_summary
TTL cache mantığı. Gerçek collector'lar (git/http/db) monkeypatch ile sahte
fonksiyonlarla değiştirilir — dış dünyaya asla gidilmez.
"""
from __future__ import annotations

import pytest

from app import aggregator


@pytest.fixture(autouse=True)
def _reset_cache():
    """Modül seviyesindeki _cache global durumunu her testten önce/sonra
    temizler ki testler birbirini etkilemesin."""
    aggregator._cache["summary"] = None
    aggregator._cache["fetched_at"] = 0.0
    yield
    aggregator._cache["summary"] = None
    aggregator._cache["fetched_at"] = 0.0


def _patch_all_collectors(
    monkeypatch, git=None, cor=None, borsasite=None, readbunny=None, vault=None, maintenance=None
):
    monkeypatch.setattr(aggregator.git_status, "collect", lambda cfg: git if git is not None else [])
    monkeypatch.setattr(aggregator.cor_status, "collect", lambda cfg: cor if cor is not None else {"reachable": True})
    monkeypatch.setattr(
        aggregator.borsasite_status, "collect", lambda cfg: borsasite if borsasite is not None else {"reachable": True}
    )
    monkeypatch.setattr(
        aggregator.readbunny_status, "collect", lambda cfg: readbunny if readbunny is not None else {"reachable": True}
    )
    monkeypatch.setattr(aggregator.vault_status, "collect", lambda cfg: vault if vault is not None else {})
    monkeypatch.setattr(
        aggregator.maintenance_status, "collect", lambda cfg: maintenance if maintenance is not None else {}
    )


def test_collect_all_merges_all_sources(monkeypatch):
    _patch_all_collectors(
        monkeypatch,
        git=[{"name": "repo1", "dirty_count": 0}],
        cor={"reachable": True, "health": {"ok": True}},
        borsasite={"reachable": True},
        readbunny={"reachable": True},
        vault={"broken_link_count": 0},
        maintenance={"disk": {"full": []}},
    )

    result = aggregator.collect_all({})

    assert result["git"] == [{"name": "repo1", "dirty_count": 0}]
    assert result["cor"] == {"reachable": True, "health": {"ok": True}}
    assert result["borsasite"] == {"reachable": True}
    assert result["readbunny"] == {"reachable": True}
    assert result["vault"] == {"broken_link_count": 0}
    assert result["maintenance"] == {"disk": {"full": []}}
    assert "collected_at" in result
    assert isinstance(result["collected_at"], float)


def test_collect_all_isolates_one_failing_collector(monkeypatch):
    def boom(cfg):
        raise RuntimeError("kaboom")

    _patch_all_collectors(monkeypatch, cor={"reachable": True})
    monkeypatch.setattr(aggregator.git_status, "collect", boom)

    result = aggregator.collect_all({})

    assert result["git"] == {"error": "kaboom"}
    # diğer kaynaklar etkilenmemeli
    assert result["cor"] == {"reachable": True}
    assert result["borsasite"] == {"reachable": True}
    assert result["readbunny"] == {"reachable": True}
    assert result["vault"] == {}
    assert result["maintenance"] == {}


def test_collect_all_isolates_failing_maintenance_collector(monkeypatch):
    def boom(cfg):
        raise RuntimeError("bakım patladi")

    _patch_all_collectors(monkeypatch, cor={"reachable": True})
    monkeypatch.setattr(aggregator.maintenance_status, "collect", boom)

    result = aggregator.collect_all({})

    assert result["maintenance"] == {"error": "bakım patladi"}
    assert result["cor"] == {"reachable": True}
    assert result["git"] == []


def test_collect_all_isolates_multiple_failures_independently(monkeypatch):
    def boom_git(cfg):
        raise ValueError("git patladi")

    def boom_vault(cfg):
        raise OSError("vault patladi")

    def boom_maintenance(cfg):
        raise TimeoutError("bakım zaman aşımı")

    _patch_all_collectors(monkeypatch, cor={"reachable": True}, borsasite={"reachable": False}, readbunny={"reachable": True})
    monkeypatch.setattr(aggregator.git_status, "collect", boom_git)
    monkeypatch.setattr(aggregator.vault_status, "collect", boom_vault)
    monkeypatch.setattr(aggregator.maintenance_status, "collect", boom_maintenance)

    result = aggregator.collect_all({})

    assert result["git"] == {"error": "git patladi"}
    assert result["vault"] == {"error": "vault patladi"}
    assert result["maintenance"] == {"error": "bakım zaman aşımı"}
    assert result["cor"] == {"reachable": True}


def test_get_cached_summary_calls_collect_all_once_within_ttl(monkeypatch):
    call_count = {"n": 0}

    def fake_collect_all(config):
        call_count["n"] += 1
        return {"git": [], "n": call_count["n"]}

    monkeypatch.setattr(aggregator, "collect_all", fake_collect_all)

    first = aggregator.get_cached_summary({}, ttl_seconds=60)
    second = aggregator.get_cached_summary({}, ttl_seconds=60)

    assert call_count["n"] == 1
    assert first == second
    assert first["n"] == 1


def test_get_cached_summary_refetches_after_ttl_zero(monkeypatch):
    call_count = {"n": 0}

    def fake_collect_all(config):
        call_count["n"] += 1
        return {"n": call_count["n"]}

    monkeypatch.setattr(aggregator, "collect_all", fake_collect_all)

    first = aggregator.get_cached_summary({}, ttl_seconds=0)
    second = aggregator.get_cached_summary({}, ttl_seconds=0)

    assert call_count["n"] == 2
    assert first["n"] == 1
    assert second["n"] == 2


def test_get_cached_summary_refetches_after_ttl_expires(monkeypatch):
    call_count = {"n": 0}
    fake_time = {"t": 1000.0}

    def fake_collect_all(config):
        call_count["n"] += 1
        return {"n": call_count["n"]}

    monkeypatch.setattr(aggregator, "collect_all", fake_collect_all)
    monkeypatch.setattr(aggregator.time, "time", lambda: fake_time["t"])

    aggregator.get_cached_summary({}, ttl_seconds=10)
    assert call_count["n"] == 1

    fake_time["t"] += 5  # hala TTL icinde
    aggregator.get_cached_summary({}, ttl_seconds=10)
    assert call_count["n"] == 1

    fake_time["t"] += 20  # TTL asildi
    aggregator.get_cached_summary({}, ttl_seconds=10)
    assert call_count["n"] == 2


def test_get_cached_summary_uses_default_ttl_constant(monkeypatch):
    assert aggregator.CACHE_TTL_SECONDS == 60


# --- dalga G: atlas / orkestra ------------------------------------------
#
# `collect_all` artık DOKUZ kaynağı birleştiriyor (harita kartı panelden
# çıkarıldı; `harita_status` modülü duruyor ama aggregator'a KAYITLI DEĞİL,
# bkz. `test_harita_is_not_registered_in_aggregator`). Yeni collector'ların
# kaydı elle yapıldığı için (CLAUDE.md madde 2) burada iki şey kilitleniyor:
# (1) anahtarlar çıktıda bulunuyor, (2) biri patladığında diğerleri
# ETKİLENMİYOR.

DURUM_GEZERLI = {
    "atlas": {"reachable": True, "bulgu_toplam": 5, "veri_bayat": False},
    "orkestra": {"reachable": True, "gorev_toplam": 14, "onay_bekleyen": 0, "basarisiz": 0},
}

# Modül adı -> sonuç anahtarı. `jobs` dict'i MODÜL adıyla değil kaynak
# ADIYLA kaydedilir (`"atlas": (atlas_status.collect, ...)`); ikisi aynı
# görünse de karıştırılırsa test yanlış anahtarı arar.
DURUM_MODULLERI = {
    "atlas": "atlas_status",
    "orkestra": "orkestra_status",
}


def _patch_durum(monkeypatch, **patlayan_kaynaklar):
    """İki yeni collector'ı sahte `collect` fonksiyonlarıyla değiştirir.

    `patlayan_kaynaklar` içinde geçen KAYNAK adının collector'ı patlatır
    (izolasyon testi).
    """
    for kaynak, modul_ad in DURUM_MODULLERI.items():
        modul = getattr(aggregator, modul_ad)
        if kaynak in patlayan_kaynaklar:
            def boom(cfg, _ad=modul_ad):
                raise RuntimeError(f"{_ad} patladi")

            monkeypatch.setattr(modul, "collect", boom)
        else:
            deger = DURUM_GEZERLI[kaynak]
            monkeypatch.setattr(modul, "collect", lambda cfg, _d=deger: dict(_d))
    _patch_bagimlilik(monkeypatch)


# `bagimlilik` dosya tabanlı bir kaynak (rapor dosyasını okur), yine de
# aggregator'daki kayıt/izolasyon sözleşmesinin bir parçası: matematik
# yapmaya gerek yok, sahte `collect` yeterli. `_patch_durum` bunu da
# çağırır — testler GERÇEK dosya sistemine hiç gitmesin diye; yalnız
# izolasyon testi `patlasin=True` ile üstüne yazar.
def _patch_bagimlilik(monkeypatch, *, patlasin=False, deger=None):
    if patlasin:
        def boom(cfg):
            raise RuntimeError("bagimlilik patladi")

        monkeypatch.setattr(aggregator.bagimlilik_status, "collect", boom)
        return
    gecerli = deger if deger is not None else {
        "reachable": True,
        "repo_sayisi": 1,
        "acikli_repo": 0,
        "kritik_yuksek": 0,
        "toplam_acik": 0,
        "denetlenemedi": 0,
        "son_tarama": "2026-10-02T22:42:46+00:00",
    }
    monkeypatch.setattr(aggregator.bagimlilik_status, "collect", lambda cfg: dict(gecerli))


def test_collect_all_merges_bagimlilik_source(monkeypatch):
    _patch_all_collectors(monkeypatch)
    _patch_durum(monkeypatch)

    result = aggregator.collect_all({})

    assert result["bagimlilik"]["reachable"] is True
    assert result["bagimlilik"]["repo_sayisi"] == 1


def test_collect_all_isolates_failing_bagimlilik_collector(monkeypatch):
    """bagimlilik patladığında yalnız o anahtar düşer, diğer sekiz sağlam kalır."""
    _patch_all_collectors(monkeypatch)
    _patch_durum(monkeypatch)
    _patch_bagimlilik(monkeypatch, patlasin=True)

    result = aggregator.collect_all({})

    assert result["bagimlilik"] == {"error": "bagimlilik patladi"}
    assert result["cor"] == {"reachable": True}
    assert result["git"] == []
    assert result["atlas"] == DURUM_GEZERLI["atlas"]


def test_collect_all_registers_nine_sources(monkeypatch):
    """Dokuz anahtar: altı eski + atlas/orkestra/bagimlilik. Bir anahtar
    unutulursa ya da yanlış yazılırsa burada yakalanır."""
    _patch_all_collectors(monkeypatch)
    _patch_durum(monkeypatch)

    result = aggregator.collect_all({})

    beklenen = {
        "git",
        "cor",
        "borsasite",
        "readbunny",
        "vault",
        "maintenance",
        "atlas",
        "orkestra",
        "bagimlilik",
        "collected_at",
    }
    assert set(result) == beklenen


def test_harita_is_not_registered_in_aggregator(monkeypatch):
    """Harita kartı kaldırıldı (vault kartı tek kaynak): `collect_all` harita
    alt sürecini HİÇ başlatmamalı. Modül duruyor, yeniden eklemek için
    aggregator'a tek satır yeter."""
    _patch_all_collectors(monkeypatch)
    _patch_durum(monkeypatch)
    from app.collectors import harita_status

    def cagrilmamali(cfg):
        raise AssertionError("harita_status.collect çağrıldı")

    monkeypatch.setattr(harita_status, "collect", cagrilmamali)

    result = aggregator.collect_all({})

    assert "harita" not in result


def test_collect_all_merges_durum_sources(monkeypatch):
    _patch_all_collectors(monkeypatch)
    _patch_durum(monkeypatch)

    result = aggregator.collect_all({})

    assert result["atlas"] == DURUM_GEZERLI["atlas"]
    assert result["orkestra"] == DURUM_GEZERLI["orkestra"]


@pytest.mark.parametrize("patlayan", list(DURUM_MODULLERI))
def test_collect_all_isolates_failing_durum_collector(monkeypatch, patlayan):
    """Yeni collector'lardan biri patladığında o anahtar `{"error": ...}`
    olur, DİĞER kaynaklar etkilenmez."""
    _patch_all_collectors(monkeypatch, cor={"reachable": True}, vault={"broken_link_count": 0})
    _patch_durum(monkeypatch, **{patlayan: True})

    result = aggregator.collect_all({})

    assert result[patlayan] == {"error": f"{DURUM_MODULLERI[patlayan]} patladi"}
    # geri kalan her şey normal döner
    assert result["cor"] == {"reachable": True}
    assert result["vault"] == {"broken_link_count": 0}
    assert result["git"] == []
    assert result["borsasite"] == {"reachable": True}
    assert result["readbunny"] == {"reachable": True}
    assert result["maintenance"] == {}
    for kaynak in DURUM_MODULLERI:
        if kaynak != patlayan:
            assert result[kaynak] == DURUM_GEZERLI[kaynak]


def test_collect_all_isolates_all_durum_collectors_failing(monkeypatch):
    """İkisi birden patlasa bile altı eski kaynak sağlam kalır."""
    _patch_all_collectors(monkeypatch, cor={"reachable": True})
    _patch_durum(monkeypatch, atlas=True, orkestra=True)

    result = aggregator.collect_all({})

    for kaynak, modul_ad in DURUM_MODULLERI.items():
        assert result[kaynak] == {"error": f"{modul_ad} patladi"}
    assert result["cor"] == {"reachable": True}
    assert result["git"] == []
