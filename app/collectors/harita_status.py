"""harita durumu: `harita durum --json` alt sürecinden SADECE sayıları okur.

Not BAŞLIĞI, içeriği veya dosya yolu kule'ye GİRMEZ — sözleşme bunları
yasaklıyor. `tutarlilik_uyari` yalnızca bir SAYIDIR (atlas DB'si gerektiren
karşılaştırma çalıştırılamıyorsa `null`, 0 DEĞİL).

Panel/Telegram ayrımı: `indeks_bayat` (indeks vault'tan eski, sayılar
güvenilmez) panelde sarı görünür ama Telegram'a GİTMEZ: vault'a her oturumda
makine günlük yazar, indeks elle yenilenir, bayatlık neredeyse kalıcıdır.
`kirik_link`/`yetim_not` sürekli >0 olabilir, yalnızca panelde görünür.

`harita.vault` opsiyoneldir: verilmezse harita kendi varsayılan
çözümlemesini kullanır (sözleşme §harita), o yüzden kule yanlış yol
dayatmaz — boş/bozuk bir vault değeri sessizce yok sayılır.

Bu collector mevcut `vault_status`'tan FARKLIDIR: `vault_status` Mt3Ui55OS
klasörünü doğrudan dosya sisteminden okur (wikilink/Threads.md ayrıştırma),
harita ise harita projesinin KENDİ indeksini okur. İkisi aynı veriden
besleniyor olsa da biri "dosyaları tara", diğeri "indeksi sorgula" işidir —
aynı mantığı iki kere yazmıyoruz, harita'yı subprocess ile çağırıyoruz.
"""
from __future__ import annotations

from typing import Any

from app.collectors import durum_status

KAYNAK = "harita"

# Sözleşmede her zaman int>=0 olan sayılar. `son_indeks`/`indeks_bayat`/
# `tutarlilik_uyari` aşağıda ayrı ele alınır (`indeks_bayat` bool ya da
# null, `tutarlilik_uyari` int ya da null).
REQUIRED_COUNTS = ["not_sayisi", "kirik_link", "yetim_not"]


def _collect_from_raw(raw: dict) -> dict:
    """Doğrulanmış ham çıktıdan panelin döndürdüğü sözlüğü kurar."""
    out: dict[str, Any] = {
        "son_indeks": durum_status.timestamp_or_none(raw, "son_indeks"),
        # `indeks_bayat` hesaplanamıyorsa null olabilir — panelde
        # "bilinmiyor", 0/"taze" değil.
        "indeks_bayat": durum_status.flag_or_none(raw, "indeks_bayat"),
    }
    out.update(durum_status.counts_or_none(raw, REQUIRED_COUNTS))
    # `tutarlilik_uyari`: çalıştırılamadıysa null. int>=0 değilse de null —
    # "0 uyarı" ile "karşılaştırma yapılamadı" farkı panelde önemlidir.
    out["tutarlilik_uyari"] = durum_status.count_or_none(raw, "tutarlilik_uyari")
    return out


def collect(config: dict) -> dict:
    """harita'nın durum sayılarını döner; **asla raise etmez**.

    Başarılı: `{"reachable": True, ...sayılar}`. Erişilemez: kardeş
    collector'ların `{"reachable": False, "error": <sabit kod>}` şekli.
    """
    try:
        cfg = config.get("harita")
        raw, hata = durum_status.run_durum(
            cfg,
            KAYNAK,
            # harita sözleşmesi "tipik çalışma < 2 sn" diyor; kule buna
            # güvenip 15 sn'de yüksek bir tolerans bırakıyor (ağır diskte
            # indeks okuması yavaş olabilir).
            pozisyonel=_vault_arg,
        )
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


def _vault_arg(cfg: Any) -> list[str]:
    """`harita.vault`'u opsiyonel pozisyonel argv'ye çevirir.

    Boşsa/bozuksa elenir: harita `vault` verilmezse kendi varsayılan
    çözümlemesini kullanır (sözleşme §harita), kule yanlış bir yol
    dayatmaz. `~` genişletilir — `config.yaml`'da `~/...` yazılabilir.
    """
    if not isinstance(cfg, dict):
        return []
    return durum_status._vault_arg(cfg.get("vault"))
