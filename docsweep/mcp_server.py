"""MCP サーバー（stdio）— AI エージェント面の標準口。

「1 コマンド＝1 MCP ツール」の粒度で公開し、CLI と無改修で両対応する。
配布は PyPI パッケージに同梱（新しい配布物を増やさない）。
PATH に依存しない `python -m docsweep mcp` 起動を標準にする。

依存 ``mcp``（mcp extra）が無い環境では import 時に分かるよう遅延 import する。
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .engine import apply_action, auto_sweep, promote_state, run_scan
from .index import build_index, write_index
from .inject import eject as do_eject
from .inject import eject_global as do_eject_global
from .inject import inject as do_inject
from .inject import inject_global as do_inject_global
from .reports import build_triage, render_summary


def build_server(config: Config):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover - mcp extra 未導入
        raise RuntimeError("MCP には mcp extra が必要です: pip install 'docsweep[mcp]'") from e

    mcp = FastMCP("docsweep")

    def _doc_for(path: str):
        result = run_scan(config)
        target = Path(path).resolve().as_posix()
        return next((d for d in result.docs if d.record.path == target), None)

    @mcp.tool()
    def scan(project: str | None = None) -> list[dict]:
        """全スキャンルートを走査し、各ファイルの状態・経過日数・flags・allowed_actions を返す。

        ``project`` を指定するとそのプロジェクト名に絞る（sweep/promote と対称）。
        """
        records = run_scan(config).records
        if project:
            records = [r for r in records if r.project == project]
        return [r.to_dict() for r in records]

    @mcp.tool()
    def triage(project: str | None = None) -> dict:
        """セッション開始時の残作業ビュー。要判断＋保留を古い順に絞り、各項目に rel/title/
        state/type/age_days と機械実行できる actions を付けて返す（ファイル名を思い出さなくても
        「次にやるべき作業」が先頭に出る）。壊れたラベルは needs_fix に別枠で添える。

        ``project`` を指定すると当該プロジェクトの subset 版を返す（counts も per-project に揃う）。
        """
        return build_triage(config, project=project)

    @mcp.tool()
    def apply(path: str, action: str, to: str | None = None) -> dict:
        """1 ファイルに閉じた action（discard/keep/resume/relabel/promote）を機械実行する。"""
        doc = _doc_for(path)
        if doc is None:
            return {"error": "対象が見つかりません（スキャン範囲外?）", "path": path}
        try:
            return apply_action(doc, action, config, to=to).to_dict()
        except ValueError as e:
            return {"error": str(e), "path": path}

    @mcp.tool()
    def sweep(project: str | None = None, dry_run: bool = False) -> list[dict]:
        """done/discarded を各プロジェクトの archive/ へ移送する（watching は触らない）。

        ``project`` を指定するとそのプロジェクト名に絞る（promote と対称）。
        """
        moved = [m.to_dict() for m in auto_sweep(config, project=project, dry_run=dry_run)]
        if not dry_run and config.roots:
            write_index(config)
        return moved

    @mcp.tool()
    def promote(from_state: str = "watching", to_state: str = "done",
                project: str | None = None, dry_run: bool = False) -> list[dict]:
        """release sweep: 溜まった様子見をまとめて完了へ昇格し archive へ移送する。"""
        try:
            return [m.to_dict() for m in promote_state(
                config, from_state=from_state, to_state=to_state, project=project, dry_run=dry_run)]
        except ValueError as e:
            return [{"error": str(e)}]

    @mcp.tool()
    def index() -> dict:
        """横断 INDEX を再生成して .docsweep/ に書き出し、集計を返す。"""
        write_index(config)
        return build_index(config).counts

    @mcp.tool()
    def summary(project: str | None = None) -> str:
        """AI に渡す圧縮 JSON（要判断・保留・要修正を要点だけに絞った INDEX）。

        ``project`` を指定すると当該プロジェクトの subset 版を返す。
        """
        return render_summary(config, project=project)

    @mcp.tool()
    def inject(project: str, preset: str | None = None, include_guidance: bool = True,
               write_yaml: bool = True, dry_run: bool = False) -> dict:
        """指定プロジェクトへ docsweep の運用ルール（管理ブロック＋.docsweep.yaml）を注入する。

        導線をグローバルに寄せている場合は include_guidance=False でラベル節だけにできる
        （CLI の --no-guidance 相当）。write_yaml=False で .docsweep.yaml を書かない（--no-yaml 相当）。
        """
        r = do_inject(Path(project), preset=preset, include_guidance=include_guidance,
                      write_yaml=write_yaml, dry_run=dry_run)
        return {"project": r.project, "written": r.written, "skipped": r.skipped,
                "warnings": r.warnings, "yaml": r.yaml_path}

    @mcp.tool()
    def eject(project: str, purge: bool = False, dry_run: bool = False) -> dict:
        """注入した管理ブロックを剥がす（ユーザー手書きは温存）。"""
        r = do_eject(Path(project), purge=purge, dry_run=dry_run)
        return {"project": r.project, "removed": r.removed, "warnings": r.warnings,
                "purged_yaml": r.purged_yaml}

    @mcp.tool()
    def inject_global(agent: str = "claude", target: str | None = None, dry_run: bool = False) -> dict:
        """セッション開始時に triage を読む導線だけを AI ツールのグローバル設定へ注入する（全プロジェクトで効く）。"""
        try:
            r = do_inject_global(agent=agent, target=target, dry_run=dry_run)
        except ValueError as e:
            return {"error": str(e)}
        return {"project": r.project, "written": r.written, "skipped": r.skipped, "warnings": r.warnings}

    @mcp.tool()
    def eject_global(agent: str = "claude", target: str | None = None, dry_run: bool = False) -> dict:
        """グローバルへ注入した導線ブロックを剥がす。"""
        try:
            r = do_eject_global(agent=agent, target=target, dry_run=dry_run)
        except ValueError as e:
            return {"error": str(e)}
        return {"project": r.project, "removed": r.removed, "warnings": r.warnings}

    return mcp


def run(config: Config) -> None:
    server = build_server(config)
    server.run()  # stdio トランスポート
