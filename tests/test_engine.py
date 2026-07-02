import time
from pathlib import Path

import pytest

from docsweep.config import load_config
from docsweep.engine import apply_action, auto_sweep, run_scan


def _write(p: Path, text: str, age_days: int = 0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if age_days:
        old = time.time() - age_days * 86400
        import os
        os.utime(p, (old, old))
    return p


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "dev"
    # プロジェクト proj_a
    _write(root / "proj_a" / "docs" / "local" / "plan_done.md", "# [完了] 終わった計画\n\n## 概要\n\n片付いた。\n")
    _write(root / "proj_a" / "docs" / "local" / "plan_watch.md", "# [様子見] 寝かせ中\n\n## 概要\n\n再発確認中。\n")
    _write(root / "proj_a" / "docs" / "local" / "bugfix_x_2026-01-01.md", "# [廃止] 不要になった\n\n## 症状\n\n出ていた。\n")
    # プロジェクト proj_b に陳腐化 plan
    _write(root / "proj_b" / "plan_stale.md", "# [計画] 古い計画\n\n## 概要\n\nずっと放置。\n", age_days=200)
    # archive 配下は無視されること
    _write(root / "proj_a" / "archive" / "plan_old.md", "# [完了] 既に archive 済\n")
    return root


def _cfg(root: Path):
    return load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")


def test_scan_finds_docs_excludes_archive(workspace: Path):
    cfg = _cfg(workspace)
    result = run_scan(cfg)
    names = {Path(r.path).name for r in result.records}
    assert "plan_done.md" in names
    assert "plan_old.md" not in names  # archive は除外


def test_non_type_md_is_skipped(tmp_path: Path):
    """plan_/bugfix_/pending_ に一致しない .md（LICENSE/README 等）は拾わない。"""
    root = tmp_path / "dev"
    _write(root / "proj" / "docs" / "local" / "plan_real.md", "# [計画] x\n\n## 概要\n\na\n")
    _write(root / "proj" / "README.md", "# Readme\n")
    _write(root / "proj" / "node_dep" / "marked-LICENSE.md", "MIT License\n")
    cfg = _cfg(root)
    names = {Path(r.path).name for r in run_scan(cfg).records}
    assert names == {"plan_real.md"}


def test_project_from_git_marker(tmp_path: Path):
    """深い入れ子でも最寄りの .git のフォルダ名をプロジェクトにする（構成非依存）。"""
    root = tmp_path / "dev"
    proj = root / "github" / "public" / "docsweep"
    (proj / ".git").mkdir(parents=True)  # git リポジトリ境界
    _write(proj / "docs" / "local" / "plan_x.md", "# [計画] x\n\n## 概要\n\na\n")
    cfg = _cfg(root)
    rec = run_scan(cfg).records[0]
    assert rec.project == "docsweep"
    assert rec.project_root.endswith("/docsweep")


def test_project_marker_pyproject(tmp_path: Path):
    """.git が無くても pyproject.toml 等のマーカーで境界判定する。"""
    root = tmp_path / "dev"
    proj = root / "myproj"
    proj.mkdir(parents=True)
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    _write(proj / "docs" / "plan_y.md", "# [計画] y\n\n## 概要\n\nb\n")
    rec = run_scan(_cfg(root)).records[0]
    assert rec.project == "myproj"


def test_project_fallback_no_marker(tmp_path: Path):
    """マーカーが一切無ければルート直下の先頭セグメントへフォールバック。"""
    root = tmp_path / "dev"
    _write(root / "loose" / "docs" / "local" / "plan_z.md", "# [計画] z\n\n## 概要\n\nc\n")
    rec = run_scan(_cfg(root)).records[0]
    assert rec.project == "loose"


def test_archive_lands_in_project_root(tmp_path: Path):
    """archive は検出したプロジェクト境界（.git のあるフォルダ）の archive/ に入る。"""
    root = tmp_path / "dev"
    proj = root / "group" / "myrepo"
    (proj / ".git").mkdir(parents=True)
    _write(proj / "docs" / "local" / "plan_done.md", "# [完了] x\n\n## 概要\n\nd\n")
    auto_sweep(_cfg(root), dry_run=False)
    assert (proj / "archive" / "plan_done.md").exists()


def test_watching_not_auto_moved(workspace: Path):
    cfg = _cfg(workspace)
    moved = auto_sweep(cfg, dry_run=True)
    moved_src = {Path(m.src).name for m in moved}
    assert "plan_done.md" in moved_src      # done は移送
    assert "bugfix_x_2026-01-01.md" in moved_src  # discarded も移送
    assert "plan_watch.md" not in moved_src  # watching は絶対に触らない


def test_stale_plan_flagged_needs_decision(workspace: Path):
    cfg = _cfg(workspace)
    result = run_scan(cfg)
    stale = next(r for r in result.records if Path(r.path).name == "plan_stale.md")
    assert "stale" in stale.flags
    assert "needs_decision" in stale.flags


def test_auto_sweep_moves_to_archive(workspace: Path):
    cfg = _cfg(workspace)
    auto_sweep(cfg, dry_run=False)
    assert not (workspace / "proj_a" / "docs" / "local" / "plan_done.md").exists()
    assert (workspace / "proj_a" / "archive" / "plan_done.md").exists()
    # 移動ログが残る
    assert (workspace / ".docsweep" / "moves.jsonl").exists()


def test_auto_sweep_respects_project_docsweep_yaml(workspace: Path):
    """対象プロジェクト自身の .docsweep.yaml の archive_dir が cwd / --project-dir 非依存で効く。

    経緯: sweep は複数プロジェクト横断で動くのに、archive 先が起動時の単一 config
    でしか解決されず、プロジェクトの .docsweep.yaml が --project-dir を明示しないと
    無視されていた（2026-07-03 docsweep 自身の棚卸しで顕在化）。
    """
    (workspace / "proj_a" / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n", encoding="utf-8"
    )
    cfg = _cfg(workspace)  # project_dir を渡さない（--project-dir なし相当）
    auto_sweep(cfg, dry_run=False)
    assert not (workspace / "proj_a" / "docs" / "local" / "plan_done.md").exists()
    assert (workspace / "proj_a" / "docs" / "local" / "archive" / "plan_done.md").exists()
    # 設定を持たない従来プロジェクトの挙動（既定 archive/）は
    # test_auto_sweep_moves_to_archive が担保する


def test_auto_sweep_dry_run_previews_project_archive_dir(workspace: Path):
    """dry-run の dst も per-project 設定を反映する（下見と本実行のズレ防止）。"""
    (workspace / "proj_a" / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n", encoding="utf-8"
    )
    cfg = _cfg(workspace)
    moved = auto_sweep(cfg, dry_run=True)
    dsts = {Path(m.src).name: m.dst for m in moved}
    assert dsts["plan_done.md"].endswith("proj_a/docs/local/archive/plan_done.md")
    # dry-run なので実ファイルは動かない
    assert (workspace / "proj_a" / "docs" / "local" / "plan_done.md").exists()


def test_collision_dedupe(workspace: Path):
    cfg = _cfg(workspace)
    # 先に archive に同名を置く
    dest = workspace / "proj_a" / "archive"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "plan_done.md").write_text("既存\n", encoding="utf-8")
    auto_sweep(cfg, dry_run=False)
    assert (dest / "plan_done.md").exists()
    assert (dest / "plan_done_2.md").exists()


def test_apply_discard_archives(workspace: Path):
    cfg = _cfg(workspace)
    result = run_scan(cfg)
    watch = next(d for d in result.docs if Path(d.record.path).name == "plan_watch.md")
    entry = apply_action(watch, "discard", cfg, dry_run=False)
    assert entry.op == "discard"
    assert (workspace / "proj_a" / "archive" / "plan_watch.md").exists()


def test_apply_promote_watching_to_done(workspace: Path):
    cfg = _cfg(workspace)
    result = run_scan(cfg)
    watch = next(d for d in result.docs if Path(d.record.path).name == "plan_watch.md")
    assert "promote" in watch.record.allowed_actions
    entry = apply_action(watch, "promote", cfg, dry_run=False)
    assert entry.status == "done"
    assert (workspace / "proj_a" / "archive" / "plan_watch.md").exists()


def test_apply_rejects_disallowed_action(workspace: Path):
    cfg = _cfg(workspace)
    result = run_scan(cfg)
    done = next(d for d in result.docs if Path(d.record.path).name == "plan_done.md")
    # done に discard は許可されない
    with pytest.raises(ValueError):
        apply_action(done, "discard", cfg, dry_run=True)
