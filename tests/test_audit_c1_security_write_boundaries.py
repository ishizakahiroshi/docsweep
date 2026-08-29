"""監査 C1: security・書き込み境界の決定的回帰。

親 plan の C1 が要求する 6 つの不変条件のうち、既存テストで押さえられていなかった 3 つを
ここで固定する。残る 3 つの所在は次のとおり（重複して書かない）。

- MCP inject/eject が scan 対象の実在 project だけを許すこと:
  ``tests/test_mcp_write_tools.py::test_mcp_project_inject_and_eject_require_scanned_project``
- JWT / Authorization Bearer / AWS secret / 24 文字以上の password 代入が high 検出されること:
  ``tests/test_audit_v0_4_0.py``
- suggestion の path/reason/action が DOM text として描画されること:
  ``tests/test_offline_assets.py::test_suggestion_fields_are_rendered_as_dom_text_not_html``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.config import load_config
from docsweep.excluded import (
    ExcludedConfigError,
    filter_records_by_excluded,
    load_excluded,
)
from docsweep.work_queue import WorkQueueError, resolve_work_target


def _link_dir(link: Path, target: Path) -> bool:
    """``link`` を ``target`` へのディレクトリリンクにする。作れなければ False。

    Windows でシンボリックリンクを作るには開発者モードか管理者権限が要るため、
    権限が無い環境ではテストごと skip する（CI・他 OS では通る）。
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return True


def _cfg(root: Path, tmp_path: Path):
    return load_config(explicit_roots=[str(root)], global_path=tmp_path / "no-global.yaml")


# ---- queue root の実解決境界（F-14） ----


def test_queue_root_link_is_allowed_but_escaping_child_link_is_rejected(tmp_path: Path) -> None:
    """queue root 自体のリンクは許可し、その内側から外へ抜けるリンクだけ拒否する。

    private queue をリポジトリ外へ逃がす運用（``docs/local`` を junction 化）は正規利用なので
    全面禁止にはできない。禁止すべきなのは「queue の内側に置いたリンクから、project でも
    queue でもない場所へ書き込む」経路だけ。
    """
    root = tmp_path / "dev"
    repo = root / "myrepo"
    (repo / ".git").mkdir(parents=True)

    central = root / "central" / "myrepo" / "local"
    central.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    if not _link_dir(repo / "docs" / "local", central):
        pytest.skip("ディレクトリリンクを作成できない環境")

    cfg = _cfg(root, tmp_path)

    # queue root 自体がリンクでも通常の作業先解決は通る。
    project_root, target = resolve_work_target(cfg, project_dir=repo)
    assert project_root == repo.resolve()
    assert target.name == "local"

    # queue の内側に置いたリンクから外へ抜ける書き込みは拒否する。
    escape = repo / "docs" / "local" / "escape"
    if not _link_dir(escape, outside):
        pytest.skip("ディレクトリリンクを作成できない環境")

    with pytest.raises(WorkQueueError):
        resolve_work_target(cfg, project_dir=repo, explicit_dir=escape)


# ---- 手編集退避が repo を汚さない（F-16） ----


def test_inject_hand_edit_backup_never_lands_in_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """管理ブロックを手編集した状態で再 inject しても、repo 内に ``.bak`` を作らない。

    退避先は docsweep の private 管理領域（manifest と同じ親）に限定する。repo 直下へ置くと
    そのまま tracked になり、手書き内容が公開リポへ乗る。
    """
    import docsweep.inject as inject_module
    import docsweep.inject.blocks as blocks_module
    from docsweep.inject import inject

    manifest = tmp_path / "private" / "injected.json"
    monkeypatch.setattr(inject_module, "MANIFEST_PATH", manifest)
    # _private_backup は blocks 側で束縛した MANIFEST_PATH を見るので、両方を tmp へ向ける。
    monkeypatch.setattr(blocks_module, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(inject_module, "GLOBAL_CONFIG_PATH", tmp_path / "global_config.yaml")

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("# My Project\n\n手書きの内容。\n", encoding="utf-8")
    inject(proj, preset="claude-jp")

    text = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert "docsweep:managed:start" in text
    # 管理ブロックの中身を人が書き換えた状態を作る（ブロック内へ 1 行足す）。
    start = text.index("docsweep:managed:start")
    line_end = text.index("\n", start) + 1
    edited = text[:line_end] + "人が後から書き足した行。\n" + text[line_end:]
    (proj / "CLAUDE.md").write_text(edited, encoding="utf-8")

    inject(proj, preset="claude-jp")

    assert list(proj.rglob("*.bak")) == []
    backups = sorted((manifest.parent / "inject-backups").glob("*.bak"))
    assert backups, "手編集の退避が private 管理領域に作られていません"


# ---- excluded 設定の失敗は fail-closed（F-30） ----


def test_broken_excluded_config_raises_instead_of_showing_everything(tmp_path: Path) -> None:
    """excluded 設定を読めないとき、黙って全件表示へ倒れない。

    除外は「見せない」ための設定なので、読めなかったときに全件へフォールバックすると
    private なプロジェクトが board や scan に出る。安全側は明示エラー。
    """
    broken = tmp_path / "excluded.json"
    broken.write_text("{ not json", encoding="utf-8")

    with pytest.raises(ExcludedConfigError):
        load_excluded(path=broken)

    class _Rec:
        project_root = str(tmp_path / "secret-project")

    with pytest.raises(ExcludedConfigError):
        filter_records_by_excluded([_Rec()], path=broken)


def test_excluded_config_with_wrong_shape_is_also_rejected(tmp_path: Path) -> None:
    """JSON として読めても形が違えば拒否する（空集合として素通りさせない）。"""
    wrong = tmp_path / "excluded.json"
    wrong.write_text('{"excluded": "not-a-list"}', encoding="utf-8")

    with pytest.raises(ExcludedConfigError):
        load_excluded(path=wrong)
