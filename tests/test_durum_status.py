"""atlas/orkestra/harita collector'ları: `durum --json` sözleşmesi.

Gerçek atlas/orkestra/harita komutu ÇALIŞTIRILMAZ, gerçek ağ ve gerçek
Telegram YOK. Tüm alt süreç çağrıları `tests/durum_helpers.py`'deki sahte
Python script'leriyle yapılır (veya monkeypatch'lenir), böylece testler bu
makinede hazır olmayan araçlara da aynı sonucu verir.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.collectors import atlas_status, durum_status, harita_status, orkestra_status
from tests.durum_helpers import (
    GECERLI_ATLAS,
    GECERLI_HARITA,
    GECERLI_ORKESTRA,
    GIZLI_DIZE_STDERR,
    GIZLI_DIZE_STDOUT,
    argv_kaydet,
    komut_cikti_veren,
    komut_hata_kodu_veren,
    komut_uyuyan,
)

# Üç collector aynı doğrulama sözleşmesini paylaşır; ortak senaryolar
# parametrik olarak hepsinde çalıştırılır.
KAYNAKLAR = [
    pytest.param(atlas_status, "atlas", GECERLI_ATLAS, id="atlas"),
    pytest.param(orkestra_status, "orkestra", GECERLI_ORKESTRA, id="orkestra"),
    pytest.param(harita_status, "harita", GECERLI_HARITA, id="harita"),
]


# ------------------------------------------------------------ geçerli çıktı


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_gecerli_cikti_cozulur(collector_modul, kaynak, gecerli, tmp_path):
    result = collector_modul.collect({kaynak: {"komut": komut_cikti_veren(tmp_path, gecerli)}})

    assert result["reachable"] is True
    # `surum`/`kaynak` panelde anlamsız; iç çıktı olduğu gibi sızmamalı
    assert "surum" not in result
    assert "kaynak" not in result
    # hiçbir alan gizli metin taşımamalı
    assert GIZLI_DIZE_STDOUT not in json.dumps(result)


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_kaynağin_kendi_hata_kodu_iletilir(collector_modul, kaynak, gecerli, tmp_path):
    """Sözleşme §2: hata durumunda da JSON basılır ve sabit kod tanınır."""
    result = collector_modul.collect(
        {kaynak: {"komut": komut_hata_kodu_veren(tmp_path, kaynak, "db_yok")}}
    )

    assert result == {"reachable": False, "error": "db_yok"}


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
@pytest.mark.parametrize("kod", ["db_yok", "indeks_yok", "okunamadi", "sema_eski", "yol_gecersiz"])
def test_kaynaklarin_gercek_hata_kodlari_taninir(collector_modul, kaynak, gecerli, kod, tmp_path):
    """Araçların gerçekte döndürdüğü sabit kodlar (orkestra `sema_eski`/
    `yol_gecersiz` dahil) `bilinmeyen_hata`ya düşmemeli — teşhis kaybolurdu."""
    result = collector_modul.collect(
        {kaynak: {"komut": komut_hata_kodu_veren(tmp_path, kaynak, kod)}}
    )

    assert result == {"reachable": False, "error": kod}


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_bilinmeyen_hata_kodu_icerigi_kopyalanmaz(collector_modul, kaynak, gecerli, tmp_path):
    """SABİT listede olmayan bir hata kodu İÇERİĞİYLE iletilmez — ne olursa
    olsun `bilinmeyen_hata` yazılır. Aksi halde kaynak, istemeden istisna
    metnini panele/Telegram'a taşıyabilirdi."""
    result = collector_modul.collect(
        {
            kaynak: {
                "komut": komut_hata_kodu_veren(
                    tmp_path, kaynak, f"SQLITE C:/gizli/yol.db: {GIZLI_DIZE_STDERR}"
                )
            }
        }
    )

    assert result == {"reachable": False, "error": "bilinmeyen_hata"}
    assert GIZLI_DIZE_STDERR not in json.dumps(result)


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_bilinmeyen_alanlar_yok_sayilir(collector_modul, kaynak, gecerli, tmp_path):
    """Sözleşme §7 "yeni alan eklenebilir" — kule yeni alanı görmezden
    gelmeli, mevcut alanları doğrulamaya devam etmeli."""
    payload = dict(gecerli)
    payload["gelecekteki_yeni_alan"] = {"icin": 5}
    payload["baska_yeni_alan"] = "serbest metin"

    result = collector_modul.collect({kaynak: {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result["reachable"] is True
    assert "gelecekteki_yeni_alan" not in result
    assert "baska_yeni_alan" not in result


# --------------------------------------------------------------- config yok


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
@pytest.mark.parametrize(
    "config",
    [
        pytest.param({}, id="config-bos"),
        pytest.param({"": {}}, id="config-anahtari-yok"),
    ],
)
def test_config_eksik_hic_surec_denemez(collector_modul, kaynak, gecerli, config, monkeypatch):
    """Config'de komut yoksa hiç süreç BAŞLATILMAZ."""
    patlayan = pytest.fail

    def _olmamali(*a, **kw):
        return patlayan("config eksikken subprocess çağrılmamalı")

    monkeypatch.setattr(durum_status.subprocess, "run", _olmamali)

    result = collector_modul.collect(config)

    assert result == {"reachable": False, "error": "config_yok"}


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
@pytest.mark.parametrize(
    "blok",
    [
        pytest.param({"komut": ""}, id="bos-string"),
        pytest.param({"komut": "   "}, id="bosluklu-string"),
        pytest.param({"komut": []}, id="bos-liste"),
        pytest.param({"komut": 5}, id="sayi-komut"),
        pytest.param({"komut": None}, id="null-komut"),
        pytest.param({"komut": True}, id="bool-komut"),
        pytest.param({"komut": {"a": 1}}, id="sozluk-komut"),
    ],
)
def test_bozuk_komut_configi_dusmez(collector_modul, kaynak, gecerli, blok):
    """Bozuk tipli `komut` collector'ı düşürmez, sabit kod döner."""
    result = collector_modul.collect({kaynak: blok})

    assert result == {"reachable": False, "error": "config_yok"}


# ------------------------------------------------------------ komut / süreç


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_komut_bulunamazsa_sabit_kod(collector_modul, kaynak, gecerli):
    """PATH'te olmayan bir komut: `FileNotFoundError` yakalanır, mesajı
    değil SABİT kod döner."""
    result = collector_modul.collect(
        {kaynak: {"komut": ["bu-komut-kule-ortaminda-yok-12345"]}}
    )

    assert result == {"reachable": False, "error": "komut_yok"}


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_zaman_asimi_sabit_kod(collector_modul, kaynak, gecerli, tmp_path):
    result = collector_modul.collect(
        {kaynak: {"komut": komut_uyuyan(tmp_path), "zaman_asimi": 0.4}}
    )

    assert result == {"reachable": False, "error": "zaman_asimi"}


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_sifirdan_farkli_cikis_kodu_gecersiz(collector_modul, kaynak, gecerli, tmp_path):
    """Çıkış kodu != 0 VE stdout geçerli değilse `cikti_gecersiz`.

    (Geçerli JSON basıp 1 dönmek sözleşmeye UYAR: bu `hata` yüküdür ve
    `test_kaynağin_kendi_hata_kodu_iletilir` ile ayrı test edilir.)"""
    result = collector_modul.collect(
        {kaynak: {"komut": komut_cikti_veren(tmp_path, "çökme: patlama izi", exit_code=3)}}
    )

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_nonzero_exit_stderr_panele_sizmaz(collector_modul, kaynak, gecerli, tmp_path):
    """stderr'deki tanınabilir gizli dize çıktıda ARANMAMALI."""
    result = collector_modul.collect(
        {
            kaynak: {
                "komut": komut_cikti_veren(
                    tmp_path, f"Traceback: {GIZLI_DIZE_STDERR}", exit_code=2, stderr=GIZLI_DIZE_STDERR
                )
            }
        }
    )

    assert result == {"reachable": False, "error": "cikti_gecersiz"}
    assert GIZLI_DIZE_STDERR not in json.dumps(result)


# -------------------------------------------------------------- bozuk çıktı


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
@pytest.mark.parametrize(
    "bozuk",
    [
        pytest.param("bu json degil", id="json-degil"),
        pytest.param("[1, 2, 3]", id="dizi"),
        pytest.param('"bir metin"', id="skaler"),
        pytest.param("null", id="json-null"),
        pytest.param("42", id="sayi"),
        pytest.param("", id="bos-cikti"),
        pytest.param('{"surum": 1, "kaynak": "x"', id="yarim-json"),
    ],
)
def test_gecersiz_json_sabit_kod(collector_modul, kaynak, gecerli, bozuk, tmp_path):
    result = collector_modul.collect({kaynak: {"komut": komut_cikti_veren(tmp_path, bozuk)}})

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
@pytest.mark.parametrize("surum", [0, 2, "1", None, True, False, 1.0])
def test_wrong_surum_gecersiz(collector_modul, kaynak, gecerli, surum, tmp_path):
    """`surum` daima tam sayı 1 olmalı. `True`/`1.0` da reddedilir:
    JSON'da `true` yazan bir kaynak "1" demek değildir."""
    payload = dict(gecerli)
    payload["surum"] = surum

    result = collector_modul.collect({kaynak: {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_wrong_kaynak_gecersiz(collector_modul, kaynak, gecerli, tmp_path):
    """`kaynak` alanı KENDİ adı olmalı — komut yanlış araca bakan bir
    yapılandırmada (ya da config karışmışsa) yanlış kaynağın sayıları
    kule'ye sızmamalı."""
    yanlis = {"atlas": "orkestra", "orkestra": "harita", "harita": "atlas"}[kaynak]
    payload = dict(gecerli, kaynak=yanlis)

    result = collector_modul.collect({kaynak: {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
@pytest.mark.parametrize(
    "bozuk_kaynak",
    [pytest.param(k, id=f"kaynak={k!r}") for k in ("ATLAS", "Atlas", "atlas ", "", None, 1, True)],
)
def test_kaynak_alani_gecersiz(collector_modul, kaynak, gecerli, bozuk_kaynak, tmp_path):
    payload = dict(gecerli, kaynak=bozuk_kaynak)

    result = collector_modul.collect({kaynak: {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


# --------------------------------------------------------- sayı doğrulaması


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
@pytest.mark.parametrize("bozuk_sayi", [True, False, -1, -100, 1.5, "3", None, [1], {}])
def test_zorunlu_sayi_alani_bozuksa_gecersiz(
    collector_modul, kaynak, gecerli, bozuk_sayi, tmp_path
):
    """Sayı alanları int>=0 olmalı. `True` int'in alt türü olduğu için
    YANLIŞLIKLA "1 sayılır" — o yüzden ayrıca reddedilir."""
    ilk_sayi = next(
        k
        for k, v in gecerli.items()
        if isinstance(v, int) and not isinstance(v, bool) and k != "surum"
    )
    payload = dict(gecerli)
    payload[ilk_sayi] = bozuk_sayi

    result = collector_modul.collect({kaynak: {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_zorunlu_sayi_alani_eksikse_gecersiz(collector_modul, kaynak, gecerli, tmp_path):
    payload = dict(gecerli)
    ilk_sayi = next(
        k
        for k, v in gecerli.items()
        if isinstance(v, int) and not isinstance(v, bool) and k != "surum"
    )
    del payload[ilk_sayi]

    result = collector_modul.collect({kaynak: {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result == {"reachable": False, "error": "cikti_gecersiz"}


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_sifir_gecerli_bir_degerdir(collector_modul, kaynak, gecerli, tmp_path):
    """0 bir SAYIDIR, geçersiz değildir — sıfır "ölçtüm ve sıfır" demektir."""
    ilk_sayi = next(
        k
        for k, v in gecerli.items()
        if isinstance(v, int) and not isinstance(v, bool) and k != "surum"
    )
    payload = dict(gecerli)
    payload[ilk_sayi] = 0

    result = collector_modul.collect({kaynak: {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result["reachable"] is True
    assert result[ilk_sayi] == 0


# -------------------------------------------------------------- sızıntı/env


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_stdout_stderr_gizli_dizeleri_sizmaz(collector_modul, kaynak, gecerli, tmp_path):
    """Mock stdout/stderr'a konan tanınabilir sahte "gizli" dizeler çıktıda
    ARANMAMALI. Başarılı yolda da, hata yolunda da."""
    # 1) stdout geçerli, stderr kirli → yalnızca sayılar sızar
    basarili = collector_modul.collect(
        {
            kaynak: {
                "komut": komut_cikti_veren(tmp_path, gecerli, stderr=GIZLI_DIZE_STDERR)
            }
        }
    )
    assert basarili["reachable"] is True
    assert GIZLI_DIZE_STDERR not in json.dumps(basarili)
    assert GIZLI_DIZE_STDOUT not in json.dumps(basarili)

    # 2) stdout da kirli (çıktı geçersiz) → sabit kod, stdout metni sızmaz
    basarisiz = collector_modul.collect(
        {kaynak: {"komut": komut_cikti_veren(tmp_path, GIZLI_DIZE_STDOUT, exit_code=1)}}
    )
    assert basarisiz == {"reachable": False, "error": "cikti_gecersiz"}
    assert GIZLI_DIZE_STDOUT not in json.dumps(basarisiz)


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_telegram_env_alt_surece_gecirilmez(collector_modul, kaynak, gecerli, tmp_path, monkeypatch):
    """Kule'nin Telegram secret'ı alt sürecin env'inde GÖRÜNMEMELİ."""
    monkeypatch.setenv("KULE_TELEGRAM_BOT_TOKEN", GIZLI_DIZE_STDOUT)
    monkeypatch.setenv("KULE_TELEGRAM_CHAT_ID", "4242")
    # KULE_* ama Telegram olmayan bir değişken geçmeli (aşırı temizlik yapmıyoruz)
    monkeypatch.setenv("KULE_ORTAK_DEGISKEN", "gorunur")

    kayit = tmp_path / "kayit.json"
    result = collector_modul.collect({kaynak: {"komut": argv_kaydet(tmp_path, kayit, gecerli)}})

    assert result["reachable"] is True
    kayit_env = json.loads(kayit.read_text(encoding="utf-8"))["env"]
    assert "KULE_TELEGRAM_BOT_TOKEN" not in kayit_env
    assert "KULE_TELEGRAM_CHAT_ID" not in kayit_env
    assert GIZLI_DIZE_STDOUT not in json.dumps(kayit_env)
    assert kayit_env.get("KULE_ORTAK_DEGISKEN") == "gorunur"


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_alt_surec_durum_json_argumanlarini_alir(collector_modul, kaynak, gecerli, tmp_path):
    """Komut `<komut> durum --json` olarak çağrılır; harita'da vault opsiyonel
    bir pozisyonel olarak ARKAYA eklenir."""
    kayit = tmp_path / "kayit.json"
    config = {kaynak: {"komut": argv_kaydet(tmp_path, kayit, gecerli)}}
    if kaynak == "harita":
        config[kaynak]["vault"] = "~/Mt3Ui55OS"

    collector_modul.collect(config)

    argv = json.loads(kayit.read_text(encoding="utf-8"))["argv"]
    assert argv[:2] == ["durum", "--json"]
    if kaynak == "harita":
        assert argv[2] == str(Path("~/Mt3Ui55OS").expanduser())
    else:
        assert len(argv) == 2


@pytest.mark.parametrize("collector_modul, kaynak, gecerli", KAYNAKLAR)
def test_shell_false_ve_guvenli_kwargs(collector_modul, kaynak, gecerli, tmp_path, monkeypatch):
    """`subprocess.run` daima argv listesi + `shell=False` ile çağrılır;
    config'teki komut hiçbir koşulda shell metnine dönüşmez."""
    gorulen = {}

    class FakeProc:
        returncode = 0
        stdout = json.dumps(gecerli)
        stderr = ""

    def fake_run(argv, **kwargs):
        gorulen["argv"] = argv
        gorulen["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(durum_status.subprocess, "run", fake_run)

    collector_modul.collect({kaynak: {"komut": ["atlas"]}})

    assert gorulen["argv"][:3] == ["atlas", "durum", "--json"]
    assert gorulen["kwargs"]["shell"] is False
    assert isinstance(gorulen["argv"], list)
    assert gorulen["kwargs"]["capture_output"] is True
    assert gorulen["kwargs"]["text"] is True
    assert gorulen["kwargs"]["encoding"] == "utf-8"
    assert gorulen["kwargs"]["errors"] == "replace"
    assert "timeout" in gorulen["kwargs"]


# ------------------------------------------------------------------- atlas


def test_atlas_tam_sayi_haritasi_dondugunde_cozulur(tmp_path):
    result = atlas_status.collect({"atlas": {"komut": komut_cikti_veren(tmp_path, GECERLI_ATLAS)}})

    assert result["bulgu_onem"] == {"guvenlik": 2, "performans": 3}
    assert result["son_tarama"] == "2026-09-30T08:00:00+00:00"
    assert result["veri_bayat"] is False


def test_atlas_null_zaman_damgasi_bilinmiyor_kalir(tmp_path):
    """Sözleşme: tablo boşsa `son_tarama: null`, `veri_bayat: true`."""
    payload = dict(GECERLI_ATLAS, son_tarama=None, veri_bayat=True)

    result = atlas_status.collect({"atlas": {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result["son_tarama"] is None
    assert result["veri_bayat"] is True


def test_atlas_iso_disi_zaman_damgasi_sizmaz(tmp_path):
    """Desene uymayan zaman damgası ham metin olarak PANELE SIZMAZ."""
    payload = dict(GECERLI_ATLAS, son_tarama=f"{GIZLI_DIZE_STDERR} /home/gizli/traceback")

    result = atlas_status.collect({"atlas": {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result["son_tarama"] is None
    assert GIZLI_DIZE_STDERR not in json.dumps(result)


@pytest.mark.parametrize("bozuk", [1, "guvenlik", None, True, [1]])
def test_atlas_bulgu_onem_bozuksa_null(tmp_path, bozuk):
    payload = dict(GECERLI_ATLAS, bulgu_onem=bozuk)

    result = atlas_status.collect({"atlas": {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result["reachable"] is True
    # Sözlük olmayan alan `None` olur (panel "veri yok" der); sessizce {}
    # göstermek "önem sayısı yok" anlamına gelirdi.
    assert result["bulgu_onem"] is None


def test_atlas_bulgu_onem_tehlikeli_etiketler_dusurulur(tmp_path):
    """Etiketler alt süreçten gelen metindir: HTML'e basılırlar. Kontrol
    karakteri/çok uzun etiket sessizce DOM'e sızmasın."""
    payload = dict(
        GECERLI_ATLAS,
        bulgu_onem={
            "guvenlik": 2,
            "<script>alert(1)</script>": 9,
            "x" * 200: 5,
            "satır\nsonrası": 3,
            "iyi-etiket_1": 1,
            "bool_sayaç": True,
        },
    )

    result = atlas_status.collect({"atlas": {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result["bulgu_onem"] == {"guvenlik": 2, "iyi-etiket_1": 1}


# ---------------------------------------------------------------- orkestra


def test_orkestra_kota_bloku_cozulur(tmp_path):
    result = orkestra_status.collect(
        {"orkestra": {"komut": komut_cikti_veren(tmp_path, GECERLI_ORKESTRA)}}
    )

    assert result["kota"] == {
        "gun": "2026-09-30",
        "veri_var": True,
        "toplam_istek": 87,
        "uyari_sayisi": 1,
    }
    assert result["gorev_durum"] == {"onay-bekliyor": 1, "tamamlandi": 13}


def test_orkestra_kota_null_ise_kota_null(tmp_path):
    """Sözleşme: kota okunamazsa `"kota": null` — 0 DEĞİL."""
    payload = dict(GECERLI_ORKESTRA, kota=None)

    result = orkestra_status.collect(
        {"orkestra": {"komut": komut_cikti_veren(tmp_path, payload)}}
    )

    assert result["reachable"] is True
    assert result["kota"] is None


@pytest.mark.parametrize("bozuk_kota", [{"gun": "2026-09-30"}, {"toplam_istek": 1}, "okundu", 5, [1]])
def test_orkestra_bozuk_kota_null_donusu(tmp_path, bozuk_kota):
    """Yarım/bozuk kota bloğu `None` olur: kart "kota yok" deyip geçer,
    sahte bir "veri var" izlenimi bırakmaz."""
    payload = dict(GECERLI_ORKESTRA, kota=bozuk_kota)

    result = orkestra_status.collect(
        {"orkestra": {"komut": komut_cikti_veren(tmp_path, payload)}}
    )

    assert result["kota"] is None


def test_orkestra_kota_bool_sayilar_reddedilir(tmp_path):
    """`{"toplam_istek": true, ...}` — bool sayı sayılmaz, blok bozuktur."""
    payload = dict(
        GECERLI_ORKESTRA,
        kota={"gun": "2026-09-30", "toplam_istek": True, "uyari_sayisi": 1, "veri_var": True},
    )

    result = orkestra_status.collect(
        {"orkestra": {"komut": komut_cikti_veren(tmp_path, payload)}}
    )

    assert result["kota"] is None


def test_orkestra_kota_gun_disi_gun_null(tmp_path):
    payload = dict(
        GECERLI_ORKESTRA,
        kota={
            "gun": f"{GIZLI_DIZE_STDERR} 12:00",
            "toplam_istek": 3,
            "uyari_sayisi": 0,
            "veri_var": False,
        },
    )

    result = orkestra_status.collect(
        {"orkestra": {"komut": komut_cikti_veren(tmp_path, payload)}}
    )

    assert result["kota"]["gun"] is None
    assert result["kota"]["toplam_istek"] == 3
    assert GIZLI_DIZE_STDERR not in json.dumps(result)


def test_orkestra_gorev_durum_sifir_deger_tasinir(tmp_path):
    """Sözleşme: enum değerleri HER ZAMAN mevcut (0 olsa da) — 0 sayıdır."""
    payload = dict(GECERLI_ORKESTRA, gorev_durum={"onay-bekliyor": 0, "tamamlandi": 14})

    result = orkestra_status.collect(
        {"orkestra": {"komut": komut_cikti_veren(tmp_path, payload)}}
    )

    assert result["gorev_durum"] == {"onay-bekliyor": 0, "tamamlandi": 14}


# ------------------------------------------------------------------ harita


def test_harita_tutarlilik_uyari_null_kalir(tmp_path):
    """Sözleşme: atlas DB'si yoksa `tutarlilik_uyari: null` — 0 DEĞİL."""
    result = harita_status.collect(
        {"harita": {"komut": komut_cikti_veren(tmp_path, GECERLI_HARITA)}}
    )

    assert result["tutarlilik_uyari"] is None
    assert result["not_sayisi"] == 1234
    assert result["indeks_bayat"] is False


def test_harita_indeks_bayat_null_olasidir(tmp_path):
    """`indeks_bayat` hesaplanamıyorsa null olabilir."""
    payload = dict(GECERLI_HARITA, indeks_bayat=None)

    result = harita_status.collect({"harita": {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result["indeks_bayat"] is None


@pytest.mark.parametrize("bozuk", ["true", 0, 1, "evet", {}, []])
def test_harita_indeks_bayat_bool_deger_reddedilir(tmp_path, bozuk):
    """`indeks_bayat` bool ya da null olmalı — `0`/`1`/`"evet"` kabul
    edilmez (sözleşmede `true` yazılır, sayı değil)."""
    payload = dict(GECERLI_HARITA, indeks_bayat=bozuk)

    result = harita_status.collect({"harita": {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result["indeks_bayat"] is None


@pytest.mark.parametrize("bayat", [True, False])
def test_harita_indeks_bayat_bool_kabul_edilir(tmp_path, bayat):
    """`true`/`false` ikisi de geçerli — `indeks_bayat` alanı bool'dur."""
    payload = dict(GECERLI_HARITA, indeks_bayat=bayat)

    result = harita_status.collect({"harita": {"komut": komut_cikti_veren(tmp_path, payload)}})

    assert result["indeks_bayat"] is bayat


def test_harita_komut_yoksa_komut_yok_kodu():
    result = harita_status.collect({"harita": {"komut": ["harita-bu-ortamda-yok"]}})

    assert result == {"reachable": False, "error": "komut_yok"}


def test_harita_indeks_yok_kodu_iletilir(tmp_path):
    result = harita_status.collect(
        {"harita": {"komut": komut_hata_kodu_veren(tmp_path, "harita", "indeks_yok")}}
    )

    assert result == {"reachable": False, "error": "indeks_yok"}


def test_harita_vault_verilmezse_argv_eklenmez(tmp_path):
    """`vault` verilmezse harita kendi varsayılan çözümlemesini kullanır —
    kule yanlış yol dayatmaz."""
    kayit = tmp_path / "kayit.json"

    harita_status.collect({"harita": {"komut": argv_kaydet(tmp_path, kayit, GECERLI_HARITA)}})

    argv = json.loads(kayit.read_text(encoding="utf-8"))["argv"]
    assert argv == ["durum", "--json"]


@pytest.mark.parametrize("bozuk_vault", ["", "   ", None, 5, True, ["/x"]])
def test_harita_bozuk_vault_argv_eklemez(tmp_path, bozuk_vault):
    """Bozuk vault değeri hata vermez, yok sayılır — kule kendi yolunu
    dayatmaz."""
    kayit = tmp_path / "kayit.json"

    result = harita_status.collect(
        {
            "harita": {
                "komut": argv_kaydet(tmp_path, kayit, GECERLI_HARITA),
                "vault": bozuk_vault,
            }
        }
    )

    assert result["reachable"] is True
    argv = json.loads(kayit.read_text(encoding="utf-8"))["argv"]
    assert argv == ["durum", "--json"]
