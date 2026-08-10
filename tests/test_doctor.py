"""docsweep doctor（UX W1 / P3）."""

from __future__ import annotations

from pathlib import Path

from docsweep.doctor import format_human, run_doctor
from docsweep.init_cmd import run_init


def test_doctor_warns_without_config(tmp_path: Path):
    missing = tmp_path / "no-such-config.yaml"
    db = tmp_path / "index.db"
    report = run_doctor(global_path=missing, index_db=db)
    ids = {i.id: i for i in report.items}
    assert ids["config"].status == "warn"
    assert ids["index"].status == "warn"
    assert "init" in (ids["config"].fix or "")


def test_doctor_ok_with_init_config(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    root = tmp_path / "proj"
    root.mkdir()
    run_init(yes=True, root=str(root), lang="ja", agent="none", global_path=cfg_path)
    db = tmp_path / "index.db"
    # empty db file still counts as exists but very new
    db.write_bytes(b"")
    report = run_doctor(global_path=cfg_path, index_db=db)
    ids = {i.id: i for i in report.items}
    assert ids["config"].status == "ok"
    assert ids["roots"].status == "ok"
    text = format_human(report)
    assert "config.yaml" in text


def test_cli_doctor_json(tmp_path: Path, monkeypatch):
    from docsweep.cli import main

    cfg_path = tmp_path / "config.yaml"
    root = tmp_path / "proj"
    root.mkdir()
    run_init(yes=True, root=str(root), agent="none", global_path=cfg_path)
    monkeypatch.setenv("DOCSWEEP_INDEX_DB", str(tmp_path / "idx.db"))
    code = main(["doctor", "--json", "--config", str(cfg_path)])
    # missing index may still return 0 if no fail-level roots; index missing is warn
    assert code in (0, 1)
