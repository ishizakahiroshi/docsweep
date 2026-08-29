"""2026-07-21 監査の確定 finding のうち、2026-08-29 まで未適用だった分の回帰。

棚卸しの正本は
``docs/local/bugfix_audit-2026-07-21-findings-followup_2026-08-10.md``。
F-01 / F-02 / F-04 / F-06 / F-07 / F-08 は別 commit で解消済みで、ここで固定するのは
2026-08-29 の実測で **現行 HEAD に残っていた** 2 件だけ。

- F-05 ``update_global_roots`` の regex が ``roots:`` の途中のコメント行でブロックを
  打ち切り、その後ろのリスト項目を置換せずに残す（利用者から見ると「外したはずの
  root が消えない」。yaml としては妥当なので既存の safe_load 検証も通ってしまう）
- F-03 inject 系の書き込みのうち、guidance / ``.docsweep.yaml`` / グローバル config の
  3 経路が ``write_atomic`` を通っておらず、途中で落ちると壊れたファイルが残る
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from docsweep.server.config_write import update_global_roots


# ---- F-05: roots ブロックの境界 ------------------------------------------


def _roots_of(path: Path) -> list[str]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["roots"]


def test_removed_root_does_not_survive_a_comment_inside_the_list(tmp_path: Path) -> None:
    """リストの途中にコメントがあっても、外した root は確実に消える。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "roots:\n"
        "  - D:/dev\n"
        "  # ここは開発ルート\n"
        "  - D:/work\n"
        "ignore:\n"
        "  - docs/obsidian\n",
        encoding="utf-8",
    )

    update_global_roots([Path("D:/dev")], config_path=cfg)

    assert _roots_of(cfg) == ["D:/dev"]


def test_comment_inside_the_roots_block_is_not_lost(tmp_path: Path) -> None:
    """ブロックごと差し替えても、中にあったコメントは残す（本 module の不変条件）。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "roots:\n  - D:/dev\n  # ここは開発ルート\n  - D:/work\nignore:\n  - docs/obsidian\n",
        encoding="utf-8",
    )

    update_global_roots([Path("D:/dev")], config_path=cfg)

    assert "# ここは開発ルート" in cfg.read_text(encoding="utf-8")


def test_other_keys_and_top_level_comments_are_untouched(tmp_path: Path) -> None:
    """列 0 のコメントは ``roots:`` ブロックの外なので巻き込まない。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "roots:\n"
        "  - D:/dev\n"
        "# これはトップレベルの注釈\n"
        "ignore:\n"
        "  - docs/obsidian\n"
        "provenance:\n"
        "  enabled: true\n",
        encoding="utf-8",
    )

    update_global_roots([Path("E:/x")], config_path=cfg)

    text = cfg.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert parsed["roots"] == ["E:/x"]
    assert parsed["ignore"] == ["docs/obsidian"]
    assert parsed["provenance"] == {"enabled": True}
    assert "# これはトップレベルの注釈" in text


def test_missing_roots_key_is_appended(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("ignore:\n  - docs/obsidian\n", encoding="utf-8")

    update_global_roots([Path("D:/dev")], config_path=cfg)

    assert _roots_of(cfg) == ["D:/dev"]
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["ignore"] == ["docs/obsidian"]


# ---- F-03: inject 系の残りの書き込みを atomic に -------------------------


def test_guidance_write_leaves_the_previous_file_intact_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """guidance 生成が途中で落ちても、前の内容と壊れた一時ファイルを残さない。"""
    import docsweep.atomic as atomic_module
    import docsweep.inject.api as api

    guidance = tmp_path / "guidance.md"
    guidance.write_text("以前の内容\n", encoding="utf-8")
    monkeypatch.setattr(api, "GUIDANCE_PATH", guidance)

    real_replace = os.replace

    def _boom(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(atomic_module.os, "replace", _boom)
    with pytest.raises(OSError):
        api.write_guidance_file("ja")
    monkeypatch.setattr(atomic_module.os, "replace", real_replace)

    assert guidance.read_text(encoding="utf-8") == "以前の内容\n"
    assert list(tmp_path.glob("*.tmp")) == []


def test_inject_writes_go_through_write_atomic(tmp_path: Path) -> None:
    """guidance / .docsweep.yaml / グローバル config が生の write_text に戻らないこと。

    実行時に検出できない退行なので、ソース側で固定する。``write_atomic`` を経由
    しない書き込みが 1 つでも復活したら落ちる。
    """
    source = (Path(__file__).parents[1] / "docsweep" / "inject" / "api.py").read_text(
        encoding="utf-8"
    )
    assert "GUIDANCE_PATH.write_text(" not in source
    assert "GLOBAL_CONFIG_PATH.write_text(" not in source
    assert "yaml_path.write_text(" not in source
