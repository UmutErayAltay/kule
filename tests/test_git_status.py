"""app/collectors/git_status.py testleri. Gerçek `git` komutu tmp_path
altında kurulan gerçek repolara karşı çalıştırılır (mock yok) — README/CLAUDE
talimatı gereği bu collector için gerçek subprocess tercih edilir.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.collectors import git_status


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path: Path, *, branch: str = "main", make_dirty: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "-b", branch)
    _run(path, "config", "user.email", "test@example.com")
    _run(path, "config", "user.name", "Test User")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run(path, "add", "README.md")
    _run(path, "commit", "-m", "initial commit")
    if make_dirty:
        (path / "README.md").write_text("hello changed\n", encoding="utf-8")
        (path / "new_file.txt").write_text("new\n", encoding="utf-8")
    return path


def test_find_git_repos_finds_top_level_repo(tmp_path):
    repo = _init_repo(tmp_path / "repo1")

    found = git_status.find_git_repos(tmp_path)

    assert found == [repo]


def test_find_git_repos_finds_nested_repo(tmp_path):
    repo = _init_repo(tmp_path / "projects" / "nested" / "repo2")

    found = git_status.find_git_repos(tmp_path)

    assert found == [repo]


def test_find_git_repos_does_not_descend_into_repo(tmp_path):
    """Bir repo bulununca içine inilmemeli (iç içe repo taraması yok)."""
    repo = _init_repo(tmp_path / "outer")
    # outer reposunun içinde başka bir .git benzeri dizin olsa bile taranmamalı
    inner_fake_repo_dir = repo / "vendor" / "some-lib"
    _init_repo(inner_fake_repo_dir)

    found = git_status.find_git_repos(tmp_path)

    assert found == [repo]


def test_find_git_repos_skips_skip_dirs(tmp_path):
    (tmp_path / "node_modules").mkdir()
    _init_repo(tmp_path / "node_modules" / "should-be-skipped")
    real_repo = _init_repo(tmp_path / "real")

    found = git_status.find_git_repos(tmp_path)

    assert found == [real_repo]


def test_find_git_repos_skips_hidden_dirs(tmp_path):
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    _init_repo(hidden / "repo")
    real_repo = _init_repo(tmp_path / "real")

    found = git_status.find_git_repos(tmp_path)

    assert found == [real_repo]


def test_find_git_repos_respects_max_depth(tmp_path):
    # max_depth=1 iken kökten 1 seviye derine kadar arar, bu derinlikte bir
    # repo bulunmamalı.
    deep_repo_dir = tmp_path / "a" / "b" / "c" / "d" / "e"
    _init_repo(deep_repo_dir)

    found = git_status.find_git_repos(tmp_path, max_depth=1)

    assert found == []


def test_find_git_repos_missing_root_returns_empty(tmp_path):
    missing = tmp_path / "does-not-exist"

    found = git_status.find_git_repos(missing)

    assert found == []


def test_repo_summary_clean_repo(tmp_path):
    repo = _init_repo(tmp_path / "clean-repo", branch="main")

    summary = git_status.repo_summary(repo)

    assert summary["name"] == "clean-repo"
    assert summary["path"] == str(repo)
    assert summary["branch"] == "main"
    assert summary["dirty_count"] == 0
    assert "error" not in summary
    assert summary["son_commit"]  # boş olmamalı, relative date döner


def test_repo_summary_dirty_repo(tmp_path):
    repo = _init_repo(tmp_path / "dirty-repo", make_dirty=True)

    summary = git_status.repo_summary(repo)

    # değiştirilen README.md + eklenen new_file.txt = 2 satır porcelain çıktısı
    assert summary["dirty_count"] == 2


def test_repo_summary_not_a_git_repo_returns_error(tmp_path):
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()

    summary = git_status.repo_summary(not_a_repo)

    assert summary["name"] == "plain-dir"
    assert "error" in summary or summary.get("branch") in (None, "(detached)")


def test_collect_scans_configured_repo_roots(tmp_path):
    root1 = tmp_path / "root1"
    root2 = tmp_path / "root2"
    repo1 = _init_repo(root1 / "repoA")
    repo2 = _init_repo(root2 / "repoB", make_dirty=True)

    config = {"repo_roots": [str(root1), str(root2)]}
    results = git_status.collect(config)

    names = {r["name"] for r in results}
    assert names == {"repoA", "repoB"}
    dirty_map = {r["name"]: r["dirty_count"] for r in results}
    assert dirty_map["repoA"] == 0
    assert dirty_map["repoB"] == 2


def test_collect_empty_repo_roots_returns_empty_list(tmp_path):
    config = {"repo_roots": []}

    results = git_status.collect(config)

    assert results == []


def test_collect_missing_repo_roots_key_returns_empty_list():
    results = git_status.collect({})

    assert results == []


def test_collect_nonexistent_root_is_skipped(tmp_path):
    config = {"repo_roots": [str(tmp_path / "nope")]}

    results = git_status.collect(config)

    assert results == []
