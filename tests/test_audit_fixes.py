"""監査（ai-audit-prompts / ultracode）で確定した finding の再現・回帰テスト。

各テストは plan_bug-security-quality-audit.md の finding ID に対応する。
"""

import time
from pathlib import Path

import pytest

from docsweep.config import Config, TypeDef, load_config
from docsweep.detect import detect_status, extract_summary
from docsweep.engine import apply_action, auto_sweep, promote_state, relabel_file, run_scan
from docsweep.states import StateModel, build_state_model

SM = StateModel()


def _write(p: Path, text: str, age_days: int = 0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if age_days:
        import os
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
    return p


def _cfg(root: Path):
    return load_config(explicit_roots=[str(root)], global_path=root / "no.yaml")


# ---- F-C/[00][04]: コードフェンス内の '# ' を H1 と誤検出しない ----

def test_fence_h1_not_misdetected():
    text = "```md\n# [完了] 例として書いたラベル\n```\n\n# [実行中] 本物のタイトル\n"
    d = detect_status(text=text, filename="plan_x.md", sm=SM)
    assert d.state_key == "in-progress"  # done ではなく本物の実行中
    assert d.title == "本物のタイトル"


def test_fence_h1_not_auto_archived(tmp_path: Path):
    """フェンス内 [完了] 例を含む実行中文書が --auto で誤移送されない。"""
    root = tmp_path / "dev"
    _write(root / "proj" / "plan_x.md",
           "```\n# [完了] 例\n```\n\n# [実行中] 生きてる作業\n\n## 概要\n\nx\n")
    moved = auto_sweep(_cfg(root), dry_run=True)
    assert not moved  # in-progress は移送対象にならない
    rec = run_scan(_cfg(root)).records[0]
    assert rec.state == "in-progress"


# ---- [00] relabel_file がフェンス内ではなく本物の H1 を書き換える ----

def test_relabel_targets_real_h1_not_fence(tmp_path: Path):
    p = _write(tmp_path / "plan_x.md",
               "```\n# [計画] フェンス内の例\n```\n\n# [実行中] 本物\n")
    assert relabel_file(p, "[完了]", _cfg(tmp_path)) is True
    out = p.read_text(encoding="utf-8")
    assert "# [計画] フェンス内の例" in out   # フェンスは無傷
    assert "# [完了] 本物" in out             # 本物の H1 を書換
    assert "# [実行中] 本物" not in out


# ---- 回帰防止: relabel が CRLF を全行 LF に潰さない（再調査で検出した退行）----

def test_relabel_preserves_crlf(tmp_path: Path):
    p = tmp_path / "plan_crlf.md"
    p.write_bytes(b"# [\xe8\xa8\x88\xe7\x94\xbb] t\r\n\r\nbody line\r\n")  # 「計画」CRLF
    assert relabel_file(p, "[完了]", _cfg(tmp_path)) is True
    data = p.read_bytes()
    assert b"\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")  # 生 LF が残っていない＝全行 CRLF 維持
    assert "# [完了] t" in data.decode("utf-8")


# ---- F-D: pending 型接頭辞を state と誤認して誤 conflict を立てない ----

def test_pending_prefix_no_false_conflict():
    from docsweep.config import TypeDef
    pending_type = TypeDef("pending", "pending_*.md", ("概要",), "概要", 180)
    d = detect_status(text="# [実行中] foo\n", filename="pending_foo.md", sm=SM, _type=pending_type)
    assert d.state_key == "in-progress"
    assert d.conflict is False


def test_filename_state_prefix_still_works():
    # state_type 形式（done_plan_x.md）の filename 検出は従来どおり動く（_type 無し）。
    d = detect_status(text="本文だけ\n", filename="done_plan_x.md", sm=SM)
    assert d.state_key == "done"


# ---- [07]: 非 UTF-8 文書を relabel で破壊しない ----

def test_relabel_skips_non_utf8(tmp_path: Path):
    p = tmp_path / "plan_bin.md"
    p.write_bytes(b"# [\x95\xb9] title\n\xff\xfe invalid utf8\n")
    before = p.read_bytes()
    assert relabel_file(p, "[完了]", _cfg(tmp_path)) is False
    assert p.read_bytes() == before  # 原本はバイト単位で不変


# ---- [06]: H1 が無い文書は relabel 失敗で移送中止（誤ラベルのまま archive しない）----

def test_discard_aborts_when_no_h1(tmp_path: Path):
    root = tmp_path / "dev"
    # frontmatter で状態を持つが本文に H1 が無い → relabel 不能。
    _write(root / "proj" / "plan_nofh1.md", "---\nstatus: watching\n---\n\n本文だけ。\n")
    cfg = _cfg(root)
    doc = next(d for d in run_scan(cfg).docs if Path(d.record.path).name == "plan_nofh1.md")
    with pytest.raises(ValueError):
        apply_action(doc, "discard", cfg, dry_run=False)
    # 移送されず元の場所に残る。
    assert (root / "proj" / "plan_nofh1.md").exists()
    assert not (root / "proj" / "archive" / "plan_nofh1.md").exists()


# ---- [12]: promote の無効な to_state を弾く ----

