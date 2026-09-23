"""CLI コマンドの出力が表示言語 en で英語になること（docsweep/cli/commands/*.py）。

既存テストは conftest で ja に固定されている。ここでは ``--lang en`` か
``DOCSWEEP_LANG=en`` で英語へ切り替え、各コマンドの文言が辞書から出ることを確かめる。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from docsweep.cli import main
from docsweep.i18n import use_lang

JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


@pytest.fixture
def english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCSWEEP_LANG", "en")


# ---- cli/__init__.py -------------------------------------------------------------


def test_unknown_command_is_english(capsys) -> None:
    assert main(["trige", "--lang", "en"]) == 2

    first_line = capsys.readouterr().err.splitlines()[0]
    assert first_line == "docsweep: unknown command or scan directory: trige"


def test_json_encoding_error_is_english(monkeypatch: pytest.MonkeyPatch, capsys, english) -> None:
    from docsweep.cli import _DISPATCH

    def _boom(args):
        raise UnicodeEncodeError("cp932", "–", 0, 1, "illegal multibyte sequence")

    monkeypatch.setitem(_DISPATCH, "doctor", _boom)

    assert main(["doctor", "--json"]) == 2
    err = capsys.readouterr().err
    assert "the output encoding cp932 cannot represent this content" in err


# ---- cli/commands/write.py -------------------------------------------------------


def test_sweep_with_nothing_to_move_is_english(tmp_path: Path, capsys) -> None:
    assert main(["sweep", "--root", str(tmp_path), "--lang", "en"]) == 0

    out = capsys.readouterr().out
    assert "Nothing to archive (no files with a confirmed done/discarded label)" in out


def test_moves_summary_is_english() -> None:
    from types import SimpleNamespace

    from docsweep.cli.commands.write import _print_moves_summary
    from docsweep.config import Config

    moved = [SimpleNamespace(status="done", project="app")]
    with use_lang("en"):
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            _print_moves_summary(moved, Config(), action="promote", dry_run=True)

    text = buffer.getvalue()
    assert "To be promoted in total: 1 files (1 projects)" in text
    assert "  By project:" in text
    assert "    app: 1 files" in text


def test_claim_missing_file_is_english(tmp_path: Path, capsys, english) -> None:
    assert main(["claim", str(tmp_path / "missing.md")]) == 2

    assert f"File not found: {tmp_path / 'missing.md'}" in capsys.readouterr().err


# ---- cli/commands/read.py --------------------------------------------------------


def test_pending_none_is_english(tmp_path: Path, capsys) -> None:
    assert main(["pending", "--root", str(tmp_path), "--lang", "en"]) == 0

    assert "No pending documents." in capsys.readouterr().out


def test_pending_row_is_english(tmp_path: Path, capsys) -> None:
    doc = tmp_path / "proj" / "docs" / "pending_x.md"
    doc.parent.mkdir(parents=True)
    (tmp_path / "proj" / ".git").mkdir()
    doc.write_text("# [保留] x\n", encoding="utf-8")

    assert main(["pending", "--root", str(tmp_path), "--lang", "en"]) == 0

    out = capsys.readouterr().out
    assert "[Pending]" in out
    assert "pending_x.md" in out


# ---- cli/commands/move.py --------------------------------------------------------


def test_mv_errors_are_english(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch, english) -> None:
    project = tmp_path / "repo"
    (project / "docs" / "local").mkdir(parents=True)
    monkeypatch.chdir(project)

    code = main(["mv", "docs/local/missing.md", "--to", "docs/local/sub", "--project-dir", str(project)])

    err = capsys.readouterr().err
    assert code == 2
    assert "mv: File not found: docs/local/missing.md" in err
    assert "mv: nothing was moved" in err


def test_mv_json_errors_are_english(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch, english) -> None:
    project = tmp_path / "repo"
    (project / "docs" / "local").mkdir(parents=True)
    monkeypatch.chdir(project)

    main(["mv", "docs/local/missing.md", "--to", "docs/local/sub", "--project-dir", str(project), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"] == ["File not found: docs/local/missing.md"]


# ---- cli/commands/index.py -------------------------------------------------------


def test_index_stats_is_english(tmp_path: Path, capsys) -> None:
    assert main(["index-stats", "--root", str(tmp_path), "--lang", "en"]) == 0

    out = capsys.readouterr().out
    assert "Rows: projects=0 files=0" in out
    assert "embedding: none" in out
    assert "mtime range:" in out
    assert not JAPANESE.search(out)


# ---- cli/commands/workspace.py ---------------------------------------------------


def test_workspace_option_conflict_is_english(tmp_path: Path, capsys, english) -> None:
    code = main(
        [
            "workspace", "migrate-release-tracking", "--apply",
            "--apply-manifest", str(tmp_path / "manifest.json"),
        ]
    )

    assert code == 2
    assert (
        "workspace migration: --apply and --apply-manifest cannot be used together"
        in capsys.readouterr().err
    )


def test_workspace_prompt_is_english() -> None:
    from docsweep.i18n import t

    with use_lang("en"):
        prompt = t("cli_workspace.prompt_enable", root="/repo")

    assert prompt == "/repo has no release tracking configured. Enable it? [y=enable / n=skip / q=cancel]: "


# ---- cli/commands/excluded.py ----------------------------------------------------


def test_config_usage_is_english(capsys, english) -> None:
    assert main(["config"]) == 2

    out = capsys.readouterr().out
    assert out.startswith("usage: docsweep config <key> [<value>]")
    assert "(allowed keys:" in out


# ---- cli/commands/serve.py -------------------------------------------------------


def test_serve_without_web_extra_is_english(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch, english
) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    monkeypatch.chdir(tmp_path)

    assert main(["serve", "--no-browser"]) == 3

    captured = capsys.readouterr()
    assert "The Web UI requires the web extra: pip install 'docsweep[web]'" in captured.err
    assert not JAPANESE.search(captured.out + captured.err)


# ---- cli/commands/init.py --------------------------------------------------------


def test_undo_nothing_is_english(tmp_path: Path, capsys) -> None:
    assert main(["undo", "--root", str(tmp_path), "--lang", "en"]) == 1

    assert "Nothing to undo (already restored, or no batch_id)" in capsys.readouterr().out


# ---- cli/commands/inject.py ------------------------------------------------------


def test_eject_dry_run_is_english(tmp_path: Path, capsys, english) -> None:
    assert main(["eject", "--project", str(tmp_path), "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert " (dry-run): removed=-" in out
    assert not JAPANESE.search(out)


# ---- cli/commands/provenance.py --------------------------------------------------


def test_provenance_delegation_is_english(capsys) -> None:
    from docsweep.cli.commands.provenance import _emit

    with use_lang("en"):
        assert _emit({"status": "delegated"}, as_json=False) == 0

    assert (
        "provenance: delegated to repo management (the repo-specific skill); "
        "the general ledger was not changed"
    ) in capsys.readouterr().out


# ---- cli/commands/mcp.py ---------------------------------------------------------


def test_mcp_without_extra_is_english(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    import docsweep

    monkeypatch.delattr(docsweep, "mcp_server", raising=False)
    monkeypatch.setitem(sys.modules, "docsweep.mcp_server", None)

    assert main(["mcp", "--root", str(tmp_path), "--lang", "en"]) == 3

    assert "MCP requires the mcp extra: pip install 'docsweep[mcp]'" in capsys.readouterr().err
