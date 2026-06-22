"""MCP サーバー（stdio）— AI エージェント面の標準口。

「1 コマンド＝1 MCP ツール」の粒度で公開し、CLI と無改修で両対応する。
配布は PyPI パッケージに同梱（新しい配布物を増やさない）。
PATH に依存しない `python -m docsweep mcp` 起動を標準にする。

依存 ``mcp``（mcp extra）が無い環境では import 時に分かるよう遅延 import する。

書き込み系（update_status / update_due / update_content / archive_done）は
:mod:`docsweep.services` のラッパとして実装し、Web UI と同じ関数を呼ぶ。
スコープ境界・``..`` 拒否・``.md`` 限定は :mod:`docsweep.security.path` で一元化。
物理削除の口は構造的に存在しない（最悪 archive 止まり・親 plan C6 の不変条件）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .atomic import ConflictError
from .config import Config
from .engine import apply_action, auto_sweep, promote_state, run_scan
from .index import build_index, write_index
from .inject import eject as do_eject
from .inject import eject_global as do_eject_global
from .inject import inject as do_inject
from .inject import inject_global as do_inject_global
from .reports import build_triage, render_summary
from .security import PathScopeError, resolve_writable_md
from .services.archive import archive_done as svc_archive_done
from .services.content import (
    ContentValidationError,
    update_content as svc_update_content,
)
from .services.due import DueParseError, update_due as svc_update_due
from .services.status import (
    StatusValidationError,
    update_status as svc_update_status,
)


def _mtime_iso(mtime: float | None) -> str | None:
    if not mtime:
        return None
    try:
        return datetime.fromtimestamp(float(mtime)).astimezone().isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return None


def _project_root_for(abs_path: Path, config: Config) -> Path:
    """書き込み対象から所属プロジェクト境界（``.docsweep/state.json`` の置き場）を辿る。

    最寄りの祖先で ``config.project_markers`` のどれかを持つディレクトリ。見つからなければ
    スキャンルートを返す（state.json はスキャンルートに置かれる）。
    """
    cur = abs_path.parent
    while True:
        for marker in config.project_markers:
            if (cur / marker).exists():
                return cur
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    # フォールバック: スキャンルート（複数あれば最初に containing なもの）。
    for root in config.roots:
        try:
            abs_path.relative_to(root.resolve())
            return root.resolve()
        except ValueError:
            continue
    return abs_path.parent


def _resolve_or_error(path: str, config: Config) -> tuple[Path | None, dict | None]:
    """スコープ境界チェック。OK で (Path, None)、NG で (None, error dict)。"""
    try:
        resolved = resolve_writable_md(path, roots=list(config.roots))
        return resolved, None
    except PathScopeError as e:
        return None, {"error": str(e), "path": path, "kind": "path_scope"}


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

    # ------------------------------------------------------------------
    # 書き込み系（C3 で追加）— services 層のラッパ。
    # 共通: スコープ境界違反は {"error": ..., "kind": "path_scope"} で返す
    # （MCP は raise しないでエラー dict 返却が AI に解釈しやすい）。
    # ------------------------------------------------------------------

    @mcp.tool()
    def update_status(
        path: str, new_status: str, expected_mtime: float | None = None
    ) -> dict:
        """MD の H1 ラベルを ``new_status`` に書き換える。

        ``new_status`` は日本語ラベル（"計画" / "実行中" / "対応中" / "様子見" /
        "保留" / "完了" / "廃止"）または内部 state key を受け付ける。
        ``[完了]`` / ``[廃止]`` 指定時は内部で ``archive_done`` を呼んで一気通貫で
        archive 移送する（人クリック相当の意思決定が MCP 呼び出しに含まれている前提）。
        """
        resolved, err = _resolve_or_error(path, config)
        if err is not None:
            return err
        # 日本語ラベル → 内部 state key の解決（"計画" でも "planned" でも通す）。
        st = config.state_model.match(new_status)
        new_state_key = st.key if st else new_status
        # ファイル種別を推定（バリデーション用・推定不能なら緩判定）。
        file_type = None
        type_def = config.match_type(resolved.name)
        if type_def is not None:
            file_type = type_def.name
        project_root = _project_root_for(resolved, config)
        try:
            res = svc_update_status(
                resolved, new_state_key,
                project_root=project_root, config=config,
                file_type=file_type, expected_mtime=expected_mtime,
            )
        except StatusValidationError as e:
            return {"error": str(e), "path": path, "kind": "validation"}
        except ConflictError as e:
            return {
                "error": str(e), "path": path, "kind": "conflict",
                "expected_mtime": e.expected, "actual_mtime": e.actual,
            }
        out = {
            "path": res.path,
            "old_label": res.old_label,
            "new_label": res.new_label,
            "new_mtime_iso": _mtime_iso(res.new_mtime),
            "postpone_count_reset": res.postpone_count_reset,
            "archive_triggered": res.archive_triggered,
        }
        if res.archive_triggered:
            # 内部で archive_done を 1 ファイル指定で呼ぶ（同じ閉じた口を通る）。
            arch = svc_archive_done(config=config, paths=[res.path])
            out["archive"] = arch.to_dict()
        return out

    @mcp.tool()
    def update_due(
        path: str, new_due: str, reason: str | None = None,
        expected_mtime: float | None = None,
    ) -> dict:
        """frontmatter ``due:`` を書き換え、``postpone_count`` を +1 する。

        ``new_due`` は ``YYYY-MM-DD`` または ``today`` / ``+1d`` / ``+1w`` / ``+1m``。
        過去日を指定された場合も警告のみで拒否しない（やり忘れ列に残るだけ）。
        """
        resolved, err = _resolve_or_error(path, config)
        if err is not None:
            return err
        project_root = _project_root_for(resolved, config)
        # しきい値は ``.docsweep.yaml`` の ``due:`` ブロックから読まれた Config 値を使う。
        try:
            res = svc_update_due(
                resolved, new_due,
                project_root=project_root, reason=reason,
                expected_mtime=expected_mtime,
                warn_threshold=config.due_warn_threshold,
                alert_threshold=config.due_alert_threshold,
            )
        except DueParseError as e:
            return {"error": str(e), "path": path, "kind": "validation"}
        except ConflictError as e:
            return {
                "error": str(e), "path": path, "kind": "conflict",
                "expected_mtime": e.expected, "actual_mtime": e.actual,
            }
        return {
            "path": res.path,
            "old_due": res.old_due,
            "new_due": res.new_due,
            "postpone_count": res.postpone_count,
            "warning": res.warning,
            "new_mtime_iso": _mtime_iso(res.new_mtime),
        }

    @mcp.tool()
    def update_content(
        path: str, new_content: str, expected_mtime: float | None = None,
    ) -> dict:
        """MD 本文を全置換する（楽観ロック対応）。

        ``expected_mtime`` 不一致は ``kind=conflict`` で返却。Web UI からは必須。
        """
        resolved, err = _resolve_or_error(path, config)
        if err is not None:
            return err
        try:
            res = svc_update_content(
                resolved, new_content, expected_mtime=expected_mtime,
            )
        except ContentValidationError as e:
            return {"error": str(e), "path": path, "kind": "validation"}
        except ConflictError as e:
            return {
                "error": str(e), "path": path, "kind": "conflict",
                "expected_mtime": e.expected, "actual_mtime": e.actual,
            }
        return {
            "path": res.path,
            "new_mtime_iso": _mtime_iso(res.new_mtime),
            "new_sha256": res.new_sha256,
            "warnings": res.warnings,
        }

    @mcp.tool()
    def archive_done(paths: list[str] | None = None, auto: bool = False) -> dict:
        """``[完了]`` / ``[廃止]`` のファイルを archive へ移送する。

        ``paths`` 指定で個別移送、``auto=True`` で全プロジェクト一括。両方未指定は
        破壊安全側で何もしない（空結果）。``[様子見]`` は明示指定でも拒否（寝かせを守る）。
        """
        validated_paths: list[str] = []
        errors: list[dict] = []
        if paths:
            for p in paths:
                resolved, err = _resolve_or_error(p, config)
                if err is not None:
                    errors.append(err)
                    continue
                validated_paths.append(str(resolved))
        res = svc_archive_done(
            config=config,
            paths=validated_paths if paths else None,
            auto=auto,
        )
        out = res.to_dict()
        if errors:
            out["errors"] = errors
        return out

    return mcp


def run(config: Config) -> None:
    server = build_server(config)
    server.run()  # stdio トランスポート
