"""2026-07-16 監査（ai-audit-prompts / ultracode）で確定した finding の再現テスト。

対応 plan / report:
- docs/local/plan_bug-security-quality-audit_2026-07-16.md
- docs/local/report_bug-security-quality-audit_2026-07-16.md

このテストは修正後の挙動を回帰から守るためのもの。修正前は各テストが失敗する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.capture import save_drafts
from docsweep.capture.models import Draft
from docsweep.capture.service import CaptureScopeError
from docsweep.config import load_config


# ---------- S-01 / V-01 / C-01: capture 任意ファイル書き込み ----------


def _cfg(tmp_path: Path):
    return load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")


def test_save_drafts_rejects_target_outside_roots(tmp_path: Path):
    """target_dir が roots 配下でなければ CaptureScopeError を投げる。"""
    cfg = _cfg(tmp_path)
    outside = tmp_path.parent / "definitely_outside"
    drafts = [Draft(id="d1", kind="plan", title="t", body="b", suggested_filename="plan_ok.md")]
    with pytest.raises(CaptureScopeError):
        save_drafts(drafts, config=cfg, target_dir=outside)


def test_save_drafts_rejects_path_separator_in_filename(tmp_path: Path):
    cfg = _cfg(tmp_path)
    target = tmp_path / "out"
    drafts = [
        Draft(id="d1", kind="plan", title="t", body="b", suggested_filename="../evil.md"),
    ]
    with pytest.raises(CaptureScopeError):
        save_drafts(drafts, config=cfg, target_dir=target)
    assert not (tmp_path.parent / "evil.md").exists()


def test_save_drafts_rejects_backslash_in_filename(tmp_path: Path):
    cfg = _cfg(tmp_path)
    target = tmp_path / "out"
    drafts = [Draft(id="d1", kind="plan", title="t", body="b",
                    suggested_filename="sub\\evil.md")]
    with pytest.raises(CaptureScopeError):
        save_drafts(drafts, config=cfg, target_dir=target)


def test_save_drafts_rejects_non_md_extension(tmp_path: Path):
    cfg = _cfg(tmp_path)
    target = tmp_path / "out"
    drafts = [Draft(id="d1", kind="plan", title="t", body="b",
                    suggested_filename="pwn.lnk")]
    with pytest.raises(CaptureScopeError):
        save_drafts(drafts, config=cfg, target_dir=target)


def test_save_drafts_rejects_empty_filename(tmp_path: Path):
    cfg = _cfg(tmp_path)
    target = tmp_path / "out"
    drafts = [Draft(id="d1", kind="plan", title="t", body="b", suggested_filename="")]
    with pytest.raises(CaptureScopeError):
        save_drafts(drafts, config=cfg, target_dir=target)


def test_save_drafts_accepts_normal_filename_under_roots(tmp_path: Path):
    """回帰: 通常ケース（safe な basename、roots 配下 target）は成功する。"""
    cfg = _cfg(tmp_path)
    target = tmp_path / "out"
    drafts = [Draft(id="d1", kind="plan", title="t", body="body-1",
                    suggested_filename="plan_new.md")]
    saved = save_drafts(drafts, config=cfg, target_dir=target)
    assert len(saved) == 1
    assert saved[0] == target / "plan_new.md"
    assert saved[0].read_text(encoding="utf-8") == "body-1"


def test_save_drafts_partial_write_prevented_on_bad_filename(tmp_path: Path):
    """複数 draft のうち 1 つでも不正な filename があれば書き込みを 1 件も行わない。"""
    cfg = _cfg(tmp_path)
    target = tmp_path / "out"
    drafts = [
        Draft(id="d1", kind="plan", title="t1", body="ok",
              suggested_filename="plan_ok.md"),
        Draft(id="d2", kind="plan", title="t2", body="bad",
              suggested_filename="../pwn.md"),
    ]
    with pytest.raises(CaptureScopeError):
        save_drafts(drafts, config=cfg, target_dir=target)
    # 1 件目の書き込みも起きていない（先に全 filename を検証してから書き込むため）。
    assert not target.exists() or not (target / "plan_ok.md").exists()


# ---------- A-01: relabel_file が atomic を迂回する ----------


def test_relabel_file_takes_backup(tmp_path: Path):
    """relabel_file 経由の書換で atomic の backup が取られる（write_atomic 経由の確認）。"""
    from docsweep.atomic import backup_dir_for
    from docsweep.config import load_config
    from docsweep.engine import relabel_file

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")
    md = proj / "plan_x.md"
    md.write_text("# [計画] x\n\nbody\n", encoding="utf-8")

    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    assert relabel_file(md, "[完了]", cfg) is True
    assert md.read_text(encoding="utf-8").startswith("# [完了] x")
    # backup ディレクトリに元テキストのコピーが出来ている
    bd = backup_dir_for(md)
    assert bd.is_dir()
    assert any(p.name.startswith("plan_x.md.") for p in bd.iterdir())


# ---------- A-02: config int() 剥き出しで load_config が落ちる ----------


def test_load_config_survives_non_int_postpone_threshold(tmp_path: Path):
    """postpone_warn_threshold に文字列が来ても load_config は落ちず既定に落ちる。"""
    g = tmp_path / "global.yaml"
    g.write_text(
        "due:\n  postpone_warn_threshold: 'abc'\n  postpone_alert_threshold: null\n",
        encoding="utf-8",
    )
    # 例外を出さずに load できることが本質。
    cfg = load_config(global_path=g)
    # フォールバック既定に落ちる（3 / 5）。
    assert cfg.due_warn_threshold == 3
    assert cfg.due_alert_threshold == 5


# ---------- A-03: auto-triage --apply の JSON 読み込み保護 ----------


def test_cmd_auto_triage_apply_missing_file_returns_2(tmp_path: Path, capsys):
    """apply 対象ファイルが無いときは traceback 出さずに 2 で返る。"""
    import argparse

    from docsweep.cli import cmd_auto_triage

    args = argparse.Namespace(
        suggest=False,
        apply=str(tmp_path / "nonexistent.json"),
        dry_run=True,
        roots=[str(tmp_path)],
        paths=None,
        project_dir=None,
        profile=None,
        config=str(tmp_path / "no.yaml"),
        project=None,
    )
    rc = cmd_auto_triage(args)
    assert rc == 2


def test_cmd_auto_triage_apply_broken_json_returns_2(tmp_path: Path):
    import argparse

    from docsweep.cli import cmd_auto_triage

    bad = tmp_path / "bad.json"
    bad.write_text("this is not json {{{", encoding="utf-8")
    args = argparse.Namespace(
        suggest=False,
        apply=str(bad),
        dry_run=True,
        roots=[str(tmp_path)],
        paths=None,
        project_dir=None,
        profile=None,
        config=str(tmp_path / "no.yaml"),
        project=None,
    )
    rc = cmd_auto_triage(args)
    assert rc == 2


# ---------- B-01: 空 `due:` があると重複キーが挿入される ----------


def test_update_due_replaces_empty_due_without_duplication(tmp_path: Path):
    """frontmatter に `due:` （値なし）が既にあっても、`due: <値>` が 1 本になる。"""
    from docsweep.services.due import update_due

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")
    md = proj / "plan_x.md"
    md.write_text(
        "---\ntype: plan\nstatus: planned\ndue:\n---\n# [計画] x\n\nbody\n",
        encoding="utf-8",
    )
    result = update_due(md, "2026-08-01", project_root=proj)
    text = md.read_text(encoding="utf-8")
    # `due:` 行が 1 本に置換される（重複行がない）。
    due_lines = [ln for ln in text.splitlines() if ln.strip().startswith("due:")]
    assert len(due_lines) == 1, f"due: が重複している: {due_lines}"
    assert "2026-08-01" in due_lines[0]
    assert result.new_due == "2026-08-01"


# ---------- B-04: inject.save_manifest 非アトミック ----------


def test_save_manifest_uses_atomic_replace(tmp_path: Path, monkeypatch):
    """save_manifest は tmp → os.replace のアトミック書き込みを行う（プロセス停止で truncate しない）。"""
    from docsweep import inject

    fake_manifest = tmp_path / "injected.json"
    monkeypatch.setattr(inject, "MANIFEST_PATH", fake_manifest)
    inject.save_manifest({"projects": {"a": {"blocks": {}, "preset_version": "v1"}}})
    assert fake_manifest.is_file()
    loaded = inject.load_manifest()
    assert loaded == {"projects": {"a": {"blocks": {}, "preset_version": "v1"}}}
    # 一時ファイルが残っていないこと（成功パス）。
    tmps = [p for p in tmp_path.iterdir() if p.name.startswith(".injected.json.")]
    assert tmps == []


# ---------- C-02: graph node id の basename 衝突 ----------


def test_graph_node_id_includes_project(tmp_path: Path, monkeypatch):
    """node id は project/basename の複合キー。同名 md でも edge が混線しない。"""
    from docsweep.graph.service import build_graph
    from docsweep.models import FileRecord

    # scan_records が返すレコードを直接固定して、collision シナリオを再現する。
    def _rec(path: str, project: str, related: list[str]) -> FileRecord:
        return FileRecord(
            path=path, project=project, project_root=f"/root/{project}",
            type="plan", state="planned", state_label="[計画]",
            state_source="frontmatter", title="t", summary="",
            mtime=1.0, age_days=1, archivable=False, auto_movable=False,
            related=related,
        )

    r1 = _rec("/root/proj_a/docs/local/plan_v0.1.md", "proj_a", ["plan_v0.1.md"])
    r2 = _rec("/root/proj_b/docs/local/plan_v0.1.md", "proj_b", [])
    monkeypatch.setattr("docsweep.graph.service.scan_records", lambda *a, **k: [r1, r2])
    g = build_graph(load_config(explicit_roots=[str(tmp_path)],
                                global_path=tmp_path / "no.yaml"))
    ids = {n.id for n in g.nodes}
    assert "proj_a/plan_v0.1.md" in ids
    assert "proj_b/plan_v0.1.md" in ids
    # r1 の self-related "plan_v0.1.md" は 同一プロジェクト優先で proj_a/... に解決される。
    self_edge = [e for e in g.edges if e.source == "proj_a/plan_v0.1.md"]
    assert len(self_edge) == 1
    assert self_edge[0].target == "proj_a/plan_v0.1.md"
    assert self_edge[0].resolved is True


# ---------- C-03: brief yesterday_done の並び順が逆 ----------


def test_brief_yesterday_done_sorted_newest_first(tmp_path: Path, monkeypatch):
    """yesterday_done は mtime 降順（新しいものから）で並ぶ。"""
    import time
    from datetime import date, datetime, timezone

    from docsweep.brief.service import _build_for_project
    from docsweep.models import FileRecord

    now = datetime.now(timezone.utc).astimezone()
    now_ts = time.time()

    def _done(path: str, title: str, mtime: float, age: int) -> FileRecord:
        return FileRecord(
            path=path, project="p", project_root="/a",
            type="plan", state="done", state_label="[完了]",
            state_source="frontmatter", title=title, summary="",
            mtime=mtime, age_days=age, archivable=True, auto_movable=True,
        )

    # 24h 以内の done を 3 件（age_days は混ぜて意味を持たせないようにする）
    r_new = _done("/a/newest.md", "newest", now_ts - 100, 10)
    r_mid = _done("/a/mid.md", "mid", now_ts - 3600, 1)
    r_old = _done("/a/oldest.md", "oldest", now_ts - 3600 * 20, 5)

    pb = _build_for_project([r_new, r_mid, r_old], "p", today=date.today(), now=now)
    titles = [d["title"] for d in pb.yesterday_done]
    assert titles == ["newest", "mid", "oldest"]


# ---------- C-04: timeline._resolve_date が None mtime で TypeError ----------


def test_timeline_resolve_date_survives_none_mtime(tmp_path: Path):
    """rec.mtime が None でも _resolve_date は落ちず ("", "unknown") を返す。"""
    from docsweep.models import FileRecord
    from docsweep.timeline import _resolve_date

    rec = FileRecord(
        path=str(tmp_path / "missing.md"),  # frontmatter・git・mtime いずれも取れない
        project="p", project_root=str(tmp_path),
        type="plan", state="planned", state_label="[計画]",
        state_source="frontmatter", title="t", summary="",
        mtime=None,  # type: ignore[arg-type]
        age_days=0, archivable=False, auto_movable=False,
    )
    d, src = _resolve_date(rec)
    assert d == ""
    assert src == "unknown"
