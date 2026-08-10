"""`fix-conflict --prefer h1` が実際に H1 の値を書き戻すことの回帰テスト。

2026-08-10 のドッグフーディングで再発を検出した。`fix_conflicts` は書き戻す値に
`FileRecord.state` を使っていたが、これは「frontmatter > H1 > filename」で解決した
**結果**なので、frontmatter がある conflict では常に frontmatter 自身の値になる。
つまり `--prefer h1` は自分の値を自分に書き戻すだけで、`[ok]` を報告しながら
1 文字も直らず、frontmatter の書き戻しでファイルの整形だけが変わっていた。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.config import load_config
from docsweep.detect import detect_h1_state
from docsweep.fix_conflict import fix_conflicts

LEGACY = """---
type: plan
status: planned
tags: []
---

# [様子見] レガシー形式（作業状態が status に入っている）

## context配分

| C | 種別 | 内容 | 並列 |
|---|---|---|---|
| C1 | fix | x | — |
"""

MODERN = """---
type: plan
status: draft
docsweep_state: planned
tags: []
---

# [様子見] 新形式（作業状態が docsweep_state に入っている）

## context配分

| C | 種別 | 内容 | 並列 |
|---|---|---|---|
| C1 | fix | x | — |
"""


def _workspace(tmp_path: Path, body: str) -> tuple[Path, Path]:
    root = tmp_path / "dev"
    project = root / "demo"
    queue = project / "docs" / "local"
    queue.mkdir(parents=True)
    target = queue / "plan_x.md"
    target.write_text(body, encoding="utf-8")
    return root, target


def _cfg(root: Path, tmp_path: Path):
    return load_config(explicit_roots=[str(root)], global_path=tmp_path / "no.yaml")


def test_detect_h1_state_ignores_frontmatter(tmp_path: Path) -> None:
    sm = load_config(explicit_roots=[], global_path=tmp_path / "no.yaml").state_model
    assert detect_h1_state(LEGACY, sm) == "watching"
    assert detect_h1_state(MODERN, sm) == "watching"


@pytest.mark.parametrize("body,field", [(LEGACY, "status"), (MODERN, "docsweep_state")])
def test_prefer_h1_actually_writes_the_h1_value(
    tmp_path: Path, body: str, field: str
) -> None:
    root, target = _workspace(tmp_path, body)
    cfg = _cfg(root, tmp_path)

    result = fix_conflicts(cfg, prefer="h1", paths=[str(target)])
    assert len(result.items) == 1
    assert result.items[0].fixed is True

    text = target.read_text(encoding="utf-8")
    assert f"{field}: watching" in text, text
    # 旧形式では OKF の status がそのまま作業状態なので planned は消えていること
    assert "planned" not in text.split("---")[1], text


@pytest.mark.parametrize("body", [LEGACY, MODERN])
def test_prefer_h1_keeps_the_blank_line_after_frontmatter(tmp_path: Path, body: str) -> None:
    """frontmatter を 1 フィールド直すたびに本文との間の空行を消さない。"""
    root, target = _workspace(tmp_path, body)
    cfg = _cfg(root, tmp_path)
    fix_conflicts(cfg, prefer="h1", paths=[str(target)])
    text = target.read_text(encoding="utf-8")
    assert "---\n\n# [様子見]" in text, repr(text[:200])


def test_prefer_h1_is_a_no_op_when_already_in_sync(tmp_path: Path) -> None:
    """同値なら書き込まない（成功と報告して整形だけ変える、を防ぐ）。"""
    synced = MODERN.replace("docsweep_state: planned", "docsweep_state: watching")
    root, target = _workspace(tmp_path, synced)
    cfg = _cfg(root, tmp_path)
    before = target.read_text(encoding="utf-8")

    result = fix_conflicts(cfg, prefer="h1", paths=[str(target)])
    # conflict そのものが無いので対象外になるか、対象でも書き換えない
    assert all(not item.fixed for item in result.items)
    assert target.read_text(encoding="utf-8") == before
