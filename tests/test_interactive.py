"""docsweep triage --review（インタラクティブ triage）のテスト。

キー判定 → ディスパッチの純関数層をテストする。実 TTY は触らず、``input_func`` /
``output_func`` を注入して入出力を制御する。
"""

from __future__ import annotations

from pathlib import Path

from docsweep.config import Config
from docsweep.engine import run_scan
from docsweep.interactive import (
    KEY_DISCARD,
    KEY_DONE,
    KEY_LATER,
    KEY_SKIP,
    KEY_WATCHING,
    apply_decision,
    candidates_for_review,
    dispatch_decisions,
    parse_key,
    run_interactive_triage,
    summarize,
)


def _make_config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.roots = [tmp_path]
    return cfg


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# -------- parse_key --------


def test_parse_key_known_letters():
    assert parse_key("c") == "done"
    assert parse_key("w") == "watching"
    assert parse_key("x") == "discard"
    assert parse_key("s") == "skip"
    assert parse_key("l") == "later"
    assert parse_key("o") == "open"
    assert parse_key("q") == "quit"


def test_parse_key_case_insensitive_and_first_char():
    assert parse_key("C") == "done"
    assert parse_key("Watching") == "watching"  # 先頭 1 文字だけ見る
    assert parse_key("  q  ") == "quit"


def test_parse_key_unknown_returns_none():
    assert parse_key("") is None
    assert parse_key("z") is None
    assert parse_key("?") is None


# -------- candidates_for_review --------


def test_candidates_includes_pending_and_needs_decision(tmp_path: Path):
    _write(tmp_path, "plan_p.md", "# [計画] p\n")  # 新しすぎて NEEDS_DECISION 出ない
    _write(tmp_path, "pending_q.md", "# [保留] q\n")  # pending は常に対象
    _write(tmp_path, "plan_done.md", "# [完了] r\n")  # auto_movable なので対象
    cfg = _make_config(tmp_path)
    result = run_scan(cfg)
    cands = candidates_for_review(result)
    names = sorted(Path(d.record.path).name for d in cands)
    assert "pending_q.md" in names
    assert "plan_done.md" in names
    assert "plan_p.md" not in names


# -------- apply_decision (dry-run・副作用なし) --------


def test_apply_decision_skip_is_noop(tmp_path: Path):
    p = _write(tmp_path, "pending_x.md", "# [保留] x\n")
    cfg = _make_config(tmp_path)
    docs = candidates_for_review(run_scan(cfg))
    doc = next(d for d in docs if d.record.path == p.resolve().as_posix())
    res = apply_decision(doc, KEY_SKIP, cfg, dry_run=True)
    assert res.archived is False
    assert res.action is None
    assert res.error is None
    # ファイル本文は不変
    assert p.read_text(encoding="utf-8") == "# [保留] x\n"


def test_apply_decision_later_is_distinct_from_skip(tmp_path: Path):
    p = _write(tmp_path, "pending_x.md", "# [保留] x\n")
    cfg = _make_config(tmp_path)
    docs = candidates_for_review(run_scan(cfg))
    doc = next(d for d in docs if d.record.path == p.resolve().as_posix())
    res = apply_decision(doc, KEY_LATER, cfg, dry_run=True)
    assert res.decision == "later"
    assert res.archived is False


def test_apply_decision_done_dry_run_does_not_touch_file(tmp_path: Path):
    """dry_run=True なら副作用ゼロ。判定結果だけ返る。

    対象は ``[保留]`` の pending（``candidates_for_review`` に常時含まれる）。
    pending → done では promote ルートは通らず relabel + archive 経路に乗る。
    """
    body = "# [保留] x\n"
    p = _write(tmp_path, "pending_x.md", body)
    cfg = _make_config(tmp_path)
    docs = candidates_for_review(run_scan(cfg))
    doc = next(d for d in docs if d.record.path == p.resolve().as_posix())
    res = apply_decision(doc, KEY_DONE, cfg, dry_run=True)
    assert res.decision == "done"
    assert res.archived is True
    assert p.read_text(encoding="utf-8") == body  # 変化なし
    assert p.exists()


def test_apply_decision_done_writes_label_and_archives(tmp_path: Path):
    """dry_run=False のときは H1 ラベルが書き換わり archive へ移送される。

    対象は ``[保留]`` の pending（review 対象に常時入る）+ frontmatter status 行付き。
    """
    body = "---\ntype: pending\nstatus: pending\n---\n# [保留] x\n"
    p = _write(tmp_path, "pending_x.md", body)
    cfg = _make_config(tmp_path)
    docs = candidates_for_review(run_scan(cfg))
    doc = next(d for d in docs if d.record.path == p.resolve().as_posix())
    res = apply_decision(doc, KEY_DONE, cfg)
    assert res.archived is True
    # 原本はもう元の場所に無い（archive へ移送された）
    assert not p.exists()
    # archive 配下のどこかに完了ラベルで残っている
    archived = list(tmp_path.rglob("pending_x.md"))
    assert len(archived) == 1
    after = archived[0].read_text(encoding="utf-8")
    assert "[完了]" in after
    assert "status: done" in after  # frontmatter も同期更新


