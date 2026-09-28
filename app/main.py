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

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import ConfigError, load_config
from app.aggregator import get_cached_summary

app = FastAPI(title="kule")

CONFIG_HELP = "config.yaml.example'ı config.yaml olarak kopyalayıp doldurun"

try:
    _config = load_config()
    _config_error: str | None = None
except ConfigError as e:
    _config = None
    _config_error = str(e)


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
