"""`durum --json` collector'larının test yardımcıları.

Üç collector da aynı koşucuyu (`durum_status.run_durum`) paylaştığı için
geçerli çıktı/komut yok/timeout/bozuk JSON gibi senaryolar üç dosyada da
kopyalanmaz, burada bir kez kurulur. Testler GERÇEK komut çalıştırmaz:
sahte bir Python script'i `sys.executable` ile açılır, stdout'u kontrrollü
olabilir. `sys.executable` kullanmak `python3`'e yol bulma
zorunluluğunu ortadan kaldırır (Windows'ta da çalışır).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Sözleşmedeki üç kaynağın TAM geçerli örnek çıktıları. Testler bunları
# kopyalayıp tek alanı bozarak "geçersiz çıktı" tarafını üretir.
GECERLI_ATLAS: dict[str, Any] = {
    "surum": 1,
    "kaynak": "atlas",
    "son_tarama": "2026-09-30T08:00:00+00:00",
    "veri_bayat": False,
    "repo_sayisi": 12,
    "kirli_repo": 3,
    "push_bekleyen": 2,
    "push_bilinmeyen": 1,
    "bayat_readme": 4,
    "bulgu_toplam": 5,
    "bulgu_onem": {"guvenlik": 2, "performans": 3},
    "todo_toplam": 40,
}

GECERLI_ORKESTRA: dict[str, Any] = {
    "surum": 1,
    "kaynak": "orkestra",
    "gorev_toplam": 14,
    "gorev_durum": {"onay-bekliyor": 1, "tamamlandi": 13},
    "onay_bekleyen": 1,
    "kanitsiz_ya_da_supheli": 2,
    "basarisiz": 1,
    "kota": {
        "gun": "2026-09-30",
        "toplam_istek": 87,
        "uyari_sayisi": 1,
        "veri_var": True,
    },
}

GECERLI_HARITA: dict[str, Any] = {
    "surum": 1,
    "kaynak": "harita",
    "son_indeks": "2026-09-30T07:55:00+00:00",
    "indeks_bayat": False,
    "not_sayisi": 1234,
    "kirik_link": 3,
    "yetim_not": 21,
    "tutarlilik_uyari": None,
}


def _yaz_script(tmp_path: Path, govde: str) -> Path:
    """Sahte komut script'ini yazar ve yolunu döner."""
    yol = tmp_path / "sahte_komut.py"
    yol.write_text(govde, encoding="utf-8")
    return yol


def komut_cikti_veren(tmp_path: Path, payload: Any, *, exit_code: int = 0, stderr: str = "") -> list[str]:
    """`payload`'ı stdout'a basan sahte komutun argv'sini döner.

    `payload` dict ise JSON olarak basılır; str ise HAM olarak basılır
    (bozuk JSON senaryosu için).
    """
    metin = json.dumps(payload) if not isinstance(payload, str) else payload
    govde = (
        "import sys\n"
        f"sys.stdout.write({metin!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({exit_code})\n"
    )
    return [sys.executable, str(_yaz_script(tmp_path, govde))]


def komut_hata_kodu_veren(tmp_path: Path, kaynak: str, hata_kodu: str) -> list[str]:
    """`{"surum": 1, "kaynak": ..., "hata": <kod>}` basıp 1 ile çıkan komut."""
    return komut_cikti_veren(tmp_path, {"surum": 1, "kaynak": kaynak, "hata": hata_kodu}, exit_code=1)


def komut_uyuyan(tmp_path: Path, saniye: float = 30) -> list[str]:
    """`timeout`'u tetikleyen sahte komut (kule'nin zaman aşımını beklemesi için
    gerçekten bekler — test süresi `zaman_asimi` config'iyle 1-2 saniyeye
    iner)."""
    govde = "import time\ntime.sleep(%r)\n" % saniye
    return [sys.executable, str(_yaz_script(tmp_path, govde))]


def argv_kaydet(tmp_path: Path, hedef: Path, payload: Any) -> list[str]:
    """Argv'sini, ortamını VE geçerli çıktıyı `hedef` JSON dosyasına yazan
    sahte komut.

    `KULE_TELEGRAM_*` sızıntı testi bunu kullanır: alt süreç kendi
    `os.environ`'ini diske yazar, test de oradan token'ı arar. Aynı zamanda
    stdout'a geçerli çıktı basar — kayda geçen bir komut, `collect`'in
    "başarılı" yolunu deneyebilmek için sahte çıktı da üretmelidir.
    """
    metin = json.dumps(payload) if not isinstance(payload, str) else payload
    govde = (
        "import json, os, sys\n"
        f"open({str(hedef)!r}, 'w', encoding='utf-8').write(json.dumps({{\n"
        "    'argv': sys.argv[1:],\n"
        "    'env': dict(os.environ),\n"
        "}))\n"
        f"sys.stdout.write({metin!r})\n"
    )
    return [sys.executable, str(_yaz_script(tmp_path, govde))]


# Testlerde kullanılan tanınabilir sahte "gizli" dizeler. Bunlar bir alt
# sürecin stdout/stderr'ına konduğunda collector çıktısında ARANMAMALI.
GIZLI_DIZE_STDOUT = "GIZLI-bearer-token-a1b2c3d4"
GIZLI_DIZE_STDERR = "/home/gizli/veri/tablasi.db sqlite:secret-parola"
