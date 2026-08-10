"""UX 最終バッチ: secrets / history / cookbook / ics / memory / read-only."""

from __future__ import annotations

from pathlib import Path

from docsweep.cli import main
from docsweep.config import load_config
from docsweep.cookbook import render_cookbook
from docsweep.history import read_history
from docsweep.ics_export import build_ics
from docsweep.memory_scan import scan_memory
from docsweep.secrets_guard import scan_secrets
from docsweep.services.archive import archive_done
from docsweep.services.content import update_content


def test_scan_secrets_detects_github_pat():
    hits = scan_secrets("token ghp_" + ("a" * 36))
    assert any(h["kind"] == "github_pat" for h in hits)


def test_content_warns_on_secret(tmp_path: Path):
    p = tmp_path / "plan_x.md"
    p.write_text("# [計画] x\n\n## 概要\n\nok\n", encoding="utf-8")
    res = update_content(
        p,
        "# [計画] x\n\n## 概要\n\nkey = sk-" + ("b" * 40) + "\n",
    )
    assert res.warnings
    assert any("secret" in w or "sk" in w or "openai" in w for w in res.warnings)


def test_history_and_ics(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "p"
    proj.mkdir(parents=True)
    src = proj / "plan_done.md"
    src.write_text("# [完了] x\n\n## 概要\n\na\n", encoding="utf-8")
    cfg = load_config(explicit_roots=[str(root)], global_path=tmp_path / "nog.yaml")
    archive_done(config=cfg, paths=[str(src)])
    hist = read_history(cfg, limit=10)
    assert hist.entries
    ics = build_ics(cfg)
    assert "BEGIN:VCALENDAR" in ics


def test_cookbook_and_memory_cli():
    assert "morning" in render_cookbook()
    assert main(["cookbook", "morning"]) == 0
    assert main(["memory", "--json"]) == 0
    res = scan_memory()
    assert "files" in res.to_dict()


def test_serve_read_only_flag_create_app(tmp_path: Path):
    from docsweep.server.app import create_app

    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "nog.yaml")
    app = create_app(cfg, token="t", read_only=True)
    assert app.state.docsweep.read_only is True
