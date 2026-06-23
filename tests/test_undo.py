"""services/archive.py の Undo 機能（undo_last_batch）のテスト。"""

from __future__ import annotations

from pathlib import Path

from docsweep.config import load_config
from docsweep.services.archive import archive_done, undo_last_batch


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _cfg(root: Path):
    return load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")


def test_undo_restores_single_archived_file(tmp_path: Path):
    """1 件 archive → Undo で元の場所に戻る。"""
    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    src = _write(proj / "plan_done.md", "# [完了] x\n\n## 概要\n\na\n")
    cfg = _cfg(root)

    arc_res = archive_done(config=cfg, paths=[str(src)])
    assert len(arc_res.moved) == 1
    assert not src.exists()  # 元の場所から消えてる
    assert Path(arc_res.moved[0].dst).exists()  # archive 配下にある

    undo_res = undo_last_batch(config=cfg)
    assert undo_res.batch_id is not None
    assert len(undo_res.restored) == 1
    assert src.exists()  # 元の場所に戻った
    assert not Path(arc_res.moved[0].dst).exists()  # archive 配下から消えた


def test_undo_restores_bulk_batch(tmp_path: Path):
    """複数件 archive → Undo で全件戻る（同一 batch_id）。"""
    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    files = [_write(proj / f"plan_done_{i}.md", f"# [完了] x{i}\n\n## 概要\n\na\n") for i in range(3)]
    cfg = _cfg(root)

    arc_res = archive_done(config=cfg, paths=[str(f) for f in files])
    assert len(arc_res.moved) == 3
    for f in files:
        assert not f.exists()

    undo_res = undo_last_batch(config=cfg)
    assert undo_res.batch_id is not None
    assert len(undo_res.restored) == 3
    for f in files:
        assert f.exists()


def test_undo_does_not_double_restore(tmp_path: Path):
    """Undo は同じバッチを 2 回戻さない（restore マークが効く）。"""
    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    src = _write(proj / "plan_done.md", "# [完了] x\n\n## 概要\n\na\n")
    cfg = _cfg(root)
    archive_done(config=cfg, paths=[str(src)])
    undo_last_batch(config=cfg)
    # 2 度目の Undo は対象なし
    res2 = undo_last_batch(config=cfg)
    assert res2.batch_id is None
    assert res2.restored == []


def test_undo_targets_only_latest_unrestored_batch(tmp_path: Path):
    """2 バッチ archive → Undo は最新バッチだけ戻す（古いバッチはそのまま）。"""
    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    f1 = _write(proj / "plan_done_old.md", "# [完了] old\n\n## 概要\n\nold\n")
    cfg = _cfg(root)
    archive_done(config=cfg, paths=[str(f1)])
    # 新しいバッチ
    f2 = _write(proj / "plan_done_new.md", "# [完了] new\n\n## 概要\n\nnew\n")
    archive_done(config=cfg, paths=[str(f2)])

    undo_res = undo_last_batch(config=cfg)
    # 最新（new）だけ戻る
    assert len(undo_res.restored) == 1
    assert f2.exists()
    assert not f1.exists()  # 古いバッチはそのまま archive 配下


def test_undo_skips_when_destination_exists(tmp_path: Path):
    """復元先に同名ファイルが既にあったら、Undo は failed[] へ振り分け。"""
    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    src = _write(proj / "plan_done.md", "# [完了] x\n\n## 概要\n\na\n")
    cfg = _cfg(root)
    archive_done(config=cfg, paths=[str(src)])
    # 復元先に新しいファイルを作っておく（衝突）
    src.write_text("# 新規ファイル\n", encoding="utf-8")
    undo_res = undo_last_batch(config=cfg)
    assert len(undo_res.failed) == 1
    assert "既に" in undo_res.failed[0]["error"]
    # 元のファイル（新規ファイル）はそのまま
    assert src.read_text(encoding="utf-8") == "# 新規ファイル\n"


def test_undo_returns_empty_when_no_log(tmp_path: Path):
    """moves.jsonl が無いプロジェクトでも Undo はクラッシュせず空結果。"""
    root = tmp_path / "dev"
    (root / "proj").mkdir(parents=True)
    cfg = _cfg(root)
    res = undo_last_batch(config=cfg)
    assert res.batch_id is None
    assert res.restored == []


def test_undo_skips_batch_without_batch_id(tmp_path: Path):
    """batch_id を持たない（旧形式）archive エントリは Undo 対象外。"""
    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    src = _write(proj / "plan_done.md", "# [完了] x\n\n## 概要\n\na\n")
    cfg = _cfg(root)
    # batch_id 無しで archive（engine.archive_doc 直接呼び）
    from docsweep.engine import archive_doc, run_scan
    docs = run_scan(cfg).docs
    archive_doc(docs[0], cfg)  # batch_id=None
    undo_res = undo_last_batch(config=cfg)
    assert undo_res.batch_id is None
    assert undo_res.restored == []
