"""Config の ``due:`` ブロック読み込みと既定値テスト。"""

from __future__ import annotations

from pathlib import Path

from docsweep.config import DEFAULT_DUE_OFFSET_DAYS, load_config


def _yaml(p: Path, body: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_default_thresholds_and_offsets(tmp_path: Path):
    cfg = load_config(
        explicit_roots=[str(tmp_path)],
        global_path=tmp_path / "no_global.yaml",
    )
    assert cfg.due_warn_threshold == 3
    assert cfg.due_alert_threshold == 5
    assert cfg.due_default_offset_days == DEFAULT_DUE_OFFSET_DAYS


def test_yaml_overrides_thresholds(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    _yaml(
        proj / ".docsweep.yaml",
        "due:\n"
        "  postpone_warn_threshold: 2\n"
        "  postpone_alert_threshold: 7\n",
    )
    cfg = load_config(project_dir=proj, global_path=tmp_path / "no_global.yaml")
    assert cfg.due_warn_threshold == 2
    assert cfg.due_alert_threshold == 7


def test_yaml_overrides_offset_days_partially(tmp_path: Path):
    """offset_days は部分上書き（書いたキーだけ上書き・未指定は既定温存）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    _yaml(
        proj / ".docsweep.yaml",
        "due:\n"
        "  default_offset_days:\n"
        "    plan: 14\n",
    )
    cfg = load_config(project_dir=proj, global_path=tmp_path / "no_global.yaml")
    assert cfg.due_default_offset_days["plan"] == 14
    # pending と bugfix_watching は既定値が温存される
    assert cfg.due_default_offset_days["pending"] == 14  # default は 14
    assert cfg.due_default_offset_days["bugfix_watching"] == 7


def test_yaml_invalid_offset_ignored(tmp_path: Path):
    """不正な値は既定を温存（嘘の日付を量産しない）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    _yaml(
        proj / ".docsweep.yaml",
        "due:\n"
        "  default_offset_days:\n"
        "    plan: not-a-number\n",
    )
    cfg = load_config(project_dir=proj, global_path=tmp_path / "no_global.yaml")
    assert cfg.due_default_offset_days["plan"] == 7  # 既定温存
