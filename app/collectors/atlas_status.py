"""atlas durumu: `atlas durum --json` alt sürecinden SADECE sayıları okur.

atlas'ın kendi bulgu verisine (dosya, satır, eşleşen metin) kule hiç dokunmaz
— sözleşme bunları yasaklıyor, kule zaten yalnızca panelde bakacağı
sayıları ister. Bulgunun kendisi, repo yolları ve commit başlıkları ne stdout'a
gelirse gelsin buradan geçmez.

Panel/Telegram ayrımı: `veri_bayat` (atlas taraması eski, sayılar güvenilmez)
panelde sarı görünür ama Telegram'a GİTMEZ: tarama elle yenilenir, bayatlık
kalıcı bir durum olabilir ve her cron çalışmasında mesaj üretirdi.
`bayat_readme`/`kirli_repo` gibi sürekli >0 sayaçlar da yalnızca panelde
görünür (bkz. `notifier.py`).

Config bloğu tamamen opsiyoneldir: eksik/bozuk tipli olsa bile collector
düşmez, sabit bir hata kodu döner.
"""
from __future__ import annotations

from typing import Any

from app.collectors import durum_status

KAYNAK = "atlas"

# Sözleşmede her zaman int>=0 olan sayılar. `son_tarama` bir zaman
# damgasıdır, `veri_bayat` bool'dur — ikisi de aşağıda ayrı ele alınır.
REQUIRED_COUNTS = [
    "repo_sayisi",
    "kirli_repo",
    "push_bekleyen",
    "push_bilinmeyen",
    "bayat_readme",
    "bulgu_toplam",
    "todo_toplam",
]


def _collect_from_raw(raw: dict) -> dict:
    """Doğrulanmış ham çıktıdan panelin döndürdüğü sözlüğü kurar."""
    out: dict[str, Any] = {
        "son_tarama": durum_status.timestamp_or_none(raw, "son_tarama"),
        "veri_bayat": durum_status.flag_or_none(raw, "veri_bayat"),
    }
    out.update(durum_status.counts_or_none(raw, REQUIRED_COUNTS))
    # `bulgu_onem` haritası: sözleşmeye göre `ONEMLER` enum'undan gelen
    # kısa etiketler. Sözlük değilse bozuk çıktıdır -> None, panel "veri yok"
    # der; sessizce {} göstermek "önem sayısı yok" anlamına gelirdi.
    out["bulgu_onem"] = durum_status.label_counts_or_none(raw, "bulgu_onem")
    return out


def collect(config: dict) -> dict:
    """atlas'ın durum sayılarını döner; **asla raise etmez**.

    Başarılı: `{"reachable": True, ...sayılar}`. Erişilemez: kardeş
    collector'ların `{"reachable": False, "error": <sabit kod>}` şekli.
    """
    try:
        raw, hata = durum_status.run_durum(config.get("atlas"), KAYNAK)
        if raw is None:
            return durum_status._fixed_error(hata or durum_status.ERR_CALISTIRILAMADI)

        kaynak_hatasi = durum_status.source_error(raw)
        if kaynak_hatasi is not None:
            return durum_status._fixed_error(kaynak_hatasi)

        # Zorunlu sayılar int>=0 olmalı; ilk bozuk alan tüm çıktıyı
        # geçersiz kılar (yarım veriyle kart göstermektense "erişilemiyor"
        # demek dürüst).
        for key in REQUIRED_COUNTS:
            if not durum_status.is_count(raw.get(key)):
                return durum_status._fixed_error(durum_status.ERR_CIKTI_GECERSIZ)

        return {"reachable": True, **_collect_from_raw(raw)}
    except Exception:
        # collector asla raise etmez — bu, aggregator izolasyonunun ÜSTÜNE
        # ikinci güvenlik ağıdır (bkz. CLAUDE.md "Yeni bir collector eklerken").
        return durum_status._fixed_error(durum_status.ERR_CALISTIRILAMADI)
