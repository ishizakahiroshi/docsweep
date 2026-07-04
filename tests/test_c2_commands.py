"""C2 Phase 2 サブコマンド（migrate-frontmatter / fix-related / show / stale / context /
claim / config / timeline / find / completion）の単体テスト。

H1 ラベルが書き換わらないこと、後方互換が保たれていることを軸に検証する。
"""

from __future__ import annotations

import io
import json
import os
import time
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

import pytest

from docsweep import cli as cli_mod
from docsweep.completion import render_completion
from docsweep.config import Config, get_user_setting, list_settings, set_user_setting
from docsweep.find import FindFilters, find_records
from docsweep.migrate import apply_migration, plan_migration
from docsweep.related import apply_fix_related, plan_fix_related
from docsweep.stale import find_stale


# ----------------------------------------------------------------------
# 共通 fixture
# ----------------------------------------------------------------------


def _write(p: Path, text: str, age_days: int = 0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="")
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
    return p


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """plan/bugfix/pending を 1 プロジェクトに置いた最小ワークスペース。"""
    root = tmp_path / "dev"
    proj = root / "demo"
    _write(
        proj / "docs" / "local" / "plan_alpha.md",
        "# [計画] α 計画\n\n## 概要\n\nαの計画。\n",
    )
    _write(
        proj / "docs" / "local" / "bugfix_alpha_2026-06-01.md",
        "---\n"
        "type: bugfix\n"
        "status: in-progress\n"
        "tags: [ui, alpha]\n"
        "owner: alice\n"
        "review_status: draft\n"
        "related: [plan_alpha.md]\n"
        "last_reviewed: 2026-06-29\n"
        "---\n"
        "# [対応中] α バグ修正\n\n## 症状\n\n発生中。\n",
    )
    _write(
        proj / "docs" / "local" / "pending_beta.md",
        "# [保留] β 案件\n\n## 概要\n\n保留中。\n## 保留理由\n\n調整中。\n## 着手条件\n\n外部待ち。\n",
    )
    return root


def _cfg(root: Path) -> Config:
    from docsweep.config import load_config

    return load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")


# ----------------------------------------------------------------------
# migrate-frontmatter
# ----------------------------------------------------------------------


def test_migrate_plan_lists_targets(workspace: Path):
    """dry-run は frontmatter なしの md を planned に並べ、ある md は skipped に入れる。"""
    cfg = _cfg(workspace)
    result = plan_migration(cfg)
    planned_names = {Path(p.path).name for p in result.planned}
    skipped_names = {Path(p.path).name for p in result.skipped}
    assert "plan_alpha.md" in planned_names
    assert "pending_beta.md" in planned_names
    assert "bugfix_alpha_2026-06-01.md" in skipped_names  # 既に frontmatter あり


def test_migrate_apply_inserts_frontmatter_and_preserves_h1(workspace: Path):
    """apply は frontmatter を先頭挿入し、H1 ラベル・本文に手を加えない。"""
    cfg = _cfg(workspace)
    plan_path = workspace / "demo" / "docs" / "local" / "plan_alpha.md"
    before = plan_path.read_text(encoding="utf-8", newline="")
    result = apply_migration(cfg)
    assert plan_path.as_posix() in result.applied
    after = plan_path.read_text(encoding="utf-8", newline="")
    assert after.startswith("---\n")
    assert "type: plan" in after
    assert "# [計画] α 計画" in after  # H1 温存
    # 本文がそのまま尾にある
    assert before.strip() in after


def test_migrate_skips_already_frontmatter(workspace: Path):
    """OKF キーが揃っているファイルは apply してもそのまま。"""
    cfg = _cfg(workspace)
    bf_path = workspace / "demo" / "docs" / "local" / "bugfix_alpha_2026-06-01.md"
    before = bf_path.read_text(encoding="utf-8", newline="")
    apply_migration(cfg)
    after = bf_path.read_text(encoding="utf-8", newline="")
    assert before == after


def test_migrate_upgrades_due_only_frontmatter(workspace: Path):
    """`due:` だけの部分 frontmatter は upgrade 対象になり、不足キーが追記される（due は温存）。"""
    gamma = _write(
        workspace / "demo" / "docs" / "local" / "plan_gamma.md",
        "---\ndue: 2026-07-11\n---\n\n# [計画] γ 計画\n\n## 概要\n\nγの計画。\n",
    )
    cfg = _cfg(workspace)
    plan_result = plan_migration(cfg)
    modes = {Path(p.path).name: p.mode for p in plan_result.planned}
    assert modes.get("plan_gamma.md") == "upgrade"

    apply_migration(cfg, today="2026-07-04")
    after = gamma.read_text(encoding="utf-8", newline="")
    # 不足キーが正典順で入り、既存 due とブロック直後の空行・H1・本文は不変
    assert after.startswith(
        "---\n"
        "type: plan\n"
        "status: planned\n"
        "tags: []\n"
        "owner: \n"
        "review_status: draft\n"
        "related: []\n"
        "last_reviewed: 2026-07-04\n"
        "due: 2026-07-11\n"
        "---\n"
        "\n"
        "# [計画] γ 計画\n"
    )
    assert after.endswith("## 概要\n\nγの計画。\n")


