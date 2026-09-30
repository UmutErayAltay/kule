"""app/collectors/maintenance_status.py testleri.

İzolasyon kuralı: BU MAKİNENİN gerçek diski, gerçek süreçleri ya da gerçek
`psutil` taramasına asla gidilmez. `shutil.disk_usage` ve `psutil`
monkeypatch ile sahte veriyle beslenir; git tarafı ise (git_status'un kendi
test deseni gibi) tmp_path altında kurulan GERÇEK repolara karşı, mocksuz
çalışır.
"""
from __future__ import annotations

import os
import subprocess
import time
import types
from pathlib import Path

import pytest

from app.collectors import maintenance_status
from conftest import symlink_skip


# ------------------------------------------------------------------ yardım


class FakeUsage(tuple):
    """shutil.disk_usage'in döndürdüğü (total, used, free) üçlüsü."""

    def __new__(cls, total, used, free):
        return super().__new__(cls, (total, used, free))

    total = property(lambda self: self[0])
    used = property(lambda self: self[1])
    free = property(lambda self: self[2])


def _patch_disk_usage(monkeypatch, mapping):
    """mapping: {path_str: (total, used, free)} — yol yoksa OSError."""
    def fake_disk_usage(path):
        key = str(path)
        if key not in mapping:
            raise OSError(f"erişilemedi: {key}")
        return FakeUsage(*mapping[key])

    monkeypatch.setattr(maintenance_status.shutil, "disk_usage", fake_disk_usage)


class FakeProc:
    """psutil.process_iter'ın döndürdüğü süreç benzeri."""

    def __init__(self, info):
        self.info = info


def _fake_psutil(monkeypatch, processes):
    """Sahte psutil modülü: process_iter yalnızca verilen listeyi döner."""
    module = types.ModuleType("psutil")
    module.process_iter = lambda attrs=None: [FakeProc(info) for info in processes]
    monkeypatch.setitem(__import__("sys").modules, "psutil", module)
    return module


def _proc(name, hours, pid=1, cmd=None):
    return {
        "pid": pid,
        "name": name,
        "create_time": time.time() - hours * 3600,
        "cmdline": cmd if cmd is not None else [name, "--flag"],
    }


