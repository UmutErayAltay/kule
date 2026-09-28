"""kule konfigürasyon yükleyici.

`config.yaml`'ı okur; yoksa sessizce `config.yaml.example`'a düşmez —
secret gerektiren alanlar (Telegram token, DB URL) örnek dosyada boş,
oraya düşmek "çalışıyor gibi görünüp aslında hiçbir kaynağa ulaşamayan"
bir panel üretir. Bunun yerine açık hata verip hangi dosyanın
oluşturulması gerektiğini söyler.

Env değişkenleri (KULE_TELEGRAM_BOT_TOKEN, KULE_TELEGRAM_CHAT_ID) yaml'daki
değerin önüne geçer — secret'lar repo'ya asla yazılmaz, sadece ortamdan akar.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILENAME = "config.yaml"
EXAMPLE_FILENAME = "config.yaml.example"

# Proje kökü: bu dosya app/config.py altında, kök bir üst dizin.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """config.yaml eksik veya bozuksa fırlatılır."""


def _expand(value: Any) -> Any:
    """Str değerlerde ~ ve env değişkenlerini genişletir; dict/list içinde recursive."""
    if isinstance(value, str):
        return os.path.expanduser(os.path.expandvars(value))
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_config(path: Path | str | None = None) -> dict:
    """config.yaml'ı yükler. Bulunamazsa ConfigError fırlatır (example'a düşmez)."""
    config_path = Path(path).expanduser() if path else PROJECT_ROOT / CONFIG_FILENAME

    if not config_path.exists():
        example_path = PROJECT_ROOT / EXAMPLE_FILENAME
        raise ConfigError(
            f"{config_path} bulunamadı. Önce şunu çalıştır: "
            f"cp {example_path} {config_path} — sonra gerçek değerlerini gir."
        )

    # Bozuk YAML (yarım kalmış indentation, tuhaf tırnak) `yaml.YAMLError`
    # alt sınıflarıyla gelir. Sarılmazsa sunucu import-time çöker, CLI ham
    # traceback basar — CLAUDE.md'nin "config bozuksa sunucu exit etmez,
    # açıklayıcı hata verir" vaadi böylece gerçekleşmez.
    try:
        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{config_path} bozuk YAML: {e}") from e
    except OSError as e:
        raise ConfigError(f"{config_path} okunamadı: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(
            f"{config_path} bir sözlük olmalı, {type(raw).__name__} bulundu."
        )

    config = _expand(raw)

    # Env override'ları: yalnızca Telegram secret'ları için (görev tanımı gereği).
    telegram = config.setdefault("telegram", {})
    env_token = os.environ.get("KULE_TELEGRAM_BOT_TOKEN")
    if env_token:
        telegram["bot_token"] = env_token
    env_chat_id = os.environ.get("KULE_TELEGRAM_CHAT_ID")
    if env_chat_id:
        telegram["chat_id"] = env_chat_id

    return config
