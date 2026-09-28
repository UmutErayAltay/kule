"""Mt3Ui55OS vault durumu: kırık wikilink sayısı, yetim not sayısı, açık
Threads.md hikaye sayısı. Mantık Mt3Ui55OS/.claude/scripts/vault_stats.py ve
morning_briefing.py'den referans alındı ama bağımsız yeniden yazıldı (kule
o repo'ya import ile bağımlı olmamalı).

Görev gereği yalnızca SAYILAR döner, kırık linklerin/yetim notların tam
listesi değil — panel için liste gerekmiyor ve büyük vault'larda gereksiz
büyür.
"""
from __future__ import annotations

import re
from pathlib import Path

STATUS_LINE = re.compile(r"\*\*Status:\*\*\s*([^\s]+)")
CLOSED_MARKERS = {"✅"}


def _extract_wikilinks(text: str) -> list[str]:
    """Wikilink hedeflerini çıkarır, kod bloğu/inline kod içindekileri atlar."""
    links: list[str] = []
    in_code_block = False
    in_inline_code = False
    i = 0
    while i < len(text):
        if text[i:i + 3] == "```":
            in_code_block = not in_code_block
            i += 3
            continue
        if text[i] == "`":
            in_inline_code = not in_inline_code
            i += 1
            continue
        if text[i:i + 2] == "[[" and not in_code_block and not in_inline_code:
            j = i + 2
            while j < len(text) and text[j:j + 2] != "]]":
                j += 1
            if j < len(text):
                content = text[i + 2:j].strip()
                cut = len(content)
                for sep in ("|", "#"):
                    idx = content.find(sep)
                    if idx != -1:
                        cut = min(cut, idx)
                target = content[:cut].strip()
                if target:
                    links.append(target)
            i = j + 2
            continue
        i += 1
    return links


def _count_broken_links_and_orphans(vault_path: Path) -> tuple[int, int]:
    md_files = [p for p in vault_path.rglob("*.md") if p.is_file()]
    known = {p.stem for p in md_files}

    referenced: set[str] = set()
    broken_count = 0
    for p in md_files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        links = _extract_wikilinks(text)
        for link in links:
            if link in known:
                referenced.add(link)
            else:
                broken_count += 1

    orphan_count = len(known - referenced)
    return broken_count, orphan_count


def _count_open_threads(threads_md: Path) -> tuple[int, int]:
    if not threads_md.exists():
        return 0, 0
    text = threads_md.read_text(encoding="utf-8", errors="replace")
    statuses = STATUS_LINE.findall(text)
    open_count = sum(1 for s in statuses if s not in CLOSED_MARKERS)
    return open_count, len(statuses)


def collect(config: dict) -> dict:
    vault_path_raw = config.get("vault", {}).get("path", "")
    if not vault_path_raw:
        return {"error": "config.vault.path tanımlı değil"}

    vault_path = Path(vault_path_raw)
    if not vault_path.exists():
        return {"error": f"vault yolu bulunamadı: {vault_path}"}

    try:
        broken_count, orphan_count = _count_broken_links_and_orphans(vault_path)
        open_threads, total_threads = _count_open_threads(
            vault_path / "🔮 850-Companion" / "Threads.md"
        )
        return {
            "broken_link_count": broken_count,
            "orphan_note_count": orphan_count,
            "open_threads": open_threads,
            "total_threads": total_threads,
        }
    except Exception as e:
        return {"error": str(e)}
