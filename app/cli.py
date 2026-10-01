"""kule için komut satırı giriş noktası.

`pyproject.toml::[project.scripts]` bu modüldeki `main()`'i `kule` komutuna
bağlar (Dalga D). Sorumluluğu tek: argümanları çözüp uvicorn'u başlatmak.

Config eksikse (`config.yaml` yok) burada erken ve anlaşılır bir uyarı basılır,
ama `app/main.py`'daki desenle tutarlı olarak sunucu YİNE DE başlatılır —
config'i CLI seviyesinde zorunlu kılmak, health-check gibi config'den bağımsız
uçları da engeller (bkz. app/main.py modül dokümantasyonu).

`--notify-once` (Dalga E) ise sunucuyu hiç başlatmaz: tek seferlik özet alır,
sorun varsa Telegram'a gönderir, her durumda exit code 0 ile çıkar — cron
gibi zamanlanmış görevlerde kullanılabilmesi için "sorun var" durumu da hata
sayılmaz.
"""
from __future__ import annotations

import argparse
import atexit

import uvicorn

from app.aggregator import get_cached_summary
from app.config import PROJECT_ROOT, ConfigError, load_config
from app.launcher import shutdown as shutdown_launched_tools
from app.notifier import build_alert_message, send_telegram_message

DEFAULT_HOST = "127.0.0.1"
# Bu repoda daha önce test edilmiş bir port kaydı yok; görev tanımındaki
# varsayılanla (8790) tutarlı tutuluyor.
DEFAULT_PORT = 8790


def _warn_if_config_missing() -> None:
    try:
        load_config()
    except ConfigError as e:
        example_path = PROJECT_ROOT / "config.yaml.example"
        config_path = PROJECT_ROOT / "config.yaml"
        print(
            f"[kule] UYARI: config.yaml bulunamadı ({e})\n"
            f"[kule]        Önce şunu çalıştır: cp {example_path} {config_path} "
            "— sonra gerçek değerlerini gir.\n"
            "[kule]        Sunucu yine de başlatılıyor; config gelene kadar "
            "/api/summary ve / 500 dönecek.",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kule",
        description="Umut'un projelerinin durumunu tek panelde toplayan kontrol kulesi.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Sunucunun dinleyeceği host (varsayılan: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Sunucunun dinleyeceği port (varsayılan: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Geliştirme için otomatik yeniden yükleme (uvicorn --reload)",
    )
    parser.add_argument(
        "--notify-once",
        action="store_true",
        help=(
            "Sunucuyu başlatmadan tek seferlik durum kontrolü yapar; sorun "
            "varsa Telegram'a uyarı gönderir. Cron gibi zamanlanmış görevler "
            "için: her durumda exit code 0."
        ),
    )
    return parser


def run_notify_once(config: dict) -> None:
    """Tek seferlik kontrol: özet al, sorun varsa uyar, stdout'a bas.

    Sunucuyu başlatmaz ve raise etmez — cron'da patlamak yerine sessizce
    "gönderilemedi" demek doğru davranış.
    """
    summary = get_cached_summary(config)
    message = build_alert_message(summary)

    if message is None:
        print("kule: her şey yolunda")
        return

    sent = send_telegram_message(config, message)
    print(message)
    if sent:
        print("kule: uyarı Telegram'a gönderildi")
    else:
        print(
            "kule: uyarı gönderilemedi "
            "(telegram.bot_token / chat_id boş ya da Telegram'a ulaşılamadı)"
        )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.notify_once:
        try:
            config = load_config()
        except ConfigError as e:
            # Sunucu modundaki "yine de başlat" politikası burada geçerli
            # değil: kontrol yapmadan çıkmanın anlamı yok, kullanıcıya ne
            # yapması gerektiğini söyleyip sadece uyarı basıp çıkıyoruz.
            print(f"[kule] UYARI: {e}\n[kule]        Bildirim gönderilemedi.")
            return
        run_notify_once(config)
        return

    _warn_if_config_missing()

    # Sunucu modu: kule kapanırken panelden başlatılan alt süreçleri de
    # kapat. Kayıt YALNIZCA burada, sunucuyu kuran süreçte yapılır —
    # `--notify-once` ve testler bu hakkı kazanmamalı (bkz.
    # `app/launcher.py::shutdown` docstring'i).
    atexit.register(shutdown_launched_tools)

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