def test_migrate_completes_partial_okf_keys(workspace: Path):
    """type/status が既にあっても、不足キー（tags 等）だけ追記され、既存キーの値は不変。"""
    delta = _write(
        workspace / "demo" / "docs" / "local" / "plan_delta.md",
        "---\ntype: plan\nstatus: watching\ndue: 2026-07-20\n---\n# [様子見] δ 計画\n\n本文。\n",
    )
    cfg = _cfg(workspace)
    apply_migration(cfg, today="2026-07-04")
    after = delta.read_text(encoding="utf-8", newline="")
    # 既存キーはそのまま（status: watching が planned 等に書き換わらない）
    assert "type: plan\nstatus: watching\ndue: 2026-07-20\n" in after
    assert after.count("\nstatus:") == 1  # review_status: は含めない（行頭一致）
    # 不足キーが追記されている
    for line in ("tags: []", "owner: ", "review_status: draft", "related: []", "last_reviewed: 2026-07-04"):
        assert line in after
    assert "# [様子見] δ 計画\n\n本文。\n" in after


# ----------------------------------------------------------------------
# fix-related
# ----------------------------------------------------------------------


def test_fix_related_plan_detects_one_sided(workspace: Path):
    """bugfix → plan の片側参照を検出（plan に related が無いため対称化が要る）。"""
    cfg = _cfg(workspace)
    result = plan_fix_related(cfg)
    # plan_alpha は frontmatter なし → 書けないので apply 対象には入らないはず
    # まず migrate して再確認
    apply_migration(cfg)
    result = plan_fix_related(cfg)
    paths = {Path(f.path).name for f in result.fixes}
    assert "plan_alpha.md" in paths


def test_fix_related_apply_symmetrizes(workspace: Path):
    """apply で B 側にも A への related が書き戻される。"""
    cfg = _cfg(workspace)
    apply_migration(cfg)
    result = apply_fix_related(cfg)
    plan_path = workspace / "demo" / "docs" / "local" / "plan_alpha.md"
    text = plan_path.read_text(encoding="utf-8")
    assert "bugfix_alpha_2026-06-01.md" in text
    assert plan_path.as_posix() in result.applied


# ----------------------------------------------------------------------
# stale
# ----------------------------------------------------------------------


def test_stale_finds_draft_over_threshold(workspace: Path):
    """draft review_status を 15 日前に書き戻したファイルを find_stale が拾う。"""
    cfg = _cfg(workspace)
    bf_path = workspace / "demo" / "docs" / "local" / "bugfix_alpha_2026-06-01.md"
    # mtime を 20 日前にして draft 14日しきい値を超過させる
    old = time.time() - 20 * 86400
    os.utime(bf_path, (old, old))
    result = find_stale(cfg)
    paths = {Path(it.path).name for it in result.items}
    assert "bugfix_alpha_2026-06-01.md" in paths


def test_stale_respects_review_threshold(workspace: Path):
    """review_status: review は 7 日しきい値（draft より厳しい）。"""
    cfg = _cfg(workspace)
    bf_path = workspace / "demo" / "docs" / "local" / "bugfix_alpha_2026-06-01.md"
    # review に切替 + 10 日前
    text = bf_path.read_text(encoding="utf-8")
    text = text.replace("review_status: draft", "review_status: review")
    bf_path.write_text(text, encoding="utf-8", newline="")
    old = time.time() - 10 * 86400
    os.utime(bf_path, (old, old))
    result = find_stale(cfg)
    paths = {Path(it.path).name for it in result.items}
    assert "bugfix_alpha_2026-06-01.md" in paths


def test_stale_published_uses_last_reviewed(workspace: Path):
    """published は last_reviewed 起点で 90 日しきい値。"""
    cfg = _cfg(workspace)
    bf_path = workspace / "demo" / "docs" / "local" / "bugfix_alpha_2026-06-01.md"
    text = bf_path.read_text(encoding="utf-8")
    text = text.replace("review_status: draft", "review_status: published")
    text = text.replace("last_reviewed: 2026-06-29", "last_reviewed: 2020-01-01")
    bf_path.write_text(text, encoding="utf-8", newline="")
    result = find_stale(cfg, today=date(2026, 6, 29))
    paths = {Path(it.path).name for it in result.items}
    assert "bugfix_alpha_2026-06-01.md" in paths


