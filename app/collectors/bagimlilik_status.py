"""bagimlilik durumu: `~/.bagimlilik/son.json` rapor dosyasından SADECE
sayıları okur.

Aracın raporu bir dosyadır, alt süreç çıktısı DEĞİLDİR: kule dosyayı okur,
doğrular ve panele sayıları verir. Kule raporu yeniden üretmez, denetim
çalıştırmaz, hiçbir şeye müdahale etmez.

Rol ayrımı: raporun kendi per-repo/denetim ayrıntıları (paket adları,
sürümler, `aciklar`, `ayrinti`, `desteklenmeyen`, repo yolları) kule'ye
GİRMEZ — panelde gerek yok ve dosya yolu/gizli ayrıntı sızıntısı riskidir.
Kule yalnızca kaç repo'nun, kaç denetimin açık/kritik olduğunu gösterir.

Güvenlik sözleşmesi (CLAUDE.md "Kritik kurallar"): dosya yolu, ham JSON
metni ve istisna metni ASLA panele ya da Telegram'a girmez. Yalnızca
doğrulanmış sayılar ve `durum_status.py`'deki SABİT hata kodları çıkar.

Panel/Telegram ayrımı: `acikli_repo`, `kritik_yuksek`, `toplam_acik` ve
`denetlenemedi` SÜREKLİ >0 olabilen inceleme sayaçlarıdır — yalnızca
panelde görünür, Telegram uyarısı ÜRETMEZ (bkz. `notifier.py`). Rapor
hiç üretilmemişse (dosya yok) ya da yapılandırılmamışsa kaynak "izlenmiyor"
sayılır, o da uyarmaz.

Config bloğu tamamen opsiyoneldir: eksik/bozuk tipli olsa bile collector
düşmez, sabit bir hata kodu döner.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.collectors import durum_status

KAYNAK = "bagimlilik"

# Rapordaki `durum` değerlerinden kule'nin ANLAM taşıdığı ikisi. `temiz`
# yalnızca "bu denetimde bir şey yok" bilgisidir ve tek başına bir sayı
# üretmez. Başka bir değer (kaynak yeni bir durum eklerse) ne açık ne
# denetlenemedi sayılır — yarı bilgi uydurmak yerine yok sayılır.
DURUM_ACIK = "acik"
DURUM_DENETLENEMEDI = "denetlenemedi"

# Bir denetimin `sayilar` sözlüğünden okunan alanlar. Hepsi int>=0 olmalı
# (`bool` reddedilir: `{"kritik": true}` "1 kritik" demek değildir).
SAYILAR = ["kritik", "yuksek"]

# `okunamadi` bir `durum_status` sabit hata kodu DEĞİL, kaynakların kendi
# hata kodları listesindeki bir kod (atlas/orkestra/harita da dosya/veritabanı
# okuyamadığında aynı kodu döner). Dosya tabanlı okumada da AYNI kod kullanılır
# — tek dil, tek liste (test bunu `KNOWN_SOURCE_ERRORS` üyeliğiyle kilitler).
ERR_OKUNAMADI = "okunamadi"


def _sayilar(denetim: dict) -> dict[str, int] | None:
    """Bir denetimin sayı alanları; biri bile bozuksa None.

    Eksik/bozuk sayı "0" DEĞİL "bilinmiyor" olabilirdi ama burada 0'a yuvarlamak
    yanlış güvence üretir: raporun kendisi geçersiz demektir (yarım veriyle
    kart göstermektense "erişilemiyor" demek dürüst — atlas'ın zorunlu
    sayılar deseniyle aynı).
    """
    sayilar = denetim.get("sayilar")
    if not isinstance(sayilar, dict):
        return None
    out: dict[str, int] = {}
    for key in SAYILAR:
        if not durum_status.is_count(sayilar.get(key)):
            return None
        out[key] = sayilar[key]
    if not durum_status.is_count(denetim.get("toplam")):
        return None
    out["toplam"] = denetim["toplam"]
    return out


def _ozet(raw: dict) -> dict[str, Any] | None:
    """Rapor gövdesinden kule'nin gösterdiği sayıları çıkarır; şema dışıysa None.

    Sıra önemli değil, bütünlük önemli: tek bir bozuk denetim tüm çıktıyı
    geçersiz kılar (kısmi sayı "kaç açık var" sorusunu yanlış yanıtlar).
    """
    repolar = raw.get("repolar")
    if not isinstance(repolar, list):
        return None

    out: dict[str, Any] = {
        "repo_sayisi": len(repolar),
        "acikli_repo": 0,
        "kritik_yuksek": 0,
        "toplam_acik": 0,
        "denetlenemedi": 0,
    }
    for repo in repolar:
        if not isinstance(repo, dict) or not isinstance(repo.get("denetimler"), list):
            return None
        repo_acik = False
        for denetim in repo["denetimler"]:
            if not isinstance(denetim, dict):
                return None
            sayilar = _sayilar(denetim)
            if sayilar is None or not isinstance(denetim.get("durum"), str):
                return None
            durum = denetim["durum"]
            if durum == DURUM_DENETLENEMEDI:
                out["denetlenemedi"] += 1
            if durum == DURUM_ACIK:
                repo_acik = True
                out["kritik_yuksek"] += sayilar["kritik"] + sayilar["yuksek"]
            # `toplam` durumdan bağımsızdır: "temiz" bir denetimin de toplam
            # bulgusu vardır, bunlar da açık sayıya girer.
            out["toplam_acik"] += sayilar["toplam"]
        if repo_acik:
            out["acikli_repo"] += 1
    return out


def collect(config: dict) -> dict:
    """bagimlilik aracının son raporundaki sayıları döner; **asla raise etmez**.

    Başarılı: `{"reachable": True, ...sayılar}`. Erişilemez: kardeş
    collector'ların `{"reachable": False, "error": <sabit kod>}` şekli —
    yapılandırılmamışsa `config_yok`, dosya yoksa/okunamazsa `okunamadi`,
    bozuk ya da şema dışı içerikse `cikti_gecersiz`.
    """
    try:
        cfg = config.get("bagimlilik")
        cfg = cfg if isinstance(cfg, dict) else {}
        dosya = cfg.get("dosya")
        if not isinstance(dosya, str) or not dosya.strip():
            # Hiç yapılandırılmamış: hiç denemez, doğrudan sabit kod
            # (bkz. `cor_status`/`readbunny_status` deseni).
            return durum_status._fixed_error(durum_status.ERR_CONFIG_YOK)

        # `~` genişletmesini `app/config.py::_expand` yapar; burada yol
        # olduğu gibi kullanılır (hardcode yol yok).
        try:
            metin = Path(dosya.strip()).read_text(encoding="utf-8")
        except (OSError, ValueError):
            # Dosya yok, izin yok, dizin ya da UTF-8 olmayan içerik:
            # hepsi aynı sabit kod. Yol ve istisna metni ASLA dışarı çıkmaz.
            return durum_status._fixed_error(ERR_OKUNAMADI)

        try:
            raw = json.loads(metin)
        except ValueError:
            return durum_status._fixed_error(durum_status.ERR_CIKTI_GECERSIZ)
        if not isinstance(raw, dict):
            return durum_status._fixed_error(durum_status.ERR_CIKTI_GECERSIZ)

        # `surum` bir sürüm NUMARASIdır: `True == 1` ve `1.0 == 1` doğru
        # olduğu için ayrıca int ve bool değil kontrolü gerekir
        # (`durum_status._parse_payload` ile aynı gerekçe).
        surum = raw.get("surum")
        if isinstance(surum, bool) or not isinstance(surum, int) or surum != 1:
            return durum_status._fixed_error(durum_status.ERR_CIKTI_GECERSIZ)

        ozet = _ozet(raw)
        if ozet is None:
            return durum_status._fixed_error(durum_status.ERR_CIKTI_GECERSIZ)

        return {
            "reachable": True,
            # `tarih` desene uymazsa `null` ("bilinmiyor"), ham metin değil.
            "son_tarama": durum_status.timestamp_or_none(raw, "tarih"),
            **ozet,
        }
    except Exception:
        # collector asla raise etmez — bu, aggregator izolasyonunun ÜSTÜNE
        # ikinci güvenlik ağıdır (bkz. CLAUDE.md "Yeni bir collector eklerken").
        return durum_status._fixed_error(durum_status.ERR_CALISTIRILAMADI)