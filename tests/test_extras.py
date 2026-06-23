import time
from pathlib import Path

import pytest

from docsweep.config import load_config


def _write(p: Path, text: str, age_days: int = 0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if age_days:
        import os
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
    return p


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "dev"
    _write(root / "a" / "plan_done.md", "# [完了] done\n\n## 概要\n\nx\n")
    _write(root / "a" / "plan_watch.md", "# [様子見] w\n\n## 概要\n\ny\n")
    _write(root / "a" / "plan_stale.md", "# [計画] s\n\n## 概要\n\nz\n", age_days=200)
    _write(root / "b" / "pending_foo.md", "# [保留] foo\n\n## 概要\n\np\n")
    return root


def _cfg(root: Path):
    return load_config(explicit_roots=[str(root)], global_path=root / "no.yaml")


# ---- INDEX (C5) ----

def test_build_index_counts(ws):
    from docsweep.index import build_index

    idx = build_index(_cfg(ws))
    assert idx.counts["projects"] == 2
    assert idx.counts["pending"] == 1
    assert idx.counts["needs_decision"] == 1  # stale plan
    assert any(Path(d["path"]).name == "pending_foo.md" for d in idx.pending)


def test_write_index_files(ws):
    from docsweep.index import write_index

    cfg = _cfg(ws)
    json_path, md_path = write_index(cfg)
    assert json_path.is_file() and md_path.is_file()
    assert "docsweep INDEX" in md_path.read_text(encoding="utf-8")


def test_index_output_not_rescanned(ws):
    """生成した .docsweep/INDEX.md を次のスキャンが拾わない（自己再帰の防止）。"""
    from docsweep.engine import run_scan
    from docsweep.index import write_index

    cfg = _cfg(ws)
    write_index(cfg)
    names = {Path(r.path).name for r in run_scan(cfg).records}
    assert "INDEX.md" not in names


# ---- promote / reports (C3) ----

def test_promote_watching(ws):
    from docsweep.engine import promote_state

    cfg = _cfg(ws)
    moved = promote_state(cfg, from_state="watching", to_state="done")
    assert len(moved) == 1
    assert (ws / "a" / "archive" / "plan_watch.md").exists()


def test_summary_json(ws):
    import json

    from docsweep.reports import render_summary

    data = json.loads(render_summary(_cfg(ws)))
    assert "counts" in data and "pending" in data


# ---- new (C3) ----

def test_new_plan(tmp_path):
    from docsweep.templates_gen import new_doc

    (tmp_path / "docs" / "local").mkdir(parents=True)
    doc = new_doc("plan", "my-topic", project_dir=tmp_path)
    assert doc.path.name == "plan_my-topic.md"
    assert doc.path.parent.name == "local"
    assert "[計画]" in doc.path.read_text(encoding="utf-8")


def test_new_bugfix_dated(tmp_path):
    from docsweep.templates_gen import new_doc

    doc = new_doc("bugfix", "crash", project_dir=tmp_path)
    assert doc.path.name.startswith("bugfix_crash_")
    # 2026-06-23 改修: 新規 bugfix は [対応中] でなく [実行中] を書く（active 統合）
    assert "[実行中]" in doc.path.read_text(encoding="utf-8")


# ---- inject / eject (C7) ----

@pytest.fixture
def manifest(tmp_path, monkeypatch):
    mp = tmp_path / "injected.json"
    monkeypatch.setattr("docsweep.inject.MANIFEST_PATH", mp)
    # inject_global は GLOBAL_CONFIG_PATH のひな型生成も行うので、実 home を汚さないよう
    # tmp 側へ向けておく（既存テストの暗黙副作用を防ぐ）。
    monkeypatch.setattr("docsweep.inject.GLOBAL_CONFIG_PATH", tmp_path / "global_config.yaml")
    return mp


def test_inject_creates_block_and_yaml(tmp_path, manifest):
    from docsweep.inject import inject

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("# My Project\n\n手書きの内容。\n", encoding="utf-8")
    r = inject(proj, preset="claude-jp")
    text = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert "docsweep:managed:start" in text
    assert "手書きの内容。" in text  # ユーザー手書き温存
    assert (proj / ".docsweep.yaml").is_file()
    assert "CLAUDE.md" in r.written


def test_inject_idempotent(tmp_path, manifest):
    from docsweep.inject import inject

    proj = tmp_path / "proj"
    proj.mkdir()
    inject(proj, preset="claude-jp")
    r2 = inject(proj, preset="claude-jp")
    assert "CLAUDE.md" in r2.skipped  # 2 回目は不変


def test_eject_removes_block_keeps_handwritten(tmp_path, manifest):
    from docsweep.inject import eject, inject

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("# My Project\n\n手書き。\n", encoding="utf-8")
    inject(proj, preset="claude-jp")
    eject(proj)
    text = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert "docsweep:managed" not in text
    assert "手書き。" in text
    assert (proj / ".docsweep.yaml").is_file()  # 既定では残す


def test_eject_purge_removes_yaml(tmp_path, manifest):
    from docsweep.inject import eject, inject

    proj = tmp_path / "proj"
    proj.mkdir()
    inject(proj, preset="claude-jp")
    eject(proj, purge=True)
    assert not (proj / ".docsweep.yaml").exists()


def test_inject_handedit_detection(tmp_path, manifest):
    from docsweep.inject import inject

    proj = tmp_path / "proj"
    proj.mkdir()
    inject(proj, preset="claude-jp")
    # 管理ブロック内を手編集
    p = proj / "CLAUDE.md"
    text = p.read_text(encoding="utf-8")
    text = text.replace("内部状態", "改ざん")
    p.write_text(text, encoding="utf-8")
    r = inject(proj, preset="claude-jp")
    assert any("手編集" in w for w in r.warnings)
    assert (proj / "CLAUDE.md.bak").is_file()


def test_list_injected(tmp_path, manifest):
    from docsweep.inject import inject, list_injected
    from docsweep.presets import PRESETS

    proj = tmp_path / "proj"
    proj.mkdir()
    inject(proj, preset="frontmatter")
    items = list_injected()
    assert len(items) == 1
    assert items[0]["preset"] == "frontmatter"
    # プリセット定義の改訂版がマニフェスト経由で UI に届くこと（v0.1.0 初期は "1"）
    assert items[0]["version"] == PRESETS["frontmatter"].version


def test_list_injected_records_global_guidance_version(tmp_path, manifest, monkeypatch):
    """グローバル inject の場合は導線ブロックの版 (GUIDANCE_VERSION) が version に乗る。"""
    from docsweep import inject as I

    target = tmp_path / "CLAUDE.md"
    monkeypatch.setattr(I, "GUIDANCE_PATH", tmp_path / "guidance.md")
    I.inject_global(agent="claude", target=target)
    items = [it for it in I.list_injected() if it.get("scope") == "global"]
    assert items and items[0]["version"] == I.GUIDANCE_VERSION


# ---- pointer / @import モード（single source of truth） ----

def test_agents_md_gets_pointer_not_duplicate(tmp_path, manifest):
    """AGENTS.md は CLAUDE.md のフルブロックを複製せず、ポインタ＋注記だけを書く。"""
    from docsweep.inject import inject

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "AGENTS.md").write_text("# Codex entry\n", encoding="utf-8")
    inject(proj, preset="claude-jp")

    claude = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (proj / "AGENTS.md").read_text(encoding="utf-8")
    # CLAUDE.md は正本（ラベル表を持つ）
    assert "| 内部状態 |" in claude
    # AGENTS.md はポインタのみ（ラベル表を複製しない）＋ docsweep の注記＋マーカー
    assert "| 内部状態 |" not in agents
    assert "CLAUDE.md" in agents
    assert "docsweep inject が自動追加・管理" in agents
    assert "docsweep:managed:start" in agents