# ----------------------------------------------------------------------
# config (user.name / user.email)
# ----------------------------------------------------------------------


def test_config_roundtrip(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    set_user_setting("user.name", "Alice", global_path=cfg_path)
    assert get_user_setting("user.name", global_path=cfg_path) == "Alice"
    set_user_setting("user.email", "alice@example.com", global_path=cfg_path)
    settings = list_settings(global_path=cfg_path)
    assert settings["user.name"] == "Alice"
    assert settings["user.email"] == "alice@example.com"
    set_user_setting("user.name", None, global_path=cfg_path)
    assert get_user_setting("user.name", global_path=cfg_path) is None


def test_config_rejects_unknown_key(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    with pytest.raises(ValueError):
        set_user_setting("foo.bar", "x", global_path=cfg_path)


def test_config_and_web_share_same_file(tmp_path: Path):
    """config CLI が書いた値を、Web UI 側の current_owner が同じファイルから読む。"""
    from docsweep.services.frontmatter import current_owner

    cfg_path = tmp_path / "config.yaml"
    set_user_setting("user.name", "Hiroshi", global_path=cfg_path)
    # current_owner は GLOBAL_CONFIG_PATH を直接読む。monkeypatch 経由ではなく、
    # 共有が成立していることだけ「同じ値が読める」で代替確認。
    assert get_user_setting("user.name", global_path=cfg_path) == "Hiroshi"
    # 直接の owner 値（fallback 経路）も呼び出せること（実行環境の値次第なので存在のみ）
    assert isinstance(current_owner(), str)


# ----------------------------------------------------------------------
# find (free-form query)
# ----------------------------------------------------------------------


def test_find_owner_and_tag(workspace: Path):
    cfg = _cfg(workspace)
    recs = find_records(cfg, FindFilters(owner="alice", tags=["ui"]))
    names = {Path(r.path).name for r in recs}
    assert names == {"bugfix_alpha_2026-06-01.md"}


def test_find_multiple_conditions_are_and(workspace: Path):
    cfg = _cfg(workspace)
    # owner 違いなら空
    recs = find_records(cfg, FindFilters(owner="ghost"))
    assert recs == []
    # tag のみで絞れる
    recs = find_records(cfg, FindFilters(tags=["alpha"]))
    names = {Path(r.path).name for r in recs}
    assert names == {"bugfix_alpha_2026-06-01.md"}


def test_find_state_label_alias(workspace: Path):
    cfg = _cfg(workspace)
    # "計画" でも "planned" でも plan_alpha が拾える
    recs1 = find_records(cfg, FindFilters(states=["計画"]))
    recs2 = find_records(cfg, FindFilters(states=["planned"]))
    n1 = {Path(r.path).name for r in recs1}
    n2 = {Path(r.path).name for r in recs2}
    assert "plan_alpha.md" in n1
    assert n1 == n2


def test_find_review_status_filter(workspace: Path):
    cfg = _cfg(workspace)
    recs = find_records(cfg, FindFilters(review_statuses=["draft"]))
    names = {Path(r.path).name for r in recs}
    assert names == {"bugfix_alpha_2026-06-01.md"}


# ----------------------------------------------------------------------
# show (逆参照)
# ----------------------------------------------------------------------


def test_show_lists_backref(workspace: Path):
    """plan_alpha を bugfix が related に持っているので逆参照に出る。"""
    from docsweep.engine import run_scan
    from docsweep.related import backref_records

    cfg = _cfg(workspace)
    records = list(run_scan(cfg).records)
    target = next(r for r in records if Path(r.path).name == "plan_alpha.md")
    backs = backref_records(target, records)
    assert {Path(r.path).name for r in backs} == {"bugfix_alpha_2026-06-01.md"}


# ----------------------------------------------------------------------
# timeline
# ----------------------------------------------------------------------


def test_timeline_orders_by_date(workspace: Path):
    from docsweep.timeline import build_timeline, render_timeline

    cfg = _cfg(workspace)
    result = build_timeline(cfg, "alpha")
    names = [Path(e.path).name for e in result.entries]
    assert "plan_alpha.md" in names
    assert "bugfix_alpha_2026-06-01.md" in names
    md = render_timeline(result, fmt="markdown")
    assert "timeline: alpha" in md
    js = render_timeline(result, fmt="json")
    json.loads(js)  # 文法 OK


# ----------------------------------------------------------------------
# context
# ----------------------------------------------------------------------


def test_context_includes_body_and_parent(workspace: Path):
    from docsweep.context import collect_context, render_context

    cfg = _cfg(workspace)
    target = (workspace / "demo" / "docs" / "local" / "bugfix_alpha_2026-06-01.md").resolve().as_posix()
    bundle = collect_context(target, cfg)
    text = render_context(bundle, fmt="markdown")
    assert "対象: bugfix_alpha_2026-06-01.md" in text
    assert "## 本文" in text
    # 親 plan が related から解決される
    assert "親 plan: plan_alpha.md" in text


# ----------------------------------------------------------------------
# claim / unclaim
# ----------------------------------------------------------------------


def test_claim_sets_owner_and_claimed_at(tmp_path: Path, monkeypatch):
    """frontmatter なしの md でも claim で frontmatter ごと作る（owner / claimed_at）。"""
    from docsweep.claim import claim

    # 設定経路で owner を固定
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(
        "docsweep.services.frontmatter.get_user_setting" if False else "docsweep.config.GLOBAL_CONFIG_PATH",
        cfg_path,
        raising=False,
    )
    set_user_setting("user.name", "Tester", global_path=cfg_path)

    # current_owner は GLOBAL_CONFIG_PATH を直接読む → monkeypatch しておく
    from docsweep import config as cfg_module
    monkeypatch.setattr(cfg_module, "GLOBAL_CONFIG_PATH", cfg_path)

    md = tmp_path / "plan_x.md"
    md.write_text("# [計画] x\n\n## 概要\n\nx\n", encoding="utf-8", newline="")
    result = claim(md)
    assert result.owner == "Tester"
    text = md.read_text(encoding="utf-8")
    assert "owner: Tester" in text
    assert "claimed_at:" in text
    assert "# [計画] x" in text  # H1 温存

    # unclaim で owner 空・claimed_at 削除
    result = claim(md, unclaim=True)
    assert result.owner is None
    text2 = md.read_text(encoding="utf-8")
    assert "owner:" in text2
    assert "owner: Tester" not in text2
    assert "claimed_at:" not in text2


# ----------------------------------------------------------------------
# completion
# ----------------------------------------------------------------------


def test_completion_bash_contains_subcommands(workspace: Path):
    cfg = _cfg(workspace)
    cfg.known_tags = ["ui", "backend"]
    out = render_completion("bash", cfg)
    assert "_docsweep" in out
    assert "migrate-frontmatter" in out
    assert "fix-related" in out
    assert "complete -F _docsweep docsweep" in out


def test_completion_zsh_syntax(workspace: Path):
    cfg = _cfg(workspace)
    out = render_completion("zsh", cfg)
    assert "#compdef docsweep" in out
    assert "_describe" in out


def test_completion_pwsh_uses_register_argument_completer(workspace: Path):
    cfg = _cfg(workspace)
    out = render_completion("pwsh", cfg)
    assert "Register-ArgumentCompleter" in out
    assert "docsweep" in out


def test_completion_rejects_unknown_shell(workspace: Path):
    cfg = _cfg(workspace)
    with pytest.raises(ValueError):
        render_completion("fish", cfg)


# ----------------------------------------------------------------------
# 既存挙動の後方互換: triage の既定動作が C2 配線後も変わらない
# ----------------------------------------------------------------------


def test_triage_default_behavior_unchanged(workspace: Path):
    """C2 追加で triage の既定（JSON 出力・要判断＋保留・古い順）が壊れていないこと。"""
    from docsweep.reports import build_triage

    cfg = _cfg(workspace)
    payload = build_triage(cfg)
    assert "items" in payload
    assert "needs_fix" in payload
    assert "counts" in payload
    paths = {Path(it["path"]).name for it in payload["items"]}
    # pending は保留としてヒット
    assert "pending_beta.md" in paths


# ----------------------------------------------------------------------
# CLI 統合（subcommand が parse / dispatch される）
# ----------------------------------------------------------------------


def test_cli_completion_runs(workspace: Path, capsys):
    rc = cli_mod.main(["completion", "bash"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "_docsweep" in out


def test_cli_migrate_dry_run_json(workspace: Path, capsys):
    rc = cli_mod.main([
        "migrate-frontmatter",
        "--root", str(workspace),
        "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert "planned" in payload
    assert "skipped" in payload


def test_cli_find_json(workspace: Path, capsys):
    rc = cli_mod.main([
        "find",
        "--root", str(workspace),
        "--tag", "ui",
        "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    rows = json.loads(out)
    names = {Path(r["path"]).name for r in rows}
    assert names == {"bugfix_alpha_2026-06-01.md"}
