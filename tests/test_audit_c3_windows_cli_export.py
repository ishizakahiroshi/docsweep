"""監査 C3: Windows 入出力・CLI エラー・ICS/export の決定的回帰。"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

import docsweep.context as context_module
import docsweep.export as export_module
from docsweep.cli import main
from docsweep.config import load_config
from docsweep.doctor import _max_project_last_scanned
from docsweep.linkcheck import FileStatus, LinkCheckResult


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _cfg(root: Path):
    return load_config(explicit_roots=[str(root)], global_path=root / "no-global.yaml")


def test_windows_clipboard_reader_handles_cp932_redirect(monkeypatch):
    import docsweep.cli.commands.write as write_module

    monkeypatch.setattr(write_module.sys, "platform", "win32")

    def fake_run(*args, **kwargs):
        assert kwargs["text"] is False
        assert "OutputEncoding" in args[0][-1]
        return types.SimpleNamespace(returncode=0, stdout="日本語\n".encode("cp932"))

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert write_module._read_clipboard() == "日本語\n"


def test_windows_clipboard_backend_failure_is_empty(monkeypatch):
    import docsweep.cli.commands.write as write_module

    monkeypatch.setattr(write_module.sys, "platform", "win32")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=1, stdout=b"garbage"),
    )

    assert write_module._read_clipboard() == ""


def test_windows_clipboard_writer_uses_unicode_powershell(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert context_module.to_clipboard("日本語\n") is True
    assert calls[0][1]["input"] == "日本語\n".encode()
    assert "Set-Clipboard" in calls[0][0][-1]


def test_ics_uid_is_stable_across_python_hash_seeds(tmp_path: Path):
    root = tmp_path / "root"
    _write(
        root / "proj" / "docs" / "plan_due.md",
        "---\ndue: 2026-08-23\n---\n# [計画] due\n",
    )
    repo = Path(__file__).resolve().parent.parent
    script = (
        "from pathlib import Path; "
        "from docsweep.config import load_config; "
        "from docsweep.ics_export import build_ics; "
        f"print(build_ics(load_config(explicit_roots=[r'{root}'], "
        "global_path=Path(r'" + str(root / "no-global.yaml") + "'))))"
    )
    outputs: list[str] = []
    for seed in ("1", "2"):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(repo)
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        outputs.append(proc.stdout)

    uids = [re.findall(r"^UID:(.+)$", output, flags=re.MULTILINE) for output in outputs]
    assert uids[0] == uids[1]


def test_export_omits_file_that_disappears_after_scan(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"
    disappearing = _write(root / "proj" / "docs" / "plan_disappear.md", "# [計画] gone\n")
    keep = _write(root / "proj" / "docs" / "plan_keep.md", "# [計画] keep\n")
    cfg = _cfg(root)
    real_collect = export_module.collect_export

    def collect_then_remove(*args, **kwargs):
        files, pairs = real_collect(*args, **kwargs)
        disappearing.unlink()
        return files, pairs

    monkeypatch.setattr(export_module, "collect_export", collect_then_remove)
    out = tmp_path / "bundle.zip"
    result = export_module.run_export(cfg, out=out)

    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("okf-manifest.json"))
        names = set(zf.namelist())
    paths = {item["path"] for item in manifest["files"]}
    assert result.file_count == len(paths)
    assert all(path in names for path in paths)
    assert not any(Path(path).name == disappearing.name for path in paths)
    assert any(Path(path).name == keep.name for path in paths)


def test_export_reflects_duplicate_arcname_in_manifest_and_index(tmp_path: Path):
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    for root, body in ((root_a, "one"), (root_b, "two")):
        # C5 disambiguates same-name live projects.  Archive collection still
        # derives the legacy project basename, so it remains a real duplicate
        # arcname case for the bundle writer to handle.
        _write(root / "same" / "archive" / "plan_x.md", f"# [計画] {body}\n")
    cfg = load_config(
        explicit_roots=[str(root_a), str(root_b)],
        global_path=tmp_path / "no-global.yaml",
    )
    out = tmp_path / "bundle.zip"
    result = export_module.run_export(cfg, out=out, include_archive=True)

    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("okf-manifest.json"))
        index = zf.read("index.md").decode("utf-8")
        names = set(zf.namelist())
    paths = [item["path"] for item in manifest["files"]]
    assert len(paths) == 2
    assert len(set(paths)) == 2
    assert set(paths).issubset(names)
    assert all(f"]({path})" in index for path in paths)
    assert result.file_count == len(paths)
    assert any("__1.md" in path for path in paths)


def test_unknown_command_returns_usage_error_but_existing_dir_scans(tmp_path: Path, capsys):
    assert main(["trige"]) == 2
    assert "unknown command" in capsys.readouterr().err

    root = tmp_path / "root"
    _write(root / "proj" / "docs" / "plan_x.md", "# [計画] x\n")
    assert main([str(root), "--all"]) == 0
    assert "plan_x.md" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        lambda path: ["ics", "--root", str(path), "--out", str(path)],
        lambda path: ["export", "--root", str(path), "--out", str(path)],
    ],
)
def test_cli_environment_oserror_is_short_exit_two(tmp_path: Path, argv, capsys):
    rc = main(argv(tmp_path))
    captured = capsys.readouterr()

    assert rc == 2
    assert "Traceback" not in captured.err
    assert captured.err.strip()


def test_doctor_quotes_special_sqlite_path(tmp_path: Path):
    # ``?`` is not a legal Windows filename character; # and % are enough to
    # exercise URI fragment/escape handling on this Windows test host.
    db = tmp_path / "index#percent%.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE projects (last_scanned TEXT)")
        conn.execute("INSERT INTO projects(last_scanned) VALUES (?)", ("ok",))
        conn.commit()
    finally:
        conn.close()

    assert _max_project_last_scanned(db) == "ok"


def test_linkcheck_human_output_is_cp932_safe(monkeypatch, capsys, tmp_path: Path):
    from docsweep.cli.commands import read as read_commands

    result = LinkCheckResult(
        plan_path=str(tmp_path / "plan.md"),
        plan_name="plan.md",
        declared_files=[FileStatus(path="missing.py", exists=False, touches_since_plan=0, mentioned_in_commit=False)],
        progress_hint="partial",
    )
    monkeypatch.setattr(read_commands, "_build_config", lambda args: object())
    monkeypatch.setattr("docsweep.linkcheck.linkcheck", lambda config, target=None: [result])

    args = types.SimpleNamespace(file=None, json=False)
    assert read_commands.cmd_linkcheck(args) == 0
    output = capsys.readouterr().out
    assert "NG missing.py" in output
    assert "✓" not in output and "✗" not in output


# --- cp932 コンソールへの人間向け出力（C3 手動 gate で見つかった追加分） -------
#
# C3 の決定的回帰は linkcheck / inject だけを名指ししていたため、同じ型が
# `scan` と `doctor` に残っていた。2026-08-29 の ja-JP Windows 実機確認で
# `PYTHONIOENCODING=cp932` かつ stdout リダイレクトにすると両方が
# UnicodeEncodeError の raw traceback で落ちることを実測した。
#
# 原因は 2 層ある。
#   1. 装飾 glyph（`—` `·` `≥` `≈` `⏻` `↔`）を人間向け出力へ直接書いていた
#   2. 文書側のデータ（要約に含まれる `–` 等）はそもそも ASCII 化できない
# 1 は各 print を ASCII へ、2 は描画境界だけを緩める形で塞いだ。


def _cp932_stream():
    import io

    return io.TextIOWrapper(io.BytesIO(), encoding="cp932", newline="")


def test_human_output_streams_are_softened_for_a_cp932_console(monkeypatch):
    """人間向け出力では、コンソールが表現できない文字で落ちない。"""
    from docsweep.cli import _soften_console_encoding

    out = _cp932_stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", _cp932_stream())

    _soften_console_encoding(False)
    # 文書側のデータに現れる en dash。ASCII 化はできないが落ちてもいけない。
    print("plan_x.md - \u2013 \u2014 \u00b7 \U0001F600")
    out.flush()
    rendered = out.buffer.getvalue().decode("cp932")
    assert "plan_x.md" in rendered
    # \u843d\u3061\u306a\u3044\u4ee3\u308f\u308a\u306b\u3001\u5931\u308f\u308c\u305f\u6587\u5b57\u304c\u30d0\u30c3\u30af\u30b9\u30e9\u30c3\u30b7\u30e5\u8868\u8a18\u3067\u898b\u3048\u308b\u5f62\u306b\u6b8b\u308b\u3002
    assert "\\u2013" in rendered
    assert "\\U0001f600" in rendered


def test_json_output_stream_is_left_exact(monkeypatch):
    """``--json`` は stdout のバイト列が契約なので緩めない。"""
    from docsweep.cli import _soften_console_encoding

    out = _cp932_stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", _cp932_stream())

    _soften_console_encoding(True)
    with pytest.raises(UnicodeEncodeError):
        print("\u2013")
        out.flush()


def test_json_unicode_error_is_short_exit_two_not_a_traceback(monkeypatch, capsys, tmp_path: Path):
    """表現できない ``--json`` payload は raw traceback ではなく exit 2。"""
    from docsweep.cli import _DISPATCH, main as cli_main

    def _boom(args):
        raise UnicodeEncodeError("cp932", "\u2013", 0, 1, "illegal multibyte sequence")

    monkeypatch.setitem(_DISPATCH, "doctor", _boom)
    assert cli_main(["doctor", "--json"]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "cp932" in err


@pytest.mark.parametrize("command", ["scan", "doctor", "pending", "cookbook"])
def test_cli_human_output_survives_a_cp932_redirect(command: str, tmp_path: Path):
    """実プロセスで cp932 リダイレクトを再現し、traceback で終わらないこと。

    ``_soften_console_encoding`` は起動時に stdout の encoding を見るため、
    monkeypatch ではなく実際の子プロセスで確認する。
    """
    work = tmp_path / "proj" / "docs" / "local"
    work.mkdir(parents=True)
    (tmp_path / "proj" / ".git").mkdir()
    # 要約に cp932 で表現できない文字を持つ文書。
    (work / "plan_dash.md").write_text(
        "# [計画] en dash \u2013 と em dash \u2014 を含む見出し\n\n本文。\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp932"
    env["DOCSWEEP_INDEX_DB"] = str(tmp_path / "index.db")
    argv = [sys.executable, "-m", "docsweep", command]
    if command in ("scan", "pending"):
        argv += ["--root", str(tmp_path / "proj")]
    proc = subprocess.run(
        argv,
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        timeout=120,
    )
    assert b"Traceback" not in proc.stderr, proc.stderr.decode("cp932", "replace")
    assert b"UnicodeEncodeError" not in proc.stderr
