"""Çapraz-repo git durumu. Mt3Ui55OS/.claude/scripts/repo_status.py ile aynı
mantık ama bağımsız kopya — kule dış bir scripte bağımlı olmamalı (o repo
gelecekte taşınabilir/silinebilir).

Tek bir repo okunamazsa (bozuk .git, izin hatası, git bulunamadı) o repo
`error` alanıyla işaretlenir, TÜM tarama patlamaz.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

SKIP_DIR_NAMES = {"node_modules", ".git", "venv", ".venv", "__pycache__", "dist", "build"}
MAX_DEPTH = 4  # her kökten itibaren göreli


def find_git_repos(root: Path, max_depth: int = MAX_DEPTH) -> list[Path]:
    """`.git` içeren dizinleri bulur, derinlik sınırlı, ağır dizinleri atlar."""
    found: list[Path] = []
    if not root.exists():
        return found
    root_depth = len(root.parts)
    stack = [root]
    while stack:
        d = stack.pop()
        if not d.is_dir():
            continue
        if (d / ".git").exists():
            found.append(d)
            continue  # iç içe repo'ların içine inme
        if len(d.parts) - root_depth >= max_depth:
            continue
        try:
            for child in d.iterdir():
                if child.is_dir() and child.name not in SKIP_DIR_NAMES and not child.name.startswith("."):
                    stack.append(child)
        except PermissionError:
            continue
    return sorted(found)


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
    )
    return result.stdout.strip()


def repo_summary(repo: Path) -> dict:
    """Tek bir repo için özet. Git çağrılarından biri patlarsa `error` ile döner."""
    try:
        branch = _run_git(repo, "branch", "--show-current") or "(detached)"
        dirty_lines = _run_git(repo, "status", "--porcelain")
        dirty_count = len([l for l in dirty_lines.splitlines() if l.strip()])
        last_commit = _run_git(repo, "log", "-1", "--format=%cr") or "commit yok"
        return {
            "name": repo.name,
            "path": str(repo),
            "branch": branch,
            "dirty_count": dirty_count,
            "son_commit": last_commit,
        }
    except Exception as e:  # subprocess timeout, git bulunamadı, vs.
        return {"name": repo.name, "path": str(repo), "error": str(e)}


def collect(config: dict) -> list[dict]:
    """config['repo_roots'] altındaki tüm repoları tarar ve özetler döner."""
    roots = [Path(r) for r in config.get("repo_roots", [])]
    repos: list[Path] = []
    for root in roots:
        try:
            repos.extend(find_git_repos(root))
        except Exception:
            continue  # bu kök okunamadı, diğer köklerle devam
    summaries = [repo_summary(r) for r in repos]
    # Aynı adlı iki klon (ör. Desktop + Documents) tabloda ayırt edilsin.
    adlar = [x["name"] for x in summaries]
    for x in summaries:
        if adlar.count(x["name"]) > 1:
            x["name"] = f"{Path(x['path']).parent.name}/{x['name']}"
    return summaries
