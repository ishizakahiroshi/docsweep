"""docsweep init / undo CLI（UX W1 / P1, P12）."""

from __future__ import annotations

from pathlib import Path

from docsweep.cli import main
from docsweep.config import load_config
from docsweep.init_cmd import run_init
from docsweep.services.archive import archive_done


def test_init_creates_config(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    root = tmp_path / "work"
    root.mkdir()
    res = run_init(yes=True, root=str(root), lang="en", agent="none", global_path=cfg)
    assert res.created
    assert cfg.is_file()
    body = cfg.read_text(encoding="utf-8")
    assert "roots:" in body
    assert "lang: en" in body


def test_init_does_not_overwrite(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    root = tmp_path / "work"
    root.mkdir()
    run_init(yes=True, root=str(root), global_path=cfg)
    res2 = run_init(yes=True, root=str(root / "other"), global_path=cfg)
    assert not res2.created
    assert str(root) in cfg.read_text(encoding="utf-8")


def test_cli_undo_restores(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    src = proj / "plan_done.md"
    src.write_text("# [完了] x\n\n## 概要\n\na\n", encoding="utf-8")
    cfg = load_config(explicit_roots=[str(root)], global_path=tmp_path / "no.yaml")
    archive_done(config=cfg, paths=[str(src)])
    assert not src.exists()
    code = main(["undo", "--root", str(root), "--config", str(tmp_path / "no.yaml")])
    assert code == 0
    assert src.exists()
