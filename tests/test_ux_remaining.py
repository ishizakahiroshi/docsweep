"""UX 残り実装: excluded / project / split / review-week."""

from __future__ import annotations

from pathlib import Path

from docsweep import excluded as ex
from docsweep.cli import main
from docsweep.config import load_config
from docsweep.engine import run_scan
from docsweep.excluded import disable_project, enable_project, is_excluded
from docsweep.templates_gen import new_split_plans


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_exclude_filters_scan(tmp_path: Path, monkeypatch):
    root = tmp_path / "dev"
    a = root / "proj_a"
    b = root / "proj_b"
    _write(a / "plan_a.md", "# [計画] a\n\n## 概要\n\nx\n")
    _write(b / "plan_b.md", "# [計画] b\n\n## 概要\n\ny\n")
    excl = tmp_path / "excluded.json"
    monkeypatch.setattr(ex, "EXCLUDED_PATH", excl)
    cfg = load_config(explicit_roots=[str(root)], global_path=tmp_path / "nog.yaml")
    assert len(run_scan(cfg).records) == 2
    disable_project(a)
    assert is_excluded(a)
    recs = run_scan(cfg).records
    names = {Path(r.path).name for r in recs}
    assert "plan_a.md" not in names
    assert "plan_b.md" in names
    enable_project(a)
    assert not is_excluded(a)
    assert len(run_scan(cfg).records) == 2


def test_new_split_plans(tmp_path: Path):
    created = new_split_plans("big-topic", n=2, project_dir=tmp_path)
    assert len(created) == 3
    parent = created[0].path.read_text(encoding="utf-8")
    assert "related:" in parent
    child = created[1].path.read_text(encoding="utf-8")
    assert created[0].path.name in child


def test_cli_project_and_review_week(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "p"
    _write(proj / "plan_x.md", "# [計画] x\n\n## 概要\n\nhi\n")
    assert main(["project", "list", "--root", str(root), "--json"]) == 0
    assert main(["review-week", "--root", str(root), "--json"]) == 0
