"""app/collectors/bagimlilik_status.py testleri. Gerçek dosya sistemi
(tmp_path) üzerinde GERÇEK rapor dosyaları kurulur, mock yok.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.collectors import bagimlilik_status
from app.collectors import durum_status


def _denetim(kaynak="requirements.txt", durum="temiz", kritik=0, yuksek=0, toplam=0):
    """Sözleşmeye uyan tek bir denetim. Testler tek alanı bozarak
    "geçersiz çıktı" tarafını üretir."""
    return {
        "ekosistem": "pip",
        "kaynak": kaynak,
        "durum": durum,
        "neden": None,
        "sayilar": {
            "kritik": kritik,
            "yuksek": yuksek,
            "orta": 0,
            "dusuk": 0,
            "bilinmiyor": 0,
        },
        "toplam": toplam,
        "aciklar": [],
        "ayrinti": None,
    }


def _rapor(repolar, tarih="2026-10-02T22:42:46+00:00", surum=1):
    return {"surum": surum, "tarih": tarih, "repolar": repolar}


def _yaz(tmp_path: Path, payload, *, ham=None) -> str:
    """Rapor dosyasını yazar ve config parçasını döner."""
    yol = tmp_path / "son.json"
    yol.write_text(ham if ham is not None else json.dumps(payload), encoding="utf-8")
    return {"bagimlilik": {"dosya": str(yol)}}


# ------------------------------------------------------- eksik/bozuk config


def test_collect_missing_config_returns_config_yok():
    """`bagimlilik` bloğu hiç yazılmamışsa hiç dosya denemez."""
    result = bagimlilik_status.collect({})

    assert result == {"reachable": False, "error": "config_yok"}


def test_collect_empty_path_returns_config_yok():
    for cfg in (
        {"bagimlilik": {"dosya": ""}},
        {"bagimlilik": {"dosya": "   "}},
        {"bagimlilik": {}},
        {"bagimlilik": 5},
        {"bagimlilik": "~/x.json"},
    ):
        result = bagimlilik_status.collect(cfg)

        assert result == {"reachable": False, "error": "config_yok"}


def test_collect_bool_path_returns_config_yok(tmp_path):
    """`dosya: true` geçerli YAML'dir ama bir yol değildir."""
    result = bagimlilik_status.collect({"bagimlilik": {"dosya": True}})

    assert result == {"reachable": False, "error": "config_yok"}


# ------------------------------------------------------------ okunamayan


def test_collect_missing_file_returns_okunamadi(tmp_path):
    """Henüz hiç tarama yapılmamış: rapor dosyası yok."""
    cfg = {"bagimlilik": {"dosya": str(tmp_path / "yok.json")}}

    result = bagimlilik_status.collect(cfg)

    assert result == {"reachable": False, "error": "okunamadi"}


def test_collect_directory_instead_of_file_returns_okunamadi(tmp_path):
    """Yol bir dizine işaret ederse okuma hatası — sabit kod, yol dışarı çıkmaz."""
    dizin = tmp_path / "klasor"
    dizin.mkdir()

    result = bagimlilik_status.collect({"bagimlilik": {"dosya": str(dizin)}})

    assert result == {"reachable": False, "error": "okunamadi"}


def test_collect_error_never_leaks_path_or_raw_text(tmp_path):
    """Hata dönüşünde dosya YOLU ya da ham metin ASLA bulunmaz."""
    yol = tmp_path / "son.json"
    yol.write_text("{bozuk json", encoding="utf-8")

    result = bagimlilik_status.collect({"bagimlilik": {"dosya": str(yol)}})

    assert set(result) == {"reachable", "error"}
    assert str(tmp_path) not in json.dumps(result, ensure_ascii=False)
    assert str(yol) not in json.dumps(result, ensure_ascii=False)
    assert "bozuk json" not in json.dumps(result, ensure_ascii=False)


# -------------------------------------------------------------- bozuk çıktı


def test_collect_broken_json_returns_cikti_gecersiz(tmp_path):
    result = bagimlilik_status.collect(_yaz(tmp_path, None, ham="{bozuk"))

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


def test_collect_non_object_json_returns_cikti_gecersiz(tmp_path):
    for ham in ("[]", "5", '"metin"', "null"):
        result = bagimlilik_status.collect(_yaz(tmp_path, None, ham=ham))

        assert result == {"reachable": False, "error": "cikti_gecersiz"}


def test_collect_wrong_version_returns_cikti_gecersiz(tmp_path):
    """`surum` bir sürüm NUMARASIdır: `true`/`1.0` da `== 1` doğrudur."""
    for surum in (2, 0, True, 1.0, None, "1"):
        cfg = _yaz(tmp_path, _rapor([], surum=surum))

        result = bagimlilik_status.collect(cfg)

        assert result == {"reachable": False, "error": "cikti_gecersiz"}