def test_inject_global_claude_uses_import(tmp_path, manifest, monkeypatch):
    """Claude グローバルは @import 1 行＋注記。実体は docsweep 所有の guidance.md。"""
    from docsweep import inject as I

    gpath = tmp_path / "home_docsweep" / "guidance.md"
    monkeypatch.setattr(I, "GUIDANCE_PATH", gpath)
    target = tmp_path / "fake_claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True)
    target.write_text("# 個人グローバル\n\n手書き。\n", encoding="utf-8")

    I.inject_global(agent="claude", target=target)
    text = target.read_text(encoding="utf-8")
    assert "手書き。" in text  # 個人ファイルは温存
    assert f"@{I.GUIDANCE_IMPORT}" in text  # @import 1 行
    assert "docsweep inject が自動追加・管理" in text  # 注記
    assert "docsweep:managed:start" in text
    assert gpath.is_file()  # 中央ファイル生成
    assert "残作業" in gpath.read_text(encoding="utf-8")


def test_inject_global_codex_inlines_guidance(tmp_path, manifest, monkeypatch):
    """Codex は @import 非対応 → 導線本文をその場に展開（注記付き）。"""
    from docsweep import inject as I

    gpath = tmp_path / "g.md"
    monkeypatch.setattr(I, "GUIDANCE_PATH", gpath)
    target = tmp_path / "codex" / "AGENTS.md"
    target.parent.mkdir(parents=True)

    I.inject_global(agent="codex", target=target)
    text = target.read_text(encoding="utf-8")
    assert "@" + I.GUIDANCE_IMPORT not in text  # import 行ではない
    assert "-m docsweep triage" in text  # 本文がインライン（PATH 非依存）
    assert "docsweep inject が自動追加・管理" in text
    # Codex はインライン展開で中央ファイルを参照しない → 孤児 guidance.md を作らない。
    assert not gpath.exists()


