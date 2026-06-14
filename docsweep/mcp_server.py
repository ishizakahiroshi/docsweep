"""MCP サーバー（stdio）— AI エージェント面の標準口。

「1 コマンド＝1 MCP ツール」の粒度で公開し、CLI と無改修で両対応する。
配布は PyPI パッケージに同梱（新しい配布物を増やさない）。`uvx docsweep mcp` で起動。

依存 ``mcp``（mcp extra）が無い環境では import 時に分かるよう遅延 import する。
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .engine import apply_action, auto_sweep, promote_state, run_scan
from .index import build_index, write_index
from .inject import eject as do_eject
from .inject import inject as do_inject
from .reports import render_summary


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
    def scan() -> list[dict]:
        """全スキャンルートを走査し、各ファイルの状態・経過日数・flags・allowed_actions を返す。"""
        return [r.to_dict() for r in run_scan(config).records]

    @mcp.tool()
    def triage() -> dict:
        """要判断・要修正・保留に絞った材料を返す（AI が読んで判断するための入力）。"""
        idx = build_index(config)
        return {
            "counts": idx.counts,
            "needs_decision": idx.needs_decision,
            "needs_fix": idx.needs_fix,
            "pending": idx.pending,
        }

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
    def sweep(dry_run: bool = False) -> list[dict]:
        """done/discarded を各プロジェクトの archive/ へ移送する（watching は触らない）。"""
        return [m.to_dict() for m in auto_sweep(config, dry_run=dry_run)]

    @mcp.tool()
    def promote(from_state: str = "watching", to_state: str = "done",
                project: str | None = None, dry_run: bool = False) -> list[dict]:
        """release sweep: 溜まった様子見をまとめて完了へ昇格し archive へ移送する。"""
        return [m.to_dict() for m in promote_state(
            config, from_state=from_state, to_state=to_state, project=project, dry_run=dry_run)]

    @mcp.tool()
    def index() -> dict:
        """横断 INDEX を再生成して .docsweep/ に書き出し、集計を返す。"""
        write_index(config)
        return build_index(config).counts

    @mcp.tool()
    def summary() -> str:
        """AI に渡す圧縮 JSON（要判断・保留・要修正を要点だけに絞った INDEX）。"""
        return render_summary(config)

    @mcp.tool()
    def inject(project: str, preset: str | None = None, dry_run: bool = False) -> dict:
        """指定プロジェクトへ docsweep の運用ルール（管理ブロック＋.docsweep.yaml）を注入する。"""
        r = do_inject(Path(project), preset=preset, dry_run=dry_run)
        return {"project": r.project, "written": r.written, "skipped": r.skipped,
                "warnings": r.warnings, "yaml": r.yaml_path}

    @mcp.tool()
    def eject(project: str, purge: bool = False, dry_run: bool = False) -> dict:
        """注入した管理ブロックを剥がす（ユーザー手書きは温存）。"""
        r = do_eject(Path(project), purge=purge, dry_run=dry_run)
        return {"project": r.project, "removed": r.removed, "warnings": r.warnings,
                "purged_yaml": r.purged_yaml}

    return mcp


def run(config: Config) -> None:
    server = build_server(config)
    server.run()  # stdio トランスポート
