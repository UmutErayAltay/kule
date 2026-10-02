"""Telegram bildirimi: collector'ların topladığı özetten kısa bir uyarı
metni üretir ve Telegram Bot API'ye gönderir.

Collector'larla aynı iki kural geçerli: (1) hiçbir fonksiyon asla raise
etmez — bağlantı hatası, 4xx/5xx, hatta beklenmedik bir tip hatası olsa
bile yalnızca False döner, (2) ağır/opsiyonel bağımlılıklar fonksiyon içinde
lazy import edilir. `httpx` burada modül seviyesinde import edilir, tıpkı
`cor_status`/`borsasite_status` gibi (zorunlu bağımlılık; lazy import edilen
şey ağır/opsiyonel olan `psycopg`).

Secret'lar dosyadan değil ortamdan akar: `bot_token`/`chat_id`
`app/config.py::load_config` tarafından env'den doldurulur, burada sadece
okunur.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

TELEGRAM_API_BASE = "https://api.telegram.org"
TIMEOUT = 10.0

# Telegram mesajı kısa kalsın: uzun hata metinleri ve çok sayıda kaynak
# bildirimi okunmaz hale getirir. Telegram'ın 4096 karakterlik tavanının
# altında kalmak için kısaltma.
MAX_ERROR_CHARS = 80
MAX_LISTED_SOURCES = 5
# Telegram sendMessage 4096 karakterlik tavanı reddeder (API hatası döner,
# bildirim sessizce kaybolur). MAX_ERROR_CHARS tek tek HATA DETAYINI kısar;
# bakım satırları ise birleştirilmiş listeden geldiği için toplam mesaj
# yine de tavanı aşabiliyordu. Son kesme burada, mesaj genelinde yapılır.
MAX_MESSAGE_CHARS = 4096

# Özette kaynak OLMAYAN anahtarlar. `collect_all` her zaman `collected_at`
# (float) ekliyor; sayısal olduğu için `error`/`reachable` kontrolünden
# geçiyor ama bir "kaynak" değil — listelenmemeli.
NON_SOURCE_KEYS = {"collected_at"}

# Bakım bulguları kaynaklar arasında sayılmaz (MAX_LISTED_SOURCES kotasına
# girmez, `hidden` sayımına katılmaz); her bulgu kendi satırında listelenir.
MAINTENANCE_KEY = "maintenance"

# `durum --json` sözleşmesini konuşan üç kaynak (atlas/orkestra/harita) ve
# dosya tabanlı `bagimlilik`. Bunlar erişilemez olduklarında
# `{"reachable": False, "error": <sabit kod>}` döndüğü için `_describe`'ın
# VAR OLAN "dict + reachable" dalı zaten onları doğru tanır — erişilememe
# cümlesi ek bir yol istemez. Ancak sayı alanları `reachable: True` iken de
# sorun bildirebilir; o dal aşağıda.
#
# SÖZLEŞME KURALI (sözleşme.md, "kule tarafı"): uyarı koşulları SPAM
# OLMAMASI İÇİN DAR TUTULUR. Yalnızca gerçek arıza/uyumsuzluk bildirilir:
#   - orkestra: onay_bekleyen > 0 (bir insanın kararı bekleniyor; karar
#               verilince kendiliğinden sıfırlanır). `basarisiz` DEĞİL:
#               biriken bir sayaç, eski tek görev sonsuza dek spam üretir.
#   - hepsi: kaynağa ERİŞİLEMEMESİ (yapılandırılmamışsa `config_yok` hariç:
#               opsiyonel kaynak, izlenmiyor demektir).
# `veri_bayat` (atlas) ve `indeks_bayat` (harita) BİLEREK Telegram'a gitmez,
# yalnızca panelde sarı görünür: atlas taraması ve harita indeksi elle
# yenilenir, vault'a ise her oturumda makine günlük yazar — bayatlık neredeyse
# kalıcı bir durumdur ve her cron çalışmasında mesaj üretirdi.
# `bayat_readme`, `push_bekleyen`, `kirli_repo` gibi SÜREKLİ >0
# olan sayaçlar da YALNIZCA panelde görünür — her koşuda 3 kırık link varsa
# her 15 dakikada bir mesaj atmak bildirimi değersiz kılar. Aynı sebeple
# `kanitsiz_ya_da_supheli`, `kota.uyari_sayisi` ve bagimlilik'ın
# `acikli_repo`/`kritik_yuksek`/`toplam_acik`/`denetlenemedi`
# sayaçları da uyarı üretmez: bunlar inceleme bulgusudur, arıza değil.

#: Kaynak -> "hiç izlenmiyor" demek olan sabit hata kodları (uyarı üretmez).
#: `config_yok` her kaynak için geçerlidir: opsiyonel blok hiç yazılmamış.
#: `okunamadi` YALNIZCA `bagimlilik` için muaftır: o kaynak raporu bir
#: DOSYADAN okur ve dosya yoksa araç henüz hiç çalışmamış demektir — kule
#: her cron koşusunda "bagimlilik erişilemiyor (okunamadi)" göndermemeli.
#: atlas/orkestra/harita'da `okunamadi` GERÇEK bir okuma hatasıdır (veritabanı
#: dosyası bozuk, indeks kilitli) ve uyarır.
YAPILANDIRILMAMIS_KODLAR: dict[str, tuple[str, ...]] = {
    "atlas": ("config_yok",),
    "orkestra": ("config_yok",),
    "harita": ("config_yok",),
    "bagimlilik": ("config_yok", "okunamadi"),
}

DURUM_SOURCES = tuple(YAPILANDIRILMAMIS_KODLAR)


def _truncate(value: Any) -> str:
    text = str(value).replace("\n", " ").strip()
    if len(text) > MAX_ERROR_CHARS:
        return text[: MAX_ERROR_CHARS - 1] + "…"
    return text


def _detail(error: Any) -> str:
    """Hata metnini mesaja iliştirir: boşsa parantez üretmez."""
    if not error:
        return ""
    return f" ({_truncate(error)})"


def _describe(name: str, value: Any) -> str | None:
    """Tek bir kaynağın durumundan bir cümle kurar; sorun yoksa None.

    Kaynakların gerçek dönüş şekilleri üç çeşit (bkz. app/collectors/*.py):
      - dict + `reachable` (cor, borsasite, readbunny, atlas/orkestra/harita)
      - dict + yalnızca `error` (vault — `reachable` alanı YOK)
      - dict listesi (git — hata repo başına, listenin kendisinde değil)

    `atlas`/`orkestra`/`harita` erişilemezlikte bu üç şeklin birincisine
    uyar (`{"reachable": False, "error": <sabit kod>}`), ama `reachable:
    True` iken sayı alanlarından DAR uyarı koşulları üretirler — o yol
    `_durum_sentences`'tedir (aşağıda).

    `maintenance` bu üç şeklin hiçbirine uymaz (disk/süreç/git alt
    bölümlerinden oluşur) ve burada değil, `_maintenance_sentences`
    içinde ele alınır.
    """
    if isinstance(value, dict):
        if value.get("reachable") is False:
            return f"{name} erişilemiyor{_detail(value.get('error'))}"
        error = value.get("error")
        if error:
            return f"{name} hata veriyor{_detail(error)}"
        return None

    if isinstance(value, list):
        failed = [
            item.get("name") or "?"
            for item in value
            if isinstance(item, dict) and item.get("error")
        ]
        if not failed:
            return None
        return f"{name}: {len(failed)} repo okunamadı{_detail(', '.join(failed))}"

    return None


def _maintenance_sentences(value: Any) -> list[str]:
    """Bakım bulgularını ayrı ayrı cümlelere çevirir.

    `maintenance` bir kaynak değil, üç alt bölümden oluşan bir bulgu
    listesi: dolu disk, unutulmuş süreçler, eski kirli repo. Her biri
    ayrı satır olmalı — hepsi tek cümlede birleştirilince "disk dolu,
    3 süreç unutulmuş, 1 repo eski" gibi okunması zor bir yığın olur.
    Yine bir kaynak gibi, hiçbir şey bulunamazsa liste boş kalır.
    """
    if not isinstance(value, dict):
        return []

    sentences: list[str] = []

    disk = value.get("disk")
    if isinstance(disk, dict):
        full = disk.get("full") or []
        if full:
            worst = full[0] if isinstance(full[0], dict) else {}
            percent = worst.get("percent")
            detail = f" (%{percent})" if percent is not None else ""
            where = worst.get("path") or "bilinmeyen yol"
            sentences.append(f"disk dolu: {_truncate(where)}{detail}")
        error = disk.get("error")
        if error:
            sentences.append(f"disk okunamadı{_detail(error)}")

    processes = value.get("stale_processes")
    if isinstance(processes, dict):
        items = processes.get("items") or []
        if items:
            names: dict[str, int] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "?")
                names[name] = names.get(name, 0) + 1
            listed = ", ".join(
                f"{_truncate(n)}x{c}" if c > 1 else _truncate(n) for n, c in names.items()
            )
            sentences.append(f"uzun süredir çalışan süreçler: {listed}")
        error = processes.get("error")
        if error:
            sentences.append(f"süreçler okunamadı{_detail(error)}")

    stale_git = value.get("stale_git")
    if isinstance(stale_git, dict):
        items = stale_git.get("items") or []
        if items:
            names = [
                _truncate(i.get("name") or "?") for i in items if isinstance(i, dict)
            ]
            sentences.append(f"eski commitlenmemiş değişiklik: {', '.join(names)}")
        error = stale_git.get("error")
        if error:
            sentences.append(f"bakım kontrolü hata verdi{_detail(error)}")

    return sentences


def _durum_sentences(name: str, value: Any) -> list[str]:
    """`durum --json` kaynaklarının DAR uyarı koşullarını cümleye çevirir.

    `_describe` yalnızca ERİŞİLEMEZLİĞİ yakalar (`reachable: False` veya
    `error`); bu kaynaklar `reachable: True` olurken de sorun bildirebilir
    (bayat veri, tıkanan akış). O yüzden sayı alanları burada, ayrı bir
    yolda kontrol edilir.

    Mesajlarda YALNIZCA SAYI ve kısa etiket vardır — kaynağın hata metni,
    komut yolu ya da alt sürecin stderr'i zaten collector'da sabit kodlara
    indirgenmişti, buraya da sızmaz.

    Tek koşul: orkestra `onay_bekleyen > 0` (`basarisiz`, atlas `veri_bayat`,
    harita `indeks_bayat` ve `bagimlilik`ın dört sayacı uyarı ÜRETMEZ;
    gerekçe modül başındaki not).

    `null` alanlar SAYILMAZ: bilinmeyen bir sayı "sıfır" da "sorun var" da
    demek değildir.
    """
    if not isinstance(value, dict) or value.get("reachable") is not True:
        return []

    sentences: list[str] = []
    if name == "orkestra":
        # `basarisiz` BİLEREK yok: son koşusu başarısız olan görev sayısı
        # birikir ve bir insan iptal etmedikçe hiç düşmez; koşul olsaydı tek
        # bir eski görev her cron çalışmasında sonsuza dek mesaj üretirdi.
        # `onay_bekleyen` ise insan karar verince kendiliğinden sıfırlanır.
        onay = value.get("onay_bekleyen")
        if _is_positive_int(onay):
            sentences.append(f"orkestra onay bekleyen görev: {onay}")
    return sentences


def _yapilandirilmamis(name: str, value: Any) -> bool:
    """Opsiyonel durum kaynağı "hiç izlenmiyor" durumunda mı?

    Böyle bir kaynak "erişilemiyor" değil "izlenmiyor"dur; Telegram'a
    her cron çalışmasında "atlas erişilemiyor (config_yok)" gitmemeli.
    Hangi kodun "izlenmiyor" sayıldığı KAYNAĞA BAĞLIDIR
    (`YAPILANDIRILMAMIS_KODLAR`) — `bagimlilik` raporu dosyadan okuduğu
    için dosya yoksa da henüz taranmamış sayılır, atlas'ın `okunamadi`
    kodu ise gerçek bir arızadır.
    """
    return (
        isinstance(value, dict)
        and value.get("reachable") is False
        and value.get("error") in YAPILANDIRILMAMIS_KODLAR.get(name, ())
    )


def _is_positive_int(value: Any) -> bool:
    """`int` ve `> 0` mi? `bool` reddedilir (int'in alt türü)."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _clamp_message(message: str) -> str:
    """Mesajı Telegram'ın 4096 karakterlik tavanının altına keser.

    `MAX_ERROR_CHARS` yalnızca hata detayını kısar; bakım satırları
    (süreç listesi, repo listesi) tek tek kısaltılsa bile toplam mesaj
    tavanı aşabiliyordu ve Telegram tüm bildirimi reddediyordu.
    """
    if len(message) <= MAX_MESSAGE_CHARS:
        return message
    return message[: MAX_MESSAGE_CHARS - 1] + "…"


def _format_collected_at(collected_at: Any) -> str | None:
    """Özetin alındığı zamanı okunur bir satıra çevirir."""
    if not isinstance(collected_at, (int, float)):
        return None
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(collected_at))


def build_alert_message(summary: dict) -> str | None:
    """Özette sorunlu kaynakları özetleyen kısa Türkçe uyarı metni üretir.

    Bir kaynak `reachable: False` ya da `error` içeriyorsa (git'te liste
    elemanlarında) mesaj üretir; hiçbir sorun yoksa None döner. Böylece
    çağıran taraf `if message:` ile ayırabilir — "sorun yok" durumuna
    Telegram'a gönderilecek bir mesaj üretilmez.
    """
    problems: list[str] = []
    maintenance_lines: list[str] = []
    durum_lines: list[str] = []
    for name, value in summary.items():
        if name in NON_SOURCE_KEYS:
            continue
        if name == MAINTENANCE_KEY:
            # bakım bulguları kaynak cümlelerinden farklı bir şekle sahip
            # (üç alt bölüm, her biri kendi cümlesi) — _describe onları
            # tanımaz, ayrı yol gerekir.
            maintenance_lines.extend(_maintenance_sentences(value))
            continue
        if name in DURUM_SOURCES:
            # erişilememe cümlesi `_describe`'ın mevcut dalından gelir
            # (`reachable: False`); sayı alanlarındaki uyarılar ise
            # `_durum_sentences`'ten. İkisi birbirinin yerine geçmez.
            if not _yapilandirilmamis(name, value):
                sentence = _describe(name, value)
                if sentence:
                    problems.append(sentence)
            durum_lines.extend(_durum_sentences(name, value))
            continue
        sentence = _describe(name, value)
        if sentence:
            problems.append(sentence)

    if not problems and not maintenance_lines and not durum_lines:
        return None

    if problems:
        shown = problems[:MAX_LISTED_SOURCES]
        hidden = len(problems) - len(shown)
        header = "⚠️ kule uyarısı: " + ", ".join(shown)
        if hidden > 0:
            header += f" (+{hidden} kaynak daha)"
    else:
        header = "⚠️ kule uyarısı"
    lines = [header]
    lines.extend(durum_lines)
    lines.extend(maintenance_lines)

    stamp = _format_collected_at(summary.get("collected_at"))
    if stamp:
        lines.append(f"🕐 {stamp}")
    return _clamp_message("\n".join(lines))


def send_telegram_message(config: dict, text: str) -> bool:
    """Telegram Bot API'ye tek mesaj gönderir, başarılıysa True döner.

    `bot_token`/`chat_id` config'te (ve dolayısıyla env'de) boşsa hiç HTTP
    isteği atmadan False döner. Bağlantı hatası, 4xx/5xx veya beklenmedik
    herhangi bir hata durumunda da False döner — asla raise etmez, bildirim
    kule'yi düşüremez.
    """
    telegram = config.get("telegram") or {}
    if not isinstance(telegram, dict):
        return False

    bot_token = telegram.get("bot_token") or ""
    chat_id = telegram.get("chat_id") or ""
    if not bot_token or not chat_id:
        return False  # config eksik — ağa çıkmadan çık

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}

    # httpx istek URL'ini INFO seviyesinde loglar; token bu URL'in içinde
    # olduğu için log'a düşerse secret sızar. Gönderim boyunca logger
    # susturulur, sonra ESKİ SEVİYESİ geri konur (global state).
    httpx_logger = logging.getLogger("httpx")
    previous_level = httpx_logger.level
    httpx_logger.setLevel(logging.WARNING)
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
        return True
    except Exception:
        return False
    finally:
        httpx_logger.setLevel(previous_level)
