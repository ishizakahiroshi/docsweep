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

from docsweep import session_logs


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


@pytest.fixture(autouse=True)
def isolate_session_log_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """全テストで「実行中セッションの生ログ」解決を実環境から切り離す。

    ``AIMetadata.resolve`` は env と provider のホームから transcript を解決して
    frontmatter に載せる。遮断しないと provenance 系テストの期待値が「テストを
    流した開発者のセッション」に依存する（索引 DB と同型の実環境混入）。

    env を落とすだけでは足りない。Codex / Grok / Copilot / Cursor は cwd 一致で
    探すため、テストを流した開発者の実ホームに同じ cwd のセッションが残っていると
    それを拾う。``_home`` だけを差し替えるのは、``HOME`` / ``USERPROFILE`` を丸ごと
    移すと git config などテスト外の挙動まで巻き込むため。
    """
    for name in (
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CONFIG_DIR",
        "DOCSWEEP_AI_SESSION_LOG",
        "CODEX_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(session_logs, "_home", lambda: tmp_path / "no-such-home")
