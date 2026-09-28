"""app/config.py::load_config testleri.

Gerçek dosya sistemi kullanılır (tmp_path); mock yok. `load_config` bir
`path` argümanı kabul ettiği için çoğu senaryo PROJECT_ROOT'a dokunmadan
test edilebiliyor; varsayılan (path=None) davranışı test etmek için
`app.config.PROJECT_ROOT` monkeypatch edilir.
"""
from __future__ import annotations

import os

import pytest
import yaml

from app import config as config_module
from app.config import ConfigError, load_config


def _write_yaml(path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_load_config_missing_file_raises_config_error(tmp_path):
    missing = tmp_path / "config.yaml"
    assert not missing.exists()

    with pytest.raises(ConfigError) as exc_info:
        load_config(missing)

    message = str(exc_info.value)
    assert str(missing) in message
    assert "config.yaml.example" in message


def test_load_config_reads_existing_file(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"repo_roots": ["/tmp/foo"], "cor": {"base_url": "http://x"}})

    result = load_config(cfg_path)

    assert result["repo_roots"] == ["/tmp/foo"]
    assert result["cor"]["base_url"] == "http://x"


def test_load_config_empty_file_does_not_raise(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("", encoding="utf-8")

    result = load_config(cfg_path)

    # yaml.safe_load("") -> None -> raw {} ile devam edilir.
    assert isinstance(result, dict)
    assert result["telegram"] == {}


def test_load_config_missing_field_is_tolerated(tmp_path):
    """Beklenen alanlardan biri (örn. vault) hiç yoksa load_config patlamaz;
    tüketen kod .get() ile varsayılan kullanır."""
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"repo_roots": []})

    result = load_config(cfg_path)

    assert "vault" not in result
    assert result.get("vault", {}) == {}


def test_load_config_expands_tilde_in_repo_roots(tmp_path, monkeypatch):
    fake_home = tmp_path / "home-dir"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"repo_roots": ["~/Desktop", "~/Documents"]})

    result = load_config(cfg_path)

    assert result["repo_roots"] == [
        str(fake_home / "Desktop"),
        str(fake_home / "Documents"),
    ]


def test_load_config_expands_tilde_in_nested_dict(tmp_path, monkeypatch):
    fake_home = tmp_path / "home-dir"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"vault": {"path": "~/vault"}})

    result = load_config(cfg_path)

    assert result["vault"]["path"] == str(fake_home / "vault")


def test_load_config_env_overrides_telegram_token(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"telegram": {"bot_token": "from-yaml", "chat_id": "from-yaml-chat"}})

    monkeypatch.setenv("KULE_TELEGRAM_BOT_TOKEN", "from-env")
    monkeypatch.setenv("KULE_TELEGRAM_CHAT_ID", "from-env-chat")

    result = load_config(cfg_path)

    assert result["telegram"]["bot_token"] == "from-env"
    assert result["telegram"]["chat_id"] == "from-env-chat"


def test_load_config_no_env_keeps_yaml_values(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"telegram": {"bot_token": "from-yaml", "chat_id": "from-yaml-chat"}})

    monkeypatch.delenv("KULE_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("KULE_TELEGRAM_CHAT_ID", raising=False)

    result = load_config(cfg_path)

    assert result["telegram"]["bot_token"] == "from-yaml"
    assert result["telegram"]["chat_id"] == "from-yaml-chat"


def test_load_config_creates_telegram_dict_when_absent(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"repo_roots": []})
    monkeypatch.setenv("KULE_TELEGRAM_BOT_TOKEN", "from-env")

    result = load_config(cfg_path)

    assert result["telegram"]["bot_token"] == "from-env"


def test_load_config_default_path_missing_raises(tmp_path, monkeypatch):
    """path=None -> PROJECT_ROOT / config.yaml kullanılır. PROJECT_ROOT'u
    boş bir tmp_path'e yönlendirip default-yol davranışını test ediyoruz."""
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert str(tmp_path / "config.yaml") in str(exc_info.value)


def test_load_config_default_path_found(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"repo_roots": ["/x"]})

    result = load_config()

    assert result["repo_roots"] == ["/x"]


def test_load_config_accepts_string_path(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"repo_roots": ["/x"]})

    result = load_config(str(cfg_path))

    assert result["repo_roots"] == ["/x"]


# ------------------------------------------------------- bozuk YAML (güvenlik)


@pytest.mark.parametrize(
    "broken",
    [
        "repo_roots: [\n  - /a\n bad-indent: x\n",   # ScannerError
        "telegram: {bot_token: 'abc\n",              # kaçırılmamış tırnak
        "a: b\n  c: d\n",                             # beklenmedik girinti
        "\t- item\n",                                # tab ile girinti
    ],
)
def test_load_config_broken_yaml_raises_config_error(tmp_path, broken):
    """GÜVENLİK/düzgün davranış: bozuk YAML `yaml.YAMLError` fırlatır; sarılmazsa
    sunucu import-time çöker, CLI ham traceback basar. ConfigError'a çevrilir."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(broken, encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_config(cfg_path)

    assert str(cfg_path) in str(exc_info.value)
    assert "bozuk YAML" in str(exc_info.value)


def test_load_config_non_mapping_yaml_raises_config_error(tmp_path):
    """YAML geçerli ama kökü sözlük değil (`- a\n- b`); `config.setdefault`
    AttributeError fırlatırdı."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("- a\n- b\n", encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_config(cfg_path)

    assert "sözlük olmalı" in str(exc_info.value)


def test_load_config_scalar_yaml_raises_config_error(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("sadece-bir-metin\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_broken_yaml_does_not_crash_the_server(monkeypatch, tmp_path):
    """CLAUDE.md vaadi: config bozukken sunucu exit etmez, açıklayıcı 500 döner."""
    import importlib

    from fastapi.testclient import TestClient

    from app import config as config_module
    from app import main as main_module

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("repo_roots: [\n  - /a\n bad: x\n", encoding="utf-8")

    def broken_load_config():
        return load_config(cfg_path)  # gerçek ConfigError'ı yükseltir

    monkeypatch.setattr(config_module, "load_config", broken_load_config)
    reloaded = importlib.reload(main_module)

    try:
        # import ÇÖKMEDİ — sunucu ayakta, hatayı her istekte döndürüyor
        response = TestClient(reloaded.app).get("/api/summary")
        assert response.status_code == 500
        body = response.json()
        assert body["error"] == reloaded.CONFIG_HELP
        assert "bozuk YAML" in body["detail"]
    finally:
        monkeypatch.undo()
        importlib.reload(main_module)