def _assert_no_secrets(obj, secrets=("SECRET_TOKEN", "hunter2", "s3cr3t")):
    """Özyinelemeli: yapının hiçbir yerinde bu secret geçmemeli."""
    if isinstance(obj, str):
        for secret in secrets:
            assert secret not in obj, f"secret sızdı: {secret!r} -> {obj!r}"
    elif isinstance(obj, dict):
        for value in obj.values():
            _assert_no_secrets(value, secrets)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _assert_no_secrets(value, secrets)


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path: Path, *, make_dirty: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(path, "init", "-b", "main")
    _run_git(path, "config", "user.email", "test@example.com")
    _run_git(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(path, "add", "README.md")
    _run_git(path, "commit", "-m", "initial commit")
    if make_dirty:
        (path / "README.md").write_text("hello changed\n", encoding="utf-8")
    return path


def _age(path: Path, days: float) -> None:
    """Dosyanın mtime'ını geçmişe alır (unutulmuş repo senaryosu)."""
    old = time.time() - days * 86400
    os.utime(path, (old, old))


# ------------------------------------------------------------------- disk


def test_disk_below_threshold_reports_nothing(monkeypatch, tmp_path):
    _patch_disk_usage(monkeypatch, {str(tmp_path): (1000, 500, 500)})

    result = maintenance_status._collect_disk({"repo_roots": [str(tmp_path)]}, {})

    assert result["full"] == []
    assert "error" not in result


def test_disk_above_threshold_is_flagged(monkeypatch, tmp_path):
    _patch_disk_usage(monkeypatch, {str(tmp_path): (1000, 950, 50)})

    result = maintenance_status._collect_disk({"repo_roots": [str(tmp_path)]}, {})

    assert len(result["full"]) == 1
    entry = result["full"][0]
    assert entry["path"] == str(tmp_path)
    assert entry["percent"] == 95.0
    assert entry["free_gb"] == 0.0
    assert result["threshold_percent"] == maintenance_status.DEFAULT_DISK_THRESHOLD_PERCENT


def test_disk_exactly_at_threshold_is_not_flagged(monkeypatch, tmp_path):
    """Eşik 'aşıyor' anlamında gelir, eşitlik uyarı üretmez."""
    _patch_disk_usage(monkeypatch, {str(tmp_path): (1000, 900, 100)})

    result = maintenance_status._collect_disk({"repo_roots": [str(tmp_path)]}, {})

    assert result["full"] == []


def test_disk_custom_threshold_from_config(monkeypatch, tmp_path):
    _patch_disk_usage(monkeypatch, {str(tmp_path): (1000, 750, 250)})

    result = maintenance_status._collect_disk(
        {"repo_roots": [str(tmp_path)]}, {"disk_threshold_percent": 50}
    )

    assert result["threshold_percent"] == 50.0
    assert len(result["full"]) == 1


def test_disk_paths_config_wins_over_repo_roots(monkeypatch, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _patch_disk_usage(monkeypatch, {str(other): (1000, 990, 10)})

    result = maintenance_status._collect_disk(
        {"repo_roots": ["/yok/bir/yol"]}, {"disk_paths": [str(other)]}
    )

    assert [e["path"] for e in result["full"]] == [str(other)]


def test_disk_falls_back_to_root_when_no_paths_configured(monkeypatch):
    # Anahtar, uygulamanın gerçekten çağırdığı yol: Windows'ta
    # `str(Path("/")) == "\\"`, düz "/" eşleşmezdi ve "erişilemedi" sanırdı.
    kok = str(Path(maintenance_status.FALLBACK_DISK_PATH))
    _patch_disk_usage(monkeypatch, {kok: (1000, 200, 800)})

    result = maintenance_status._collect_disk({}, {})

    assert result["full"] == []  # %20 dolu, eşiği aşmıyor
    assert "error" not in result


def test_disk_unreadable_path_reports_error_without_raising(monkeypatch, tmp_path):
    _patch_disk_usage(monkeypatch, {})
    # Kısaltma sınırı yükseltilir: aksi halde mesaj Windows'taki uzun tmp
    # yolunda 80 karaktere kesilir ve "erişilemedi" bile kırpılır.
    monkeypatch.setattr(maintenance_status, "MAX_ERROR_CHARS", 10_000)

    result = maintenance_status._collect_disk({"repo_roots": [str(tmp_path)]}, {})

    assert "error" in result
    assert "erişilemedi" in result["error"]
    assert result["full"] == []


def test_disk_error_is_truncated(monkeypatch, tmp_path):
    """Kısaltma sınırı gerçekten uygulanır: uzun hata metni paneli/Telegram'ı
    şişirmez. `test_notifier.py`deki `_clamp_message` testinin disk karşılığı."""
    _patch_disk_usage(monkeypatch, {})

    result = maintenance_status._collect_disk({"repo_roots": [str(tmp_path)]}, {})

    assert len(result["error"]) <= maintenance_status.MAX_ERROR_CHARS


def test_disk_zero_total_is_not_divided(monkeypatch, tmp_path):
    """total == 0 ise yüzde hesabı yapılmaz (ZeroDivisionError yerine error)."""
    _patch_disk_usage(monkeypatch, {str(tmp_path): (0, 0, 0)})
    monkeypatch.setattr(maintenance_status, "MAX_ERROR_CHARS", 10_000)

    result = maintenance_status._collect_disk({"repo_roots": [str(tmp_path)]}, {})

    assert "error" in result
    assert "sıfır" in result["error"]
    assert result["full"] == []


def test_disk_results_sorted_worst_first(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _patch_disk_usage(monkeypatch, {str(a): (1000, 910, 90), str(b): (1000, 990, 10)})

    result = maintenance_status._collect_disk(
        {}, {"disk_paths": [str(a), str(b)], "disk_threshold_percent": 90}
    )

    assert [e["path"] for e in result["full"]] == [str(b), str(a)]


# -------------------------------------------------------------- süreçler


def test_processes_older_than_threshold_are_flagged(monkeypatch):
    _fake_psutil(monkeypatch, [_proc("ollama", 10.0, pid=42)])

    result = maintenance_status._collect_stale_processes({})

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["pid"] == 42
    assert item["name"] == "ollama"
    assert item["hours"] == 10.0
    assert result["min_hours"] == maintenance_status.DEFAULT_STALE_PROCESS_HOURS


def test_processes_younger_than_threshold_are_ignored(monkeypatch):
    _fake_psutil(monkeypatch, [_proc("ollama", 2.0)])

    result = maintenance_status._collect_stale_processes({})

    assert result["items"] == []


def test_processes_name_must_match_pattern(monkeypatch):
    _fake_psutil(monkeypatch, [_proc("postgres", 99.0)])

    result = maintenance_status._collect_stale_processes({})

    assert result["items"] == []


def test_processes_custom_hours_and_names_from_config(monkeypatch):
    _fake_psutil(monkeypatch, [_proc("postgres", 5.0, pid=7)])

    result = maintenance_status._collect_stale_processes(
        {"stale_process_hours": 1, "process_names": ["postgres"]}
    )

    assert [i["pid"] for i in result["items"]] == [7]
    assert result["min_hours"] == 1.0
    assert result["names"] == ["postgres"]


def test_processes_name_matching_is_prefix_and_case_insensitive(monkeypatch):
    _fake_psutil(monkeypatch, [_proc("Node", 8.0, pid=3)])

    result = maintenance_status._collect_stale_processes({"process_names": ["node"]})

    assert len(result["items"]) == 1


def test_processes_cmdline_is_never_collected(monkeypatch):
    """GÜVENLİK: süreç komut satırı secret taşıyabilir (API key, DB parolası)
    ve `/api/summary` auth'suz. Bu yüzden `cmd` alanı HİÇ toplanmaz."""
    secret = "postgres://user:SECRET_TOKEN@db/prod"
    _fake_psutil(
        monkeypatch, [_proc("ollama", 10.0, pid=5, cmd=["ollama", "run", secret])]
    )

    result = maintenance_status._collect_stale_processes({})

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert set(item) == {"pid", "name", "hours"}
    assert "cmd" not in item
    _assert_no_secrets(result)
    _assert_no_secrets(result["items"][0])


def test_collect_output_carries_no_process_secret(monkeypatch, tmp_path):
    """Güvenlik: tüm `collect` çıktısı (panelin gördüğü JSON) temiz olmalı."""
    _patch_disk_usage(monkeypatch, {str(tmp_path): (1000, 100, 900)})
    _fake_psutil(
        monkeypatch,
        [_proc("uvicorn", 12.0, pid=9, cmd=["uvicorn", "--password", "hunter2"])],
    )

    result = maintenance_status.collect({"repo_roots": [str(tmp_path)]})

    _assert_no_secrets(result)


@pytest.mark.parametrize(
    "process_name, pattern, matches",
    [
        ("node", "node", True),
        ("Node", "node", True),
        ("node-red", "node", False),      # ayrı program, kelime sınırı
        ("node_helper", "node", False),
        ("python3.11", "python3", True),  # sürüm eki serbest
        ("runner", "node", False),        # içinde geçmesi yetmez
        ("ollama", "ollama", True),
        ("a.b*c", "a.b*c", True),         # regex karakterleri kaçırılır
    ],
)
def test_process_name_matching_respects_boundaries(monkeypatch, process_name, pattern, matches):
    """`node` kalbı `node-red`'i yakalamamalı; eşleşme adın başında olmalı."""
    _fake_psutil(monkeypatch, [_proc(process_name, 8.0, pid=1)])

    result = maintenance_status._collect_stale_processes({"process_names": [pattern]})

    assert bool(result["items"]) is matches


def test_process_names_ignore_none_and_empty_entries(monkeypatch):
    """GÜVENLİK: YAML'da `process_names: [ollama, null]` girdisi `str(None)` ile
    `"none"` olur ve "none" kelimesi geçen her süreçle eşleşirdi."""
    _fake_psutil(monkeypatch, [_proc("ollama", 10.0, pid=2)])

    result = maintenance_status._collect_stale_processes(
        {"process_names": ["ollama", None, "  ", ""]}
    )

    assert result["names"] == ["ollama"]
    assert [i["pid"] for i in result["items"]] == [2]


def test_process_names_all_none_falls_back_to_default(monkeypatch):
    _fake_psutil(monkeypatch, [_proc("ollama", 10.0, pid=2)])

    result = maintenance_status._collect_stale_processes({"process_names": [None, ""]})

    assert result["names"] == maintenance_status.DEFAULT_PROCESS_NAMES


@symlink_skip
def test_git_latest_mtime_does_not_follow_symlinks_out_of_repo(tmp_path):
    """GÜVENLİK: symlink takip edilirse tarama repo ağacı DIŞINA (ör. `/`)
    çıkabilir ve tüm diski tarar."""
    outside = tmp_path / "disarida"
    outside.mkdir()
    (outside / "eski.txt").write_text("x", encoding="utf-8")
    _age(outside / "eski.txt", days=999)

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.txt").write_text("y", encoding="utf-8")
    _age(repo / "src.txt", days=2)
    (repo / "baglanti").symlink_to(outside, target_is_directory=True)

    latest = maintenance_status._latest_mtime(repo)

    # repo içindeki dosya bulunur, dışarıdaki 999 günlük dosya DEĞİL
    assert latest == pytest.approx(os.stat(repo / "src.txt").st_mtime, abs=1)


@symlink_skip
def test_git_latest_mtime_skips_symlinked_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "hedef.txt"
    target.write_text("x", encoding="utf-8")
    _age(target, days=500)
    (repo / "baglanti.txt").symlink_to(target)

    assert maintenance_status._latest_mtime(repo) is None


def test_collect_ignores_nan_thresholds(monkeypatch, tmp_path):
    """NaN `<= 0` kontrolünden geçer (float('nan') <= 0 -> False); eşik olarak
    kullanılırsa her süreç/repo sessizce elenir."""
    nan = float("nan")
    _patch_disk_usage(monkeypatch, {str(tmp_path): (1000, 100, 900)})
    _fake_psutil(monkeypatch, [_proc("ollama", 10.0, pid=1)])

    result = maintenance_status.collect(
        {
            "repo_roots": [str(tmp_path)],
            "maintenance": {
                "disk_threshold_percent": nan,
                "stale_process_hours": nan,
                "stale_git_days": nan,
            },
        }
    )

    assert result["disk"]["threshold_percent"] == maintenance_status.DEFAULT_DISK_THRESHOLD_PERCENT
    assert result["stale_processes"]["min_hours"] == maintenance_status.DEFAULT_STALE_PROCESS_HOURS
    assert result["stale_git"]["min_days"] == maintenance_status.DEFAULT_STALE_GIT_DAYS
    # NaN eşiği süreçleri de eliyordu — default ile süreç yine raporlanır
    assert len(result["stale_processes"]["items"]) == 1


def test_processes_sorted_and_capped(monkeypatch):
    _fake_psutil(
        monkeypatch,
        [_proc("node", float(h), pid=h) for h in range(1, 25)],  # 1..24 saat
    )

    result = maintenance_status._collect_stale_processes({"stale_process_hours": 0.5})

    assert len(result["items"]) == maintenance_status.MAX_STALE_PROCESSES
    hours = [i["hours"] for i in result["items"]]
    assert hours == sorted(hours, reverse=True)
    assert hours[0] == 24.0


def test_processes_threshold_uses_unrounded_hours(monkeypatch):
    """Regresyon: eşik karşılaştırması yuvarlanmış `hours` ile yapılırsa,
    saatte 0.0005 gibi küçük eşiklerde süreç sessizce elenir (0.0'a
    yuvarlanır). Karşılaştırma ham değerle yapılmalı."""
    _fake_psutil(monkeypatch, [_proc("sleep", 0.00055, pid=99)])

    result = maintenance_status._collect_stale_processes(
        {"stale_process_hours": 0.0005, "process_names": ["sleep"]}
    )

    assert [i["pid"] for i in result["items"]] == [99]
    assert result["items"][0]["hours"] == 0.0  # görünüm yuvarlanır, eleme olmaz


def test_processes_missing_create_time_is_skipped(monkeypatch):
    proc = _proc("ollama", 10.0)
    del proc["create_time"]
    _fake_psutil(monkeypatch, [proc])

    result = maintenance_status._collect_stale_processes({})

    assert result["items"] == []
    assert "error" not in result


def test_processes_without_psutil_reports_error(monkeypatch):
    """psutil kurulu değilken bölüm düşer ama raporlar."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("No module named 'psutil'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = maintenance_status._collect_stale_processes({})

    assert "psutil" in result["error"]
    assert result["items"] == []


def test_processes_process_iter_failure_reports_error(monkeypatch):
    module = types.ModuleType("psutil")

    def boom(attrs=None):
        raise RuntimeError("sistem çağrısı başarısız")

    module.process_iter = boom
    monkeypatch.setitem(__import__("sys").modules, "psutil", module)

    result = maintenance_status._collect_stale_processes({})

    assert "sistem çağrısı başarısız" in result["error"]


def test_processes_one_broken_proc_does_not_break_scan(monkeypatch):
    """Tarama sırasında ölen bir süreç (info okununca hata) taramayı düşürmez."""

    class DeadProc:
        @property
        def info(self):
            raise RuntimeError("süreç öldü")

    module = types.ModuleType("psutil")
    module.process_iter = lambda attrs=None: [DeadProc(), FakeProc(_proc("ollama", 10.0))]
    monkeypatch.setitem(__import__("sys").modules, "psutil", module)

    result = maintenance_status._collect_stale_processes({})

    assert len(result["items"]) == 1
    assert "error" not in result


# ------------------------------------------------------------------- git


def test_git_dirty_repo_with_old_change_is_flagged(tmp_path):
    repo = _init_repo(tmp_path / "eski-repo", make_dirty=True)
    _age(repo / "README.md", days=10)

    result = maintenance_status._collect_stale_git({"repo_roots": [str(tmp_path)]}, {})

    assert len(result["items"]) == 1
    assert result["items"][0]["name"] == "eski-repo"
    assert result["items"][0]["age_days"] >= 10
    assert result["items"][0]["dirty_count"] == 1
    assert result["min_days"] == maintenance_status.DEFAULT_STALE_GIT_DAYS


def test_git_dirty_repo_with_recent_change_is_not_flagged(tmp_path):
    repo = _init_repo(tmp_path / "taze-repo", make_dirty=True)

    result = maintenance_status._collect_stale_git({"repo_roots": [str(tmp_path)]}, {})

    assert result["items"] == []


def test_git_clean_repo_is_never_flagged(tmp_path):
    repo = _init_repo(tmp_path / "temiz-repo")
    _age(repo / "README.md", days=90)

    result = maintenance_status._collect_stale_git({"repo_roots": [str(tmp_path)]}, {})

    assert result["items"] == []


def test_git_custom_days_from_config(tmp_path):
    repo = _init_repo(tmp_path / "repo", make_dirty=True)
    _age(repo / "README.md", days=5)

    result = maintenance_status._collect_stale_git(
        {"repo_roots": [str(tmp_path)]}, {"stale_git_days": 1}
    )

    assert len(result["items"]) == 1


def test_git_skips_broken_repo_entries(tmp_path, monkeypatch):
    """Hata taşıyan repo girdileri (git_status'un döndürdüğü `error` şekli)
    elenir; sağlam olanlar yine değerlendirilir."""
    repo = _init_repo(tmp_path / "iyi")
    (repo / "README.md").write_text("değişti\n", encoding="utf-8")
    _age(repo / "README.md", days=30)

    def fake_collect(config):
        return [
            {"name": "bozuk", "error": "git yok"},
            {"name": "yol-yok", "dirty_count": 3},
            {"name": "temiz-ama-eski", "path": str(repo), "dirty_count": 0},
            {"name": "iyi", "path": str(repo), "dirty_count": 1},
        ]

    monkeypatch.setattr(maintenance_status.git_status, "collect", fake_collect)

    result = maintenance_status._collect_stale_git({"repo_roots": []}, {})

    assert [i["name"] for i in result["items"]] == ["iyi"]


def test_git_uses_git_status_collector_not_its_own_scan(tmp_path, monkeypatch):
    """Repo taraması git_status.collect'e aittir — maintenance bunu yeniden
    yazmaz, yalnızca onun çıktısını süzer."""
    calls = {"n": 0}

    def fake_collect(config):
        calls["n"] += 1
        return [{"name": "x", "path": str(tmp_path), "dirty_count": 0}]

    monkeypatch.setattr(maintenance_status.git_status, "collect", fake_collect)

    maintenance_status._collect_stale_git({}, {})

    assert calls["n"] == 1


def test_git_latest_mtime_picks_newest_file(tmp_path):
    old = tmp_path / "eski.txt"
    new = tmp_path / "yeni.txt"
    old.write_text("a", encoding="utf-8")
    new.write_text("b", encoding="utf-8")
    _age(old, days=30)
    _age(new, days=1)

    latest = maintenance_status._latest_mtime(tmp_path)

    assert latest == pytest.approx(os.stat(new).st_mtime, abs=1)


def test_git_latest_mtime_skips_heavy_dirs(tmp_path):
    heavy = tmp_path / "node_modules" / "paket"
    heavy.mkdir(parents=True)
    (heavy / "index.js").write_text("x", encoding="utf-8")
    _age(heavy / "index.js", days=365)
    shallow = tmp_path / "src.txt"
    shallow.write_text("y", encoding="utf-8")
    _age(shallow, days=2)

    latest = maintenance_status._latest_mtime(tmp_path)

    assert latest == pytest.approx(os.stat(shallow).st_mtime, abs=1)


def test_git_latest_mtime_respects_entry_cap(tmp_path):
    for i in range(maintenance_status.MAX_SCANNED_ENTRIES + 50):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")

    # sonsuz dönmez, bir değer döner ya da döndürmez — patlamaz
    latest = maintenance_status._latest_mtime(tmp_path)

    assert latest is None or isinstance(latest, float)


def test_git_latest_mtime_empty_dir_returns_none(tmp_path):
    empty = tmp_path / "bos"
    empty.mkdir()

    assert maintenance_status._latest_mtime(empty) is None


def test_git_collect_failure_reports_error(monkeypatch):
    def boom(config):
        raise RuntimeError("tarama patladi")

    monkeypatch.setattr(maintenance_status.git_status, "collect", boom)

    result = maintenance_status._collect_stale_git({}, {})

    assert result["error"] == "tarama patladi"
    assert result["items"] == []


# ----------------------------------------------------------------- giriş


def test_collect_returns_all_three_sections(monkeypatch, tmp_path):
    _patch_disk_usage(monkeypatch, {str(tmp_path): (1000, 100, 900)})
    _fake_psutil(monkeypatch, [_proc("ollama", 10.0)])

    result = maintenance_status.collect({"repo_roots": [str(tmp_path)]})

    assert set(result) == {"disk", "stale_processes", "stale_git"}
    assert result["disk"]["full"] == []
    assert len(result["stale_processes"]["items"]) == 1
    assert result["stale_git"]["items"] == []


def test_collect_without_maintenance_config_uses_defaults(monkeypatch, tmp_path):
    _patch_disk_usage(monkeypatch, {str(tmp_path): (1000, 100, 900)})
    _fake_psutil(monkeypatch, [])

    result = maintenance_status.collect({"repo_roots": [str(tmp_path)]})

    assert result["disk"]["threshold_percent"] == maintenance_status.DEFAULT_DISK_THRESHOLD_PERCENT
    assert result["stale_processes"]["min_hours"] == maintenance_status.DEFAULT_STALE_PROCESS_HOURS
    assert result["stale_processes"]["names"] == maintenance_status.DEFAULT_PROCESS_NAMES
    assert result["stale_git"]["min_days"] == maintenance_status.DEFAULT_STALE_GIT_DAYS


def test_collect_with_broken_maintenance_config_uses_defaults(monkeypatch, tmp_path):
    _patch_disk_usage(monkeypatch, {str(tmp_path): (1000, 100, 900)})
    _fake_psutil(monkeypatch, [])

    result = maintenance_status.collect(
        {
            "repo_roots": [str(tmp_path)],
            "maintenance": "bozuk",
        }
    )

    assert result["disk"]["threshold_percent"] == maintenance_status.DEFAULT_DISK_THRESHOLD_PERCENT


@pytest.mark.parametrize(
    "bad_value",
    [None, 0, -5, "çok", True, [1, 2]],
)
def test_collect_ignores_invalid_thresholds(monkeypatch, tmp_path, bad_value):
    _patch_disk_usage(monkeypatch, {str(tmp_path): (1000, 100, 900)})
    _fake_psutil(monkeypatch, [])

    result = maintenance_status.collect(
        {
            "repo_roots": [str(tmp_path)],
            "maintenance": {
                "disk_threshold_percent": bad_value,
                "stale_process_hours": bad_value,
                "stale_git_days": bad_value,
            },
        }
    )

    assert result["disk"]["threshold_percent"] == maintenance_status.DEFAULT_DISK_THRESHOLD_PERCENT
    assert result["stale_processes"]["min_hours"] == maintenance_status.DEFAULT_STALE_PROCESS_HOURS
    assert result["stale_git"]["min_days"] == maintenance_status.DEFAULT_STALE_GIT_DAYS


def test_collect_isolates_a_failing_section(monkeypatch, tmp_path):
    """Bir alt bölüm beklenmedik hata verse diğer ikisi yine döner."""
    _patch_disk_usage(monkeypatch, {str(tmp_path): (1000, 100, 900)})
    _fake_psutil(monkeypatch, [])

    def boom(config):
        raise RuntimeError("git patladi")

    monkeypatch.setattr(maintenance_status.git_status, "collect", boom)

    result = maintenance_status.collect({"repo_roots": [str(tmp_path)]})

    assert "disk" in result and result["disk"]["full"] == []
    assert "stale_processes" in result
    assert result["stale_git"]["error"] == "git patladi"


@pytest.mark.parametrize(
    "config",
    [{}, {"repo_roots": None}, {"repo_roots": [None]}, {"maintenance": 5}],
)
def test_collect_never_raises_on_hostile_config(monkeypatch, config):
    """Hiçbir config biçimi collect'i patlatmamalı."""
    _patch_disk_usage(monkeypatch, {})
    _fake_psutil(monkeypatch, [])

    result = maintenance_status.collect(config)

    assert set(result) == {"disk", "stale_processes", "stale_git"}


def test_collect_does_not_kill_anything(monkeypatch, tmp_path):
    """Bakım botu YALNIZCA raporlar: hiçbir kill/terminate çağrısı olmamalı."""
    killed = []

    class FakeProcKillable(FakeProc):
        def kill(self):
            killed.append(self.info.get("pid"))

    module = types.ModuleType("psutil")
    module.process_iter = lambda attrs=None: [FakeProcKillable(_proc("ollama", 99.0, pid=1234))]
    monkeypatch.setitem(__import__("sys").modules, "psutil", module)
    _patch_disk_usage(monkeypatch, {str(tmp_path): (1000, 100, 900)})

    maintenance_status.collect({"repo_roots": [str(tmp_path)]})

    assert killed == []