def test_promote_invalid_to_state_raises(tmp_path: Path):
    root = tmp_path / "dev"
    _write(root / "proj" / "plan_w.md", "# [様子見] w\n\n## 概要\n\nx\n")
    with pytest.raises(ValueError):
        promote_state(_cfg(root), from_state="watching", to_state="dnoe")
    # 誤って移送されていない。
    assert (root / "proj" / "plan_w.md").exists()
    assert not (root / "proj" / "archive" / "plan_w.md").exists()


# ---- [27]: moves.jsonl の ts が resume/relabel でも埋まる ----

def test_move_log_ts_filled_for_relabel(tmp_path: Path):
    import json
    root = tmp_path / "dev"
    _write(root / "proj" / "plan_w.md", "# [様子見] w\n\n## 概要\n\nx\n")
    cfg = _cfg(root)
    doc = next(d for d in run_scan(cfg).docs if Path(d.record.path).name == "plan_w.md")
    apply_action(doc, "relabel", cfg, to="計画", dry_run=False)
    log = (root / ".docsweep" / "moves.jsonl").read_text(encoding="utf-8").strip()
    entry = json.loads(log.splitlines()[-1])
    assert entry["op"] == "relabel"
    assert entry["ts"]  # 空文字でない


# ---- [05][08]: per-type archive_dir も枝刈りされ再 archive ループしない ----

def test_per_type_archive_dir_pruned(tmp_path: Path):
    root = tmp_path / "dev"
    _write(root / "proj" / "plan_done.md", "# [完了] d\n\n## 概要\n\nx\n")
    plan_type = TypeDef("plan", "plan_*.md", ("概要",), "概要", 90, archive_dir="_archived")
    cfg = Config(roots=[root], types=[plan_type])
    auto_sweep(cfg, dry_run=False)
    assert (root / "proj" / "_archived" / "plan_done.md").exists()
    # 2 回目は再検出されず移送対象ゼロ（無限増殖しない）。
    assert auto_sweep(cfg, dry_run=False) == []
    assert not (root / "proj" / "_archived" / "plan_done_2.md").exists()


# ---- [05][08]: multi-project（root 配下に各プロジェクトの archive/）でも枝刈りが効く ----

def test_multi_project_archive_pruning(tmp_path: Path):
    root = tmp_path / "dev"
    _write(root / "proj_a" / "archive" / "old_a.md", "# [完了] a\n")  # 既に archive 済
    _write(root / "proj_b" / "archive" / "old_b.md", "# [完了] b\n")
    _write(root / "proj_a" / "plan_live.md", "# [計画] live\n\n## 概要\n\nx\n")
    names = {Path(r.path).name for r in run_scan(_cfg(root)).records}
    assert "plan_live.md" in names      # 生きた文書は拾う
    assert "old_a.md" not in names      # 各プロジェクトの archive/ は枝刈り
    assert "old_b.md" not in names


# ---- [09]: ネスト archive_dir が docs/ ツリーを過剰枝刈りしない ----

def test_nested_archive_dir_does_not_overprune(tmp_path: Path):
    root = tmp_path / "dev"
    _write(root / "proj" / "docs" / "local" / "plan_live.md", "# [計画] a\n\n## 概要\n\nx\n")
    cfg = Config(roots=[root], archive_dir="docs/archive")
    names = {Path(r.path).name for r in run_scan(cfg).records}
    assert "plan_live.md" in names  # docs/ は消えない


# ---- [20]: StateModel の key/label 重複を fail-fast ----

def test_statemodel_duplicate_label_raises():
    with pytest.raises(ValueError):
        build_state_model([
            {"key": "done", "labels": {"ja": "完了"}, "archive": True},
            {"key": "legacy", "labels": {"ja": "完了"}},  # 同一ラベル
        ])


def test_statemodel_duplicate_key_raises():
    with pytest.raises(ValueError):
        build_state_model([
            {"key": "a", "labels": {"ja": "A"}},
            {"key": "a", "labels": {"ja": "B"}},  # 同一 key
        ])


# ---- [15]: プロジェクト .docsweep.yaml の相対 roots は project_dir 基準 ----

def test_project_relative_roots_resolved_against_project_dir(tmp_path: Path):
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / ".docsweep.yaml").write_text("roots:\n  - sub\n", encoding="utf-8")
    cfg = load_config(project_dir=proj, global_path=tmp_path / "none.yaml")
    assert cfg.roots == [(proj / "sub").resolve()]


# ---- [10]: docsweep new の topic パストラバーサルをサニタイズ ----

def test_new_doc_topic_path_traversal_sanitized(tmp_path: Path):
    from docsweep.templates_gen import new_doc
    (tmp_path / "docs").mkdir()
    doc = new_doc("plan", "../../evil", project_dir=tmp_path)
    assert doc.path.name == "plan_evil.md"
    assert ".." not in str(doc.path)
    # 生成先は docs/ 配下に閉じる。
    assert doc.path.parent == tmp_path / "docs"


# ---- [30]: extract_summary がコードフェンスを概要に拾わない ----

def test_extract_summary_skips_code_fence():
    text = "## 概要\n\n```\nコードは概要ではない\n```\n\nこれが本当の概要。\n"
    assert extract_summary(text, "概要") == "これが本当の概要。"