def test_preview_global_central_only_for_claude(tmp_path, monkeypatch):
    """preview_global は中央ファイルの行を @import 参照する claude でだけ返す（Codex は出さない）。"""
    from docsweep import inject as I

    monkeypatch.setattr(I, "GUIDANCE_PATH", tmp_path / "g.md")
    claude = I.preview_global(agent="claude", target=tmp_path / "c" / "CLAUDE.md")
    assert claude["guidance"] and claude["guidance_path"]
    codex = I.preview_global(agent="codex", target=tmp_path / "x" / "AGENTS.md")
    assert codex["guidance"] is None and codex["guidance_path"] is None


def test_inject_no_guidance_label_only(tmp_path, manifest):
    """include_guidance=False はラベル節だけ書き、導線（triage 行）を含めない（CLI --no-guidance / MCP 相当）。"""
    from docsweep.inject import inject

    proj = tmp_path / "proj"
    proj.mkdir()
    inject(proj, preset="claude-jp", include_guidance=False)
    text = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert "| 内部状態 |" in text  # ラベル節はある
    assert "セッション開始時" not in text  # 導線は含まれない


def test_eject_global_removes_block_and_central(tmp_path, manifest, monkeypatch):
    """最後の global 参照を eject したら中央 guidance.md も撤去する。"""
    from docsweep import inject as I

    gpath = tmp_path / "g.md"
    monkeypatch.setattr(I, "GUIDANCE_PATH", gpath)
    target = tmp_path / "claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True)
    target.write_text("# 個人\n\n手書き。\n", encoding="utf-8")

    I.inject_global(agent="claude", target=target)
    assert gpath.is_file()
    I.eject_global(agent="claude", target=target)

    text = target.read_text(encoding="utf-8")
    assert "docsweep:managed" not in text  # フック除去
    assert "手書き。" in text  # 手書き温存
    assert not gpath.exists()  # 中央ファイルも撤去


def test_resolve_global_target_respects_codex_home(tmp_path, monkeypatch):
    from docsweep import inject as I

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    assert I.resolve_global_target("codex") == (tmp_path / "ch" / "AGENTS.md").resolve()
    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert I.resolve_global_target("codex").name == "AGENTS.md"  # 既定 ~/.codex


def test_preview_global_warns_on_override(tmp_path):
    from docsweep import inject as I

    d = tmp_path / "codexhome"
    d.mkdir()
    (d / "AGENTS.override.md").write_text("x", encoding="utf-8")
    # AGENTS.md も、フォールバック名（TEAM_GUIDE.md）も override に隠される → 警告。
    assert any("AGENTS.override.md" in w for w in I.preview_global(agent="codex", target=d / "AGENTS.md")["warnings"])
    assert any("AGENTS.override.md" in w for w in I.preview_global(agent="codex", target=d / "TEAM_GUIDE.md")["warnings"])
    # Claude は override の概念が無いので警告しない。
    assert not I.preview_global(agent="claude", target=d / "CLAUDE.md")["warnings"]


def test_inject_yaml_includes_due_scaffold(tmp_path, manifest):
    """プロジェクト inject 生成の .docsweep.yaml に due ひな型（コメント）が含まれる。

    既定値そのままを例示するので、コメントを外しただけでは挙動が変わらない（嘘の上書きが起きない）。
    """
    from docsweep.inject import inject

    proj = tmp_path / "proj"
    proj.mkdir()
    inject(proj, preset="claude-jp")
    body = (proj / ".docsweep.yaml").read_text(encoding="utf-8")
    assert "due:" in body  # ひな型行（コメントアウト中）が存在
    assert "default_offset_days" in body
    assert "plan: 7" in body
    assert "pending: 14" in body


