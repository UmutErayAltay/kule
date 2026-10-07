"""app/collectors/vault_status.py testleri. Gerçek dosya sistemi (tmp_path)
üzerinde sahte bir vault ağacı kurulur, mock yok.
"""
from __future__ import annotations

from app.collectors import vault_status


def test_collect_missing_vault_path_config_returns_error():
    result = vault_status.collect({})

    assert "error" in result


def test_collect_empty_vault_path_returns_error():
    result = vault_status.collect({"vault": {"path": ""}})

    assert "error" in result


def test_collect_nonexistent_vault_path_returns_error(tmp_path):
    missing = tmp_path / "no-such-vault"

    result = vault_status.collect({"vault": {"path": str(missing)}})

    assert "error" in result
    assert str(missing) in result["error"]


def _notes(vault, files):
    """`{"klasor/not.md": "içerik"}` -> gerçek dosyalar."""
    for rel, text in files.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_collect_counts_broken_links_and_orphans(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    _notes(
        vault,
        {
            # note-a: note-b'ye (var) ve note-missing'e (yok) link verir
            "notlar/note-a.md": "# Note A\n\nSee [[note-b]] and [[note-missing]].\n",
            # note-b: note-a'nın verdiği link sayesinde bağlı -> yetim değil
            "notlar/note-b.md": "# Note B\ncontent\n",
            # note-c: ne link alıyor ne veriyor -> gerçek yetim
            "notlar/note-c.md": "# Note C\ncontent\n",
        },
    )

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["broken_link_count"] == 1  # note-missing
    # note-a link VERİYOR (yetim değil), note-b link ALIYOR (yetim değil).
    # Yalnız note-c ne alıyor ne veriyor.
    assert result["orphan_note_count"] == 1


def test_orphan_is_neither_receiving_nor_giving_links(tmp_path):
    """Eski tanım yalnızca "kimse bağlanmıyor" diyordu; link veren ama alan
    olmayan not (grafın yaprağı olmayan başlangıcı) yetim SAYILMAZ."""
    vault = tmp_path / "vault"
    _notes(
        vault,
        {
            "notlar/veren.md": "[[alan]]\n",
            "notlar/alan.md": "içerik\n",
        },
    )

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["orphan_note_count"] == 0


def test_note_with_only_broken_outbound_link_is_not_orphan(tmp_path):
    """Kırık de olsa link veren not bağlantısız değildir (harita ile aynı)."""
    vault = tmp_path / "vault"
    _notes(vault, {"notlar/a.md": "bkz [[yok-boyle-bir-not]]\n"})

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["broken_link_count"] == 1
    assert result["orphan_note_count"] == 0


def test_structurally_alone_notes_are_not_orphans(tmp_path):
    """Kökteki `.md` dosyaları ve `daily/` günlükleri yapısal olarak yalnızdır:
    makine her oturumda günlük yazar, bunlar yetim sayısını anlamsızlaştırırdı."""
    vault = tmp_path / "vault"
    _notes(
        vault,
        {
            "README.md": "kök dosya\n",
            "daily/2026-09-30.md": "günlük\n",
            "daily/v3/2026-09-30.md": "v3 günlük\n",
            "notlar/gercek-yetim.md": "bağlantısız\n",
        },
    )

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["orphan_note_count"] == 1  # yalnız notlar/gercek-yetim.md


def test_structurally_alone_notes_still_count_their_broken_links(tmp_path):
    """Yapısal yalnızlık yalnız YETİM sayımından muaf; kırık link yine sayılır."""
    vault = tmp_path / "vault"
    _notes(vault, {"daily/2026-09-30.md": "[[silinmis-not]]\n"})

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["broken_link_count"] == 1
    assert result["orphan_note_count"] == 0


def test_subfolder_of_root_is_a_real_orphan(tmp_path):
    """Kökteki ALT KLASÖRDEKİ yalnız not gerçek yetimdir (yalnız kök dosyası
    ve `daily/` muaf)."""
    vault = tmp_path / "vault"
    _notes(vault, {"proje/tek.md": "içerik\n"})

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["orphan_note_count"] == 1


def test_excluded_directories_are_not_counted(tmp_path):
    """`.claude`, `receipts` gibi dışlanan klasörler ne kırık link ne yetim
    sayar (harita'nın varsayılan dışlamalarıyla aynı küme)."""
    vault = tmp_path / "vault"
    _notes(
        vault,
        {
            ".claude/skills/x/SKILL.md": "[[sablon-yer-tutucu]]\n",
            "receipts/abc.md": "[[baska-yok]]\n",
            "📥 000-Inbox/Dump/ham.md": "[[ham-yok]]\n",
            "sub/node_modules/pkg/README.md": "[[paket-yok]]\n",
            "notlar/normal.md": "içerik\n",
        },
    )

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["broken_link_count"] == 0
    assert result["orphan_note_count"] == 1  # yalnız notlar/normal.md


def test_inbox_outside_dump_is_still_counted(tmp_path):
    """`📥 000-Inbox` dışlanmaz, yalnız `📥 000-Inbox/Dump` dışlanır."""
    vault = tmp_path / "vault"
    _notes(vault, {"📥 000-Inbox/hızlı.md": "[[yok]]\n"})

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["broken_link_count"] == 1


def test_collect_ignores_links_inside_code_blocks(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()

    (vault / "note-a.md").write_text(
        "# A\n\n```\n[[should-not-count]]\n```\n\nInline `[[also-skip]]` code.\n",
        encoding="utf-8",
    )

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["broken_link_count"] == 0


def test_collect_link_with_alias_or_heading_resolves_to_base_target(tmp_path):
    vault = tmp_path / "vault"
    _notes(
        vault,
        {
            "notlar/note-a.md": "See [[note-b|Alias Text]] and [[note-b#Heading]].\n",
            "notlar/note-b.md": "content\n",
        },
    )

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["broken_link_count"] == 0
    # note-a link veriyor, note-b (alias/heading'e rağmen temel hedefe çözülerek)
    # link alıyor -> ikisi de bağlı, yetim yok.
    assert result["orphan_note_count"] == 0


def test_collect_open_and_closed_threads(tmp_path):
    vault = tmp_path / "vault"
    companion = vault / "🔮 850-Companion"
    companion.mkdir(parents=True)

    threads_md = companion / "Threads.md"
    threads_md.write_text(
        "\n".join(
            [
                "## Thread 1",
                "**Status:** ✅",
                "",
                "## Thread 2",
                "**Status:** 🟡",
                "",
                "## Thread 3",
                "**Status:** 🔴",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["total_threads"] == 3
    assert result["open_threads"] == 2  # sadece ✅ kapali sayilir


def test_collect_heading_format_threads(tmp_path):
    vault = tmp_path / "vault"
    companion = vault / "🔮 850-Companion"
    companion.mkdir(parents=True)
    (companion / "Threads.md").write_text(
        "\n".join(
            [
                "## Active Threads",
                "### Thread: a (açık)",
                "### Thread: b",
                "## Closed",
                "### Thread: c",
            ]
        ),
        encoding="utf-8",
    )

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert (result["open_threads"], result["total_threads"]) == (2, 3)


def test_collect_missing_threads_file_returns_zero_threads(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("no threads file here\n", encoding="utf-8")

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["open_threads"] == 0
    assert result["total_threads"] == 0


def test_collect_returns_all_expected_keys(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert set(result.keys()) == {
        "broken_link_count",
        "orphan_note_count",
        "open_threads",
        "total_threads",
    }
