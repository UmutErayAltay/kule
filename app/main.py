"""kule FastAPI uygulaması.

Config başlangıçta bir kez yüklenmeye çalışılır; `config.yaml` yoksa (ConfigError)
sunucu YİNE DE ayağa kalkar — startup'ta exit etmek yerine hatayı saklayıp her
istekte 500 döndürmek, "config eksikken sunucu hiç açılmıyor" durumunu önler
(örn. reverse proxy health-check'i config'den bağımsız 200 bekleyebilir, kök
`/` ve `/api/summary` dışında bir sağlık ucu eklenirse orası etkilenmemeli).
`render_dashboard_html` henüz yazılmamış olabileceği için import da lazy/try —
ImportError alınırsa aynı "config eksik" desenindeki gibi 500 + açıklayıcı mesaj
döner, modül import hatası tüm app'i çökertmesin diye.
"""
from __future__ import annotations

from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import ConfigError, load_config
from app.aggregator import get_cached_summary
from app.launcher import (
    ERR_AD_YOK,
    TOOLS,
    health,
    list_status,
    restart,
    start,
    stop,
    touch,
)

app = FastAPI(title="kule")

CONFIG_HELP = "config.yaml.example'ı config.yaml olarak kopyalayıp doldurun"

try:
    _config = load_config()
    _config_error: str | None = None
except ConfigError as e:
    _config = None
    _config_error = str(e)

# Kule kapanırken başlatılan panelleri kapatma kaydı BURADA DEĞİL, modül
# import'unda hiç yapılmaz: `app/cli.py::main` yalnızca sunucuyu kuran
# süreçte `atexit.register` çağırır. Import anında kaydolsaydı `kule
# --notify-once` ve `pytest` gibi süreçler de bu yetkiyi kazanır, yani
# testler çalışan panelleri öldürebilirdi.


@app.get("/api/summary")
def api_summary() -> JSONResponse:
    if _config_error is not None:
        return JSONResponse(
            status_code=500,
            content={"error": CONFIG_HELP, "detail": _config_error},
        )
    return JSONResponse(content=get_cached_summary(_config))


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    if _config_error is not None:
        return HTMLResponse(
            content=f"<h1>kule</h1><p>{CONFIG_HELP}</p>",
            status_code=500,
        )
    try:
        # app/web/page.py paralel bir ajan tarafından yazılıyor — henüz yoksa
        # ImportError'ı burada yakalayıp açıklayıcı 500 döndürmek, bu dosyanın
        # o dosyadan bağımsız test edilebilmesini sağlar.
        from app.web.page import render_dashboard_html
    except ImportError as e:
        return HTMLResponse(
            content=f"<h1>kule</h1><p>app/web/page.py henüz hazır değil: {e}</p>",
            status_code=500,
        )
    return HTMLResponse(content=render_dashboard_html())


# ---------------------------------------------------------------- araçlar
# atlas/orkestra/harita panelleri kule'nin içinden yönetilir. Uçlar
# `/api/summary` ile AYNI config politikasını paylaşır: config eksikse
# 500 + CONFIG_HELP, yoksa 400 (istek yanlış) / 500 (başlatılamadı).
# Gerekçe: bu uçlar config'siz de anlamlı değildir — exe yolu, harita'nın
# vault yolu hep oradan gelir.


def _extract_frame_origin(request: Request) -> str | None:
    """Request'ten frame_origin üretir.

    Tarayıcı kule'yi hangi adresle açtıysa araç ona izin vermiş olur.
    Sadece http://127.0.0.1:port veya http://localhost:port kabul edilir.
    Hostname 127.0.0.1 veya localhost değilse ya da port yoksa None.
    """
    hostname = request.url.hostname
    port = request.url.port
    if hostname not in ("127.0.0.1", "localhost") or port is None:
        return None
    return f"http://{hostname}:{port}"


def _no_config() -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"error": CONFIG_HELP, "detail": _config_error},
    )


def _unknown_tool() -> JSONResponse:
    """Bilinmeyen araç adı: 404 + SABİT isim listesi.

    Kullanıcının gönderdiği ad ne olursa olsun panele yansıtılmaz.
    """
    return JSONResponse(
        status_code=404,
        content={"error": ERR_AD_YOK, "gecerli": sorted(TOOLS)},
    )


def _tool_action(ad: str, action: Callable[[], tuple[dict | None, str | None]]):
    """start/stop/restart uçlarının ortak gövdesi.

    Config eksikse 500; araç bilinmiyorsa 404; aksi hâlde aksiyonun
    `(durum, hata)` sonucunu durum koduna çevirir.
    """
    if _config_error is not None:
        return _no_config()
    if ad not in TOOLS:
        return _unknown_tool()
    durum, hata = action()
    if hata is not None:
        return JSONResponse(status_code=400, content={"error": hata})
    return JSONResponse(content=durum)


@app.get("/api/tools")
def api_tools() -> JSONResponse:
    """Üç panel aracının da durumu (çalışıyor mu, pid, port, komut)."""
    if _config_error is not None:
        return _no_config()
    return JSONResponse(content={"tools": list_status(_config)})


@app.post("/api/tools/{ad}/start")
def api_tool_start(ad: str, request: Request) -> JSONResponse:
    frame_origin = _extract_frame_origin(request)
    return _tool_action(ad, lambda: start(ad, _config, frame_origin))


@app.post("/api/tools/{ad}/stop")
def api_tool_stop(ad: str) -> JSONResponse:
    return _tool_action(ad, lambda: stop(ad, _config))


@app.post("/api/tools/{ad}/restart")
def api_tool_restart(ad: str, request: Request) -> JSONResponse:
    frame_origin = _extract_frame_origin(request)
    return _tool_action(ad, lambda: restart(ad, _config, frame_origin))


@app.get("/api/tools/{ad}/health")
def api_tool_health(ad: str) -> JSONResponse:
    """Porta basit HTTP isteği atıp durum kodunu döner (panel sağlığı)."""
    if _config_error is not None:
        return _no_config()
    if ad not in TOOLS:
        return _unknown_tool()
    return JSONResponse(content=health(ad))


@app.post("/api/tools/{ad}/touch")
def api_tool_touch(ad: str) -> JSONResponse:
    """Araç sekmesi aktifken periyodik çağrılır; boşta kapatmayı geciktirir.

    Bilinmeyen ad → mevcut `_unknown_tool` 404; config hatası → mevcut
    `_no_config` 500; aksi hâlde {"ok": true}. Uç yalnız bu süreçte
    kule'nin açtığı araçta kayıt tutar, değilse sessizce no-op.
    """
    if _config_error is not None:
        return _no_config()
    if ad not in TOOLS:
        return _unknown_tool()
    touch(ad)
    return JSONResponse(content={"ok": True})