def test_inject_global_creates_docsweep_config_scaffold(tmp_path, manifest, monkeypatch):
    """グローバル inject で ~/.docsweep/config.yaml が無ければ due ひな型付きで作る。"""
    from docsweep import inject as I

    gpath = tmp_path / "g.md"
    monkeypatch.setattr(I, "GUIDANCE_PATH", gpath)
    target = tmp_path / "claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True)

    assert not I.GLOBAL_CONFIG_PATH.exists()  # 前提: ひな型は未生成
    r = I.inject_global(agent="claude", target=target)
    assert I.GLOBAL_CONFIG_PATH.is_file()
    body = I.GLOBAL_CONFIG_PATH.read_text(encoding="utf-8")
    assert "due:" in body
    assert "default_offset_days" in body
    # 作成時は warning として通知（UI で見える）
    assert any("config.yaml" in w for w in r.warnings)


def test_inject_global_keeps_existing_docsweep_config(tmp_path, manifest, monkeypatch):
    """既存の ~/.docsweep/config.yaml には触らない（ユーザー設定保護）。"""
    from docsweep import inject as I

    gpath = tmp_path / "g.md"
    monkeypatch.setattr(I, "GUIDANCE_PATH", gpath)
    target = tmp_path / "claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True)
    # ユーザーが既に config を持っている状態を作る
    I.GLOBAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    I.GLOBAL_CONFIG_PATH.write_text("roots:\n  - C:/dev\n", encoding="utf-8")

    r = I.inject_global(agent="claude", target=target)
    body = I.GLOBAL_CONFIG_PATH.read_text(encoding="utf-8")
    assert body == "roots:\n  - C:/dev\n"  # 完全に温存
    assert not any("config.yaml" in w for w in r.warnings)  # 通知も出さない


def test_inject_label_block_mentions_due(tmp_path, manifest):
    """生成された CLAUDE.md 管理ブロックに due ルールの説明節が入る。"""
    from docsweep.inject import inject

    proj = tmp_path / "proj"
    proj.mkdir()
    inject(proj, preset="claude-jp")
    text = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert "対応期日" in text
    assert "default_offset_days" in text
    # bugfix は新規時 due を付けない仕様も明記
    assert "[様子見]" in text and "bugfix" in text


def test_eject_global_keeps_central_while_claude_present(tmp_path, manifest, monkeypatch):
    """Codex のみ eject しても、@import 参照する Claude が残る限り guidance.md は保持する。"""
    from docsweep import inject as I

    gpath = tmp_path / "g.md"
    monkeypatch.setattr(I, "GUIDANCE_PATH", gpath)
    ct = tmp_path / "claude" / "CLAUDE.md"
    ct.parent.mkdir(parents=True)
    at = tmp_path / "codex" / "AGENTS.md"
    at.parent.mkdir(parents=True)

    I.inject_global(agent="claude", target=ct)
    I.inject_global(agent="codex", target=at)
    assert gpath.is_file()

    I.eject_global(agent="codex", target=at)
    assert gpath.is_file()  # Claude がまだ @import 参照しているので保持

    I.eject_global(agent="claude", target=ct)
    assert not gpath.exists()  # Claude も消えたので撤去


def test_build_triage_shape(ws):
    from docsweep.reports import build_triage

    t = build_triage(_cfg(ws))
    assert {"counts", "items", "needs_fix"} <= set(t)
    ages = [it["age_days"] for it in t["items"]]
    assert ages == sorted(ages, reverse=True)  # 古い順
    assert t["items"], "ws には stale plan と pending があるので items は非空"
    it = t["items"][0]
    assert {"project", "rel", "title", "state", "type", "age_days", "actions", "path"} <= set(it)


# ---- due 第2軸（C2/C3） ----

def test_due_parsed_and_stored(tmp_path):
    """frontmatter due: が FileRecord.due に格納される。"""
    from docsweep.config import load_config
    from docsweep.engine import run_scan

    p = tmp_path / "a" / "plan_todo.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "---\ndue: 2020-01-01\n---\n# [計画] todo\n\n## 概要\n\ntest\n",
        encoding="utf-8",
    )
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = run_scan(cfg)
    rec = next(r for r in result.records if r.path.endswith("plan_todo.md"))
    assert rec.due == "2020-01-01"
    assert not rec.due_parse_error