def test_apply_decision_watching_updates_label_and_frontmatter(tmp_path: Path):
    body = "---\ntype: pending\nstatus: pending\n---\n# [保留] x\n"
    p = _write(tmp_path, "pending_x.md", body)
    cfg = _make_config(tmp_path)
    docs = candidates_for_review(run_scan(cfg))
    doc = next(d for d in docs if d.record.path == p.resolve().as_posix())
    res = apply_decision(doc, KEY_WATCHING, cfg)
    assert res.archived is False
    after = p.read_text(encoding="utf-8")
    assert "[様子見]" in after
    assert "status: watching" in after


def test_apply_decision_discard_archives(tmp_path: Path):
    body = "# [保留] x\n"
    p = _write(tmp_path, "pending_x.md", body)
    cfg = _make_config(tmp_path)
    docs = candidates_for_review(run_scan(cfg))
    doc = next(d for d in docs if d.record.path == p.resolve().as_posix())
    res = apply_decision(doc, KEY_DISCARD, cfg)
    assert res.archived is True
    assert not p.exists()


# -------- dispatch_decisions（複数件一括）--------


def test_dispatch_decisions_returns_one_result_per_pair(tmp_path: Path):
    p1 = _write(tmp_path, "pending_a.md", "# [保留] a\n")
    p2 = _write(tmp_path, "pending_b.md", "# [保留] b\n")
    cfg = _make_config(tmp_path)
    docs = candidates_for_review(run_scan(cfg))
    by_name = {Path(d.record.path).name: d for d in docs}
    pairs = [(by_name["pending_a.md"], KEY_SKIP), (by_name["pending_b.md"], KEY_LATER)]
    results = dispatch_decisions(pairs, cfg, dry_run=True)
    assert len(results) == 2
    decisions = [r.decision for r in results]
    assert decisions == ["skip", "later"]
    # dry-run なので原本は不変
    assert p1.exists() and p2.exists()


# -------- summarize --------


def test_summarize_groups_by_decision():
    from docsweep.interactive import DecisionResult
    results = [
        DecisionResult(path="a", decision="done", action=None, archived=True),
        DecisionResult(path="b", decision="done", action=None, archived=True),
        DecisionResult(path="c", decision="skip", action=None, archived=False),
    ]
    line = summarize(results)
    assert "完了 2" in line
    assert "スキップ 1" in line


def test_summarize_empty():
    assert "対象なし" in summarize([])


# -------- run_interactive_triage（注入入出力）--------


def test_run_interactive_triage_with_injected_io(tmp_path: Path):
    """``input_func`` / ``output_func`` を注入してキー判定ループ全体を駆動する。"""
    _write(tmp_path, "pending_a.md", "# [保留] a\n")
    _write(tmp_path, "pending_b.md", "# [保留] b\n")
    cfg = _make_config(tmp_path)

    # キー入力列: 1 件目に s（スキップ）、2 件目に q（終了）
    keys = iter(["s", "q"])
    captured: list[str] = []
    rc = run_interactive_triage(
        cfg,
        input_func=lambda _prompt: next(keys),
        output_func=captured.append,
        dry_run=True,
    )
    assert rc == 0
    joined = "\n".join(captured)
    assert "インタラクティブ triage" in joined
    assert "判定結果" in joined  # summarize が呼ばれた
    # q で抜けたあとでも一括処理（skip 1 件）が走っている
    assert "スキップ 1" in joined


def test_run_interactive_triage_unknown_key_then_skip(tmp_path: Path):
    """未知キーは再プロンプト。続けて s が来たら正常に dispatch される。"""
    _write(tmp_path, "pending_a.md", "# [保留] a\n")
    cfg = _make_config(tmp_path)

    keys = iter(["?", "s"])
    captured: list[str] = []
    rc = run_interactive_triage(
        cfg,
        input_func=lambda _prompt: next(keys),
        output_func=captured.append,
        dry_run=True,
    )
    assert rc == 0
    joined = "\n".join(captured)
    assert "不明なキー" in joined
    assert "スキップ 1" in joined


def test_run_interactive_triage_with_no_candidates(tmp_path: Path):
    """対象 0 件のときは何も聞かずに終了する。"""
    cfg = _make_config(tmp_path)
    captured: list[str] = []
    rc = run_interactive_triage(
        cfg,
        input_func=lambda _prompt: "q",  # 呼ばれないはず
        output_func=captured.append,
        docs=[],  # 明示的に 0 件
        dry_run=True,
    )
    assert rc == 0
    assert any("判断が要るファイル" in line for line in captured)
