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


def test_collect_counts_broken_links_and_orphans(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()

    # note-a linke note-b (var) ve note-missing (yok)
    (vault / "note-a.md").write_text(
        "# Note A\n\nSee [[note-b]] and [[note-missing]].\n", encoding="utf-8"
    )
    # note-b var, note-a tarafından referans alınıyor -> orphan değil
    (vault / "note-b.md").write_text("# Note B\ncontent\n", encoding="utf-8")
    # note-c hiç kimse tarafından referans alınmıyor -> orphan
    (vault / "note-c.md").write_text("# Note C\ncontent\n", encoding="utf-8")

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["broken_link_count"] == 1  # note-missing
    # note-b, note-a tarafından referans alınıyor -> orphan değil.
    # note-a ve note-c'ye kimse referans vermiyor -> ikisi de orphan.
    assert result["orphan_note_count"] == 2


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
    vault.mkdir()

    (vault / "note-a.md").write_text(
        "See [[note-b|Alias Text]] and [[note-b#Heading]].\n", encoding="utf-8"
    )
    (vault / "note-b.md").write_text("content\n", encoding="utf-8")

    result = vault_status.collect({"vault": {"path": str(vault)}})

    assert result["broken_link_count"] == 0
    # note-b, note-a tarafından referans alınıyor (alias/heading'e rağmen temel
    # hedefe çözülüyor) -> orphan değil; note-a'ya kimse referans vermiyor -> orphan.
    assert result["orphan_note_count"] == 1


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