def test_due_overdue_todo_flag(tmp_path):
    """past due + planned state → overdue_todo フラグが立つ。"""
    from docsweep.config import load_config
    from docsweep.engine import run_scan
    from docsweep.models import Flag

    p = tmp_path / "a" / "plan_old.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "---\ndue: 2020-01-01\n---\n# [計画] old\n\n## 概要\n\ntest\n",
        encoding="utf-8",
    )
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = run_scan(cfg)
    rec = next(r for r in result.records if r.path.endswith("plan_old.md"))
    assert Flag.OVERDUE_TODO.value in rec.flags
    assert Flag.OVERDUE_GRADUATE.value not in rec.flags


def test_due_overdue_graduate_flag(tmp_path):
    """past due + watching state → overdue_graduate フラグが立つ。"""
    from docsweep.config import load_config
    from docsweep.engine import run_scan
    from docsweep.models import Flag

    p = tmp_path / "a" / "plan_watch.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "---\ndue: 2020-01-01\n---\n# [様子見] watch\n\n## 概要\n\ntest\n",
        encoding="utf-8",
    )
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = run_scan(cfg)
    rec = next(r for r in result.records if r.path.endswith("plan_watch.md"))
    assert Flag.OVERDUE_GRADUATE.value in rec.flags
    assert Flag.OVERDUE_TODO.value not in rec.flags


def test_due_no_flag_when_done(tmp_path):
    """done 状態では due 超過フラグを立てない（archive 制御と切り離す）。"""
    from docsweep.config import load_config
    from docsweep.engine import run_scan
    from docsweep.models import Flag

    p = tmp_path / "a" / "plan_done.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "---\ndue: 2020-01-01\n---\n# [完了] done\n\n## 概要\n\ntest\n",
        encoding="utf-8",
    )
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = run_scan(cfg)
    rec = next(r for r in result.records if r.path.endswith("plan_done.md"))
    assert Flag.OVERDUE_TODO.value not in rec.flags
    assert Flag.OVERDUE_GRADUATE.value not in rec.flags
    # archivable は due に影響されない（C4 不変条件）
    assert rec.archivable


def test_due_parse_error_flag(tmp_path):
    """不正な due 値 → due_parse_error フラグが立つ。"""
    from docsweep.config import load_config
    from docsweep.engine import run_scan
    from docsweep.models import Flag

    p = tmp_path / "a" / "plan_bad.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "---\ndue: not-a-date\n---\n# [計画] bad\n\n## 概要\n\ntest\n",
        encoding="utf-8",
    )
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = run_scan(cfg)
    rec = next(r for r in result.records if r.path.endswith("plan_bad.md"))
    assert rec.due_parse_error
    assert Flag.DUE_PARSE_ERROR.value in rec.flags


def test_due_in_slim_record(tmp_path):
    """slim_record に due フィールドが含まれる。"""
    from docsweep.config import load_config
    from docsweep.engine import run_scan
    from docsweep.reports import slim_record

    p = tmp_path / "a" / "plan_slim.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "---\ndue: 2020-06-01\n---\n# [計画] slim\n\n## 概要\n\ntest\n",
        encoding="utf-8",
    )
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = run_scan(cfg)
    rec = next(r for r in result.records if r.path.endswith("plan_slim.md"))
    s = slim_record(rec.to_dict())
    assert s["due"] == "2020-06-01"


def test_overdue_counts_in_index(tmp_path):
    """overdue_todo / overdue_graduate が build_index counts に反映される。"""
    from docsweep.config import load_config
    from docsweep.index import build_index

    (tmp_path / "a").mkdir(parents=True)
    (tmp_path / "a" / "plan_todo.md").write_text(
        "---\ndue: 2020-01-01\n---\n# [計画] todo\n\n## 概要\n\ntest\n", encoding="utf-8"
    )
    (tmp_path / "a" / "plan_watch.md").write_text(
        "---\ndue: 2020-01-01\n---\n# [様子見] watch\n\n## 概要\n\ntest\n", encoding="utf-8"
    )
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    idx = build_index(cfg)
    assert idx.counts["overdue_todo"] == 1
    assert idx.counts["overdue_graduate"] == 1
    assert len(idx.overdue_todo) == 1
    assert len(idx.overdue_graduate) == 1


def test_mcp_build_server_smoke(tmp_path):
    """mcp extra があれば、build_triage/inject_global を参照する MCP サーバが構築できる（import 健全性）。"""
    import pytest

    pytest.importorskip("mcp")
    from docsweep.config import load_config
    from docsweep.mcp_server import build_server

    assert build_server(load_config(global_path=tmp_path / "no.yaml")) is not None