def test_collect_missing_repolar_returns_cikti_gecersiz(tmp_path):
    cfg = _yaz(tmp_path, {"surum": 1, "tarih": "2026-10-02T22:42:46+00:00"})

    result = bagimlilik_status.collect(cfg)

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


def test_collect_repolar_not_a_list_returns_cikti_gecersiz(tmp_path):
    cfg = _yaz(tmp_path, _rapor({"ad": "x"}))

    result = bagimlilik_status.collect(cfg)

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


def test_collect_repo_without_denetimler_returns_cikti_gecersiz(tmp_path):
    cfg = _yaz(tmp_path, _rapor([{"yol": "C:/x", "ad": "x", "desteklenmeyen": []}]))

    result = bagimlilik_status.collect(cfg)

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


def test_collect_denetim_not_an_object_returns_cikti_gecersiz(tmp_path):
    cfg = _yaz(tmp_path, _rapor([{"ad": "x", "denetimler": ["requirements.txt"]}]))

    result = bagimlilik_status.collect(cfg)

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


def test_collect_missing_durum_returns_cikti_gecersiz(tmp_path):
    denetim = _denetim()
    del denetim["durum"]
    cfg = _yaz(tmp_path, _rapor([{"ad": "x", "denetimler": [denetim]}]))

    result = bagimlilik_status.collect(cfg)

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


@pytest.mark.parametrize("bozuk", [None, True, -1, "3", 1.5])
def test_collect_bad_count_field_returns_cikti_gecersiz(tmp_path, bozuk):
    """Sayı alanları int>=0 olmalı; `bool` bir sayı DEĞİLDİR."""
    denetim = _denetim(durum="acik", kritik=1)
    denetim["sayilar"]["kritik"] = bozuk
    cfg = _yaz(tmp_path, _rapor([{"ad": "x", "denetimler": [denetim]}]))

    result = bagimlilik_status.collect(cfg)

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


def test_collect_bool_total_returns_cikti_gecersiz(tmp_path):
    """`{"toplam": true}` geçerli JSON'dur ama "1 bulgu" demek değildir."""
    denetim = _denetim(durum="acik", toplam=3)
    denetim["toplam"] = True
    cfg = _yaz(tmp_path, _rapor([{"ad": "x", "denetimler": [denetim]}]))

    result = bagimlilik_status.collect(cfg)

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


def test_collect_missing_sayilar_returns_cikti_gecersiz(tmp_path):
    denetim = _denetim()
    del denetim["sayilar"]
    cfg = _yaz(tmp_path, _rapor([{"ad": "x", "denetimler": [denetim]}]))

    result = bagimlilik_status.collect(cfg)

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


# ------------------------------------------------------------------ geçerli


def test_collect_valid_report_counts_manually(tmp_path):
    """Sayılar ELLE hesaplanır (dönüşümün kendisi doğrulanmaz):
      repo_sayisi   = 2
      acikli_repo   = 2      (her iki repoda en az bir "acik" denetim var)
      kritik_yuksek = 2 + 1 = 3   (YALNIZCA "acik" denetimlerin kritik+yuksek'i)
      toplam_acik   = 5 + 1 + 1 = 7   (HER denetimin `toplam`ı, durumdan bağımsız)
      denetlenemedi = 1
    """
    rapor = _rapor(
        [
            {
                "yol": "C:/x",
                "ad": "bir",
                "desteklenmeyen": [],
                "denetimler": [
                    _denetim("requirements.txt", "acik", kritik=2, toplam=5),
                    _denetim("pyproject.toml", "denetlenemedi", toplam=1),
                ],
            },
            {
                "yol": "C:/y",
                "ad": "iki",
                "desteklenmeyen": ["Cargo.lock"],
                "denetimler": [
                    _denetim("package-lock.json", "acik", yuksek=1, toplam=1),
                ],
            },
        ]
    )

    result = bagimlilik_status.collect(_yaz(tmp_path, rapor))

    assert result == {
        "reachable": True,
        "son_tarama": "2026-10-02T22:42:46+00:00",
        "repo_sayisi": 2,
        "acikli_repo": 2,
        "kritik_yuksek": 3,
        "toplam_acik": 7,
        "denetlenemedi": 1,
    }


def test_collect_all_clean_report_is_all_zero(tmp_path):
    """Gerçek araç çıktısının ilk örneği: tek repo, üç temiz denetim."""
    rapor = _rapor(
        [
            {
                "yol": "C:/x",
                "ad": "danis",
                "denetimler": [_denetim(k) for k in ("requirements-dev.txt", "requirements.txt", "pyproject.toml")],
                "desteklenmeyen": [],
            }
        ]
    )

    result = bagimlilik_status.collect(_yaz(tmp_path, rapor))

    assert result["reachable"] is True
    assert result["repo_sayisi"] == 1
    assert result["acikli_repo"] == 0
    assert result["kritik_yuksek"] == 0
    assert result["toplam_acik"] == 0
    assert result["denetlenemedi"] == 0


