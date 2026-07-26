"""pytest 共通 fixture。

**テストを実環境の索引（``~/.docsweep/index.db``）から切り離すのが主目的。**

``scan_records()`` は「索引があれば索引から、無ければ run_scan へフォールバック」で
動くため、開発機で ``docsweep index-sync`` を常用していると、テストが tmp_path に
作ったワークスペースではなく開発者本人の全プロジェクトを読んでしまう。2026-07-26 の
実測では、索引ファイルが存在するだけで 22 件が失敗した（索引を退避すると 653 件全通過。
中身ではなく「存在」が失敗条件）。CI は索引が無いので顕在化せず、ローカルだけが常時
赤くなって本物の退行を見落とす状態だった。

``docsweep.index.db_path()`` は ``override > DOCSWEEP_INDEX_DB > 既定パス`` の優先順を
持つので、環境変数を tmp_path 配下へ向けるだけで遮断できる（本体の変更は不要）。
``db_path_override`` を明示的に渡しているテストは override が最優先なので干渉しない。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_index_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """全テストで ``DOCSWEEP_INDEX_DB`` をテスト専用の tmp パスへ向ける。

    ファイルは作らない（未存在 = 索引なし扱い）。索引を使いたいテストは自分で
    ``sync_index`` を呼ぶか ``db_path_override`` を渡す。スコープは function で、
    テスト間で索引を共有しない。
    """
    target = tmp_path / "docsweep-index.db"
    monkeypatch.setenv("DOCSWEEP_INDEX_DB", str(target))
    return target
