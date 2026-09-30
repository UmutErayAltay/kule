"""orkestra durumu: `orkestra durum --json` alt sürecinden SADECE sayıları okur.

Görev metni, rapor, prompt, log veya görev/rapor içeriği kule'ye GİRMEZ —
sözleşme bunları yasaklıyor ve panelde de gerekmiyor. `gorev_durum` haritası
görev BAŞLIKLARINI değil, `models.Durum` enum değerlerini taşır
(`onay-bekliyor` gibi sabit etiketler), o yüzden güvenle listelenebilir.

Panel/Telegram ayrımı: yalnızca `onay_bekleyen > 0` Telegram'a gider
(`basarisiz` birikir, panelde görünür ama uyarı üretmez). `kanitsiz_ya_da_supheli` bir inceleme bulgusu, sürekli >0
olabilir — panelde görünür, uyarı üretmez.

Config bloğu tamamen opsiyoneldir: eksik/bozuk tipli olsa bile collector
düşmez, sabit bir hata kodu döner.
"""
from __future__ import annotations

from typing import Any

from app.collectors import durum_status

KAYNAK = "orkestra"

# Sözleşmede her zaman int>=0 olan sayılar. `gorev_durum` haritası,
# `kota` bloğu ve `kota.veri_var` bool'u aşağıda ayrı ele alınır.
REQUIRED_COUNTS = [
    "gorev_toplam",
    "onay_bekleyen",
    "kanitsiz_ya_da_supheli",
    "basarisiz",
]

# Kota okunabiliyorsa dönecek blok. `gun` bir gün etiketi, `toplam_istek`/
# `uyari_sayisi` sayı, `veri_var` bool. Herhangi biri bozuksa blok ya
# tamdır ya yoktur — yarım bir kota panelde yanlış bir "veri var" izlenimi
# yaratır.
KOTA_NUMBERS = ["toplam_istek", "uyari_sayisi"]


def _collect_kota(raw: dict) -> dict | None:
    """`kota` alt nesnesini doğrular; yoksa/bozuksa None (sözleşme §orkestra).

    `raw["kota"]` `None` ise "okunamadı" demektir (beklenen durum). Sözlük
    değilse ya da içindeki sayılardan biri int>=0 değilse bozuk çıktıdır:
    o zaman da None dönerüz, ki panel "kota yok" deyip sessizce geçsin
    yerine kartın gerçekten okunamadığını anlasın.
    """
    kota = raw.get("kota")
    if not isinstance(kota, dict):
        return None
    out: dict[str, Any] = {
        "gun": durum_status.date_or_none(kota.get("gun")),
        "veri_var": durum_status.flag_or_none(kota, "veri_var"),
    }
    for key in KOTA_NUMBERS:
        if not durum_status.is_count(kota.get(key)):
            return None
        out[key] = kota[key]
    return out


def _collect_from_raw(raw: dict) -> dict:
    """Doğrulanmış ham çıktıdan panelin döndürdüğü sözlüğü kurar."""
    out: dict[str, Any] = dict(durum_status.counts_or_none(raw, REQUIRED_COUNTS))
    # `gorev_durum`: sözleşmede her enum değeri HER ZAMAN mevcut (0 olsa
    # da). Harita eksikse yine de panelde anlamlı (bilinen durumlar) —
    # eksik alanı 0 doldurmak "o durumda görev yok" uydururdu, o yüzden
    # harita olduğu gibi (doğrulanmış) geçer.
    out["gorev_durum"] = durum_status.label_counts_or_none(raw, "gorev_durum")
    out["kota"] = _collect_kota(raw)
    return out


def collect(config: dict) -> dict:
    """orkestra'nın durum sayılarını döner; **asla raise etmez**.

    Başarılı: `{"reachable": True, ...sayılar}`. Erişilemez: kardeş
    collector'ların `{"reachable": False, "error": <sabit kod>}` şekli.
    """
    try:
        raw, hata = durum_status.run_durum(config.get("orkestra"), KAYNAK)
        if raw is None:
            return durum_status._fixed_error(hata or durum_status.ERR_CALISTIRILAMADI)

        kaynak_hatasi = durum_status.source_error(raw)
        if kaynak_hatasi is not None:
            return durum_status._fixed_error(kaynak_hatasi)

        for key in REQUIRED_COUNTS:
            if not durum_status.is_count(raw.get(key)):
                return durum_status._fixed_error(durum_status.ERR_CIKTI_GECERSIZ)

        return {"reachable": True, **_collect_from_raw(raw)}
    except Exception:
        return durum_status._fixed_error(durum_status.ERR_CALISTIRILAMADI)