def test_collect_empty_repo_list_is_zero_not_error(tmp_path):
    """Tarama yapıldı ama hiç repo bulunmadı: bu GEÇERLİ bir durumdur —
    `acikli_repo: 0`, hata değil."""
    result = bagimlilik_status.collect(_yaz(tmp_path, _rapor([])))

    assert result["reachable"] is True
    assert result["repo_sayisi"] == 0
    assert result["acikli_repo"] == 0


def test_collect_counts_repo_once_even_with_two_open_audits(tmp_path):
    """`acikli_repo` REPO sayacı, denetim sayacı değil."""
    rapor = _rapor(
        [
            {
                "ad": "bir",
                "denetimler": [
                    _denetim("requirements.txt", "acik", kritik=1, toplam=1),
                    _denetim("pyproject.toml", "acik", kritik=1, toplam=1),
                ],
            }
        ]
    )

    result = bagimlilik_status.collect(_yaz(tmp_path, rapor))

    assert result["acikli_repo"] == 1
    assert result["kritik_yuksek"] == 2
    assert result["toplam_acik"] == 2


def test_collect_orta_dusuk_counts_do_not_reach_kritik_yuksek(tmp_path):
    """`kritik_yuksek` SADECE kritik+yüksek'tir; orta/düşük yalnızca toplama girer."""
    rapor = _rapor(
        [
            {
                "ad": "bir",
                "denetimler": [_denetim(durum="acik", kritik=0, yuksek=0, toplam=9)],
            }
        ]
    )
    rapor["repolar"][0]["denetimler"][0]["sayilar"]["orta"] = 9

    result = bagimlilik_status.collect(_yaz(tmp_path, rapor))

    assert result["kritik_yuksek"] == 0
    assert result["toplam_acik"] == 9


def test_collect_bad_timestamp_is_none_not_raw_text(tmp_path):
    """Desene uymayan `tarih` -> `null` ("bilinmiyor"), ham metin değil."""
    rapor = _rapor([], tarih="dün akşam 22:42 civarı")

    result = bagimlilik_status.collect(_yaz(tmp_path, rapor))

    assert result["son_tarama"] is None


def test_collect_missing_timestamp_is_none(tmp_path):
    rapor = _rapor([])
    del rapor["tarih"]

    result = bagimlilik_status.collect(_yaz(tmp_path, rapor))

    assert result["son_tarama"] is None


def test_collect_never_raises_on_broken_config_type(tmp_path):
    """Yapılandırılmamış ya da bozuk tipli config collector'ı düşürmez."""
    for cfg in ({"bagimlilik": None}, {"bagimlilik": ["liste"]}, {"bagimlilik": {"dosya": 5}}):
        result = bagimlilik_status.collect(cfg)

        assert result == {"reachable": False, "error": "config_yok"}


# ------------------------------------------------------------- şema kilidi


def test_collect_success_keys_are_exactly_the_contract(tmp_path):
    """Şema sabitlemesi: erişilebilir dönüşün alan kümesi TAM olarak sözleşme.
    Yeni alan eklenirse/çıkarsa burada yakalanır (panel bu alanlara göre
    render ediyor, sessizce eksik kalan bir `<dd>` ekranda boş kalır)."""
    rapor = _rapor([{"ad": "x", "denetimler": [_denetim(durum="acik", kritik=1, toplam=2)]}])

    result = bagimlilik_status.collect(_yaz(tmp_path, rapor))

    assert set(result.keys()) == {
        "reachable",
        "son_tarama",
        "repo_sayisi",
        "acikli_repo",
        "kritik_yuksek",
        "toplam_acik",
        "denetlenemedi",
    }


def test_error_code_okunamadi_is_a_known_fixed_code():
    """Dosya okunamadığında kullanılan kod, kaynakların SABIT hata kodu
    listesindedir — panel ve Telegram aynı dili konuşur."""
    assert bagimlilik_status.ERR_OKUNAMADI in durum_status.KNOWN_SOURCE_ERRORS
    assert bagimlilik_status.ERR_OKUNAMADI == "okunamadi"


def test_no_raw_text_or_path_escapes_on_any_error_path(tmp_path):
    """Şema dışı içerikteki gizli metin panele sızmaz."""
    gizli = "/home/kullanici/gizli/proje"
    rapor = _rapor([{"ad": "x", "yol": gizli, "denetimler": [_denetim()]}, "bozuk"])
    cfg = _yaz(tmp_path, rapor)

    result = bagimlilik_status.collect(cfg)

    assert result == {"reachable": False, "error": "cikti_gecersiz"}
    assert gizli not in json.dumps(result, ensure_ascii=False)