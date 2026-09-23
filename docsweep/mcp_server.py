"""MCP サーバー（stdio）— AI エージェント面の標準口。

「1 コマンド＝1 MCP ツール」の粒度で公開し、CLI と無改修で両対応する。
配布は PyPI パッケージに同梱（新しい配布物を増やさない）。
PATH に依存しない `python -m docsweep mcp` 起動を標準にする。

依存 ``mcp``（mcp extra）が無い環境では import 時に分かるよう遅延 import する。

書き込み系（update_status / update_due / update_content / archive_done）は
:mod:`docsweep.services` のラッパとして実装し、Web UI と同じ関数を呼ぶ。
スコープ境界・``..`` 拒否・``.md`` 限定は :mod:`docsweep.security.path` で一元化。
物理削除の口は構造的に存在しない（最悪 archive 止まり・親 plan C6 の不変条件）。

tool の説明（MCP クライアントと AI エージェントに見せる description）は docstring に書かず、
``docsweep/i18n/locales/<言語>/mcp.json`` の ``mcp.tool.<tool 名>`` に置く。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .atomic import ConflictError
from .config import Config
from .i18n import SUPPORTED_LANGS, t
from .engine import apply_action, auto_sweep, promote_state, run_scan
from .aggregate_index import build_index, write_index
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


def _valid_project_dir(project: str | None, config: Config) -> Path | None:
    """MCP の project write target を、実際に scan された project root に限定する。"""
    if not project:
        return None
    try:
        target = Path(project).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    try:
        roots = {
            Path(doc.record.project_root).resolve()
            for doc in run_scan(config).docs
        }
    except (OSError, RuntimeError):
        return None
    target_key = target.as_posix().casefold()
    for root in roots:
        if root.as_posix().casefold() == target_key:
            return target
    return None


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
        raise RuntimeError(t("mcp_server.extra_required")) from e

    mcp = FastMCP("docsweep")
    # tool の説明はサーバーを作る時点の表示言語で引く（CLI は main の use_lang で先に決めている）。
    # 対応言語の一覧は言語フォルダから作るので、フォルダを足せば説明の {langs} も増える。
    langs = " / ".join(SUPPORTED_LANGS)

    def _doc_for(path: str):
        result = run_scan(config)
        target = Path(path).resolve().as_posix()
        return next((d for d in result.docs if d.record.path == target), None)

    @mcp.tool(description=t("mcp.tool.scan", langs=langs))
    def scan(project: str | None = None) -> list[dict]:
        """スキャン結果の record 一覧（project で絞れる）。"""
        records = run_scan(config).records
        if project:
            records = [r for r in records if r.project == project]
        return [r.to_dict() for r in records]

    @mcp.tool(description=t("mcp.tool.find", langs=langs))
    def find(
        owner: str | None = None,
        tags: list[str] | None = None,
        types: list[str] | None = None,
        states: list[str] | None = None,
        review_statuses: list[str] | None = None,
        project: str | None = None,
        q: str | None = None,
        target_release: str | None = None,
        missing_target_release: bool = False,
    ) -> list[dict]:
        """CLI find と同じ条件で絞った record 一覧。"""
        from .find import FindFilters, find_records, resolve_owner_alias

        filters = FindFilters(
            owner=resolve_owner_alias(owner),
            tags=list(tags or []),
            types=list(types or []),
            states=list(states or []),
            review_statuses=list(review_statuses or []),
            project=project,
            q=q,
            target_release=target_release,
            missing_target_release=missing_target_release,
        )
        return [record.to_dict() for record in find_records(config, filters)]

    @mcp.tool(description=t("mcp.tool.set_target_release", langs=langs))
    def set_target_release(
        path: str,
        target_release: str,
        expected_mtime: float | None = None,
    ) -> dict:
        """frontmatter の target_release だけを書き換える。"""
        from .release import validate_release_label
        from .config import release_tracking_for_project
        from .services.frontmatter import update_frontmatter_field

        resolved, err = _resolve_or_error(path, config)
        if err is not None or resolved is None:
            return err or {"error": "unresolved_path", "path": path, "kind": "path_scope"}
        project_root = _project_root_for(resolved, config)
        tracking = release_tracking_for_project(config, project_root)
        if tracking.mode == "disabled":
            return {
                "error": t("mcp_server.release_tracking_disabled"),
                "path": path,
                "kind": "release_tracking_disabled",
            }
        try:
            value = validate_release_label(target_release, field="target_release")
            result = update_frontmatter_field(
                resolved,
                "target_release",
                value,
                expected_mtime=expected_mtime,
            )
        except ConflictError as exc:
            return {
                "error": str(exc),
                "path": path,
                "kind": "conflict",
                "expected_mtime": exc.expected,
                "actual_mtime": exc.actual,
            }
        except (OSError, UnicodeError, ValueError) as exc:
            return {"error": str(exc), "path": path, "kind": "write"}
        return {
            **result.to_dict(),
            "release_tracking": tracking.mode,
        }

    @mcp.tool(description=t("mcp.tool.list_projects", langs=langs))
    def list_projects() -> dict:
        """既知のプロジェクトと除外リスト。"""
        from .excluded import list_known_projects, load_excluded

        return {
            "projects": list_known_projects(config),
            "excluded": sorted(load_excluded()),
        }

    @mcp.tool(description=t("mcp.tool.set_project_enabled", langs=langs))
    def set_project_enabled(root: str, enabled: bool = True) -> dict:
        """excluded.json への除外・復帰。"""
        from .excluded import disable_project, enable_project, is_excluded

        if enabled:
            enable_project(root)
        else:
            disable_project(root)
        return {"root": root, "enabled": not is_excluded(root)}

    @mcp.tool(description=t("mcp.tool.route_intent", langs=langs))
    def route_intent(text: str) -> dict:
        """自然言語の意図をサブコマンドへ振り分ける。"""
        from .intent import route_intent as _route

        return _route(text).to_dict()

    @mcp.tool(description=t("mcp.tool.doctor", langs=langs))
    def doctor() -> dict:
        """run_doctor の結果。"""
        from .doctor import run_doctor

        return run_doctor(config=config).to_dict()

    @mcp.tool(description=t("mcp.tool.day", langs=langs))
    def day(phase: str = "open") -> dict:
        """day_open / day_close の結果。"""
        from .day import day_close, day_open

        if phase == "close":
            return day_close(config).to_dict()
        return day_open(config).to_dict()

    @mcp.tool(description=t("mcp.tool.brief", langs=langs))
    def brief(project: str | None = None, all_projects: bool = False) -> dict:
        """build_brief の結果（CLI brief と同じ契約）。"""
        from .brief import build_brief

        result = build_brief(config, project=project, all_projects=all_projects)
        return result.to_dict()

    @mcp.tool(description=t("mcp.tool.capture_extract", langs=langs))
    def capture_extract(
        text: str,
        project: str | None = None,
        use_llm: bool = False,
        max_drafts: int = 5,
        allow_sensitive: bool = False,
    ) -> dict:
        """会話から草案候補を抽出する。"""
        from .capture import extract_drafts

        try:
            drafts = extract_drafts(
                text, config=config, project=project,
                max_drafts=max_drafts, use_llm=use_llm,
                allow_sensitive=allow_sensitive,
            )
        except PermissionError as e:
            return {"error": str(e), "drafts": [], "count": 0}
        return {
            "drafts": [d.to_dict() for d in drafts],
            "count": len(drafts),
        }

    @mcp.tool(description=t("mcp.tool.capture_save", langs=langs))
    def capture_save(
        drafts: list[dict],
        project: str | None = None,
        out_dir: str | None = None,
        allow_sensitive: bool = False,
    ) -> dict:
        """採用された草案を work queue へ保存する。"""
        from pathlib import Path as _P
        from .capture import save_drafts
        from .capture.models import Draft as _Draft
        from .work_queue import resolve_work_target

        # dict -> Draft へ復元
        as_drafts = [
            _Draft(
                id=d.get("id", ""),
                kind=d.get("kind", "plan"),
                title=d.get("title", ""),
                body=d.get("body", ""),
                suggested_filename=d.get("suggested_filename", "draft.md"),
                source_hint=d.get("source_hint", ""),
                project=d.get("project") or project,
                tags=list(d.get("tags") or []),
            )
            for d in (drafts or [])
        ]

        try:
            project_root, target = resolve_work_target(
                config,
                project=project,
                explicit_dir=_P(out_dir) if out_dir else None,
            )
            saved = save_drafts(
                as_drafts,
                config=config,
                target_dir=target,
                project_dir=project_root,
                allow_sensitive=allow_sensitive,
            )
        except (OSError, ValueError) as e:
            # MCP は例外を JSON-RPC error に変換する。tool 契約に沿った error dict を返す。
            return {"error": str(e), "saved": [], "count": 0}
        return {"saved": [str(p) for p in saved], "count": len(saved)}

    @mcp.tool(description=t("mcp.tool.cross", langs=langs))
    def cross(projects: list[str] | None = None) -> dict:
        """build_cross の結果。"""
        from .cross import build_cross

        result = build_cross(config, projects=projects)
        return result.to_dict()

    @mcp.tool(description=t("mcp.tool.triage", langs=langs))
    def triage(project: str | None = None) -> dict:
        """build_triage の結果。"""
        return build_triage(config, project=project)

    @mcp.tool(description=t("mcp.tool.apply", langs=langs))
    def apply(
        path: str,
        action: str,
        to: str | None = None,
        watching_days: int | None = None,
    ) -> dict:
        """1 ファイルへ action を実行する。"""
        doc = _doc_for(path)
        if doc is None:
            return {"error": t("target.not_found_in_scan"), "path": path}
        try:
            return apply_action(
                doc,
                action,
                config,
                to=to,
                watching_days=watching_days,
            ).to_dict()
        except ValueError as e:
            return {"error": str(e), "path": path}

    @mcp.tool(description=t("mcp.tool.sweep", langs=langs))
    def sweep(project: str | None = None, dry_run: bool = False) -> list[dict] | dict:
        """done / discarded を archive へ移す。"""
        batch = auto_sweep(config, project=project, dry_run=dry_run)
        moved = [m.to_dict() for m in batch]
        if not dry_run and config.roots:
            write_index(config)
        if batch.failed or batch.ref_updates or batch.ref_failed:
            return {
                "moved": moved, "failed": batch.failed,
                "ref_updates": batch.ref_updates, "ref_failed": batch.ref_failed,
            }
        return moved

    @mcp.tool(description=t("mcp.tool.promote", langs=langs))
    def promote(from_state: str = "watching", to_state: str = "done",
                project: str | None = None, dry_run: bool = False,
                due_expired_only: bool = False) -> list[dict] | dict:
        """watching を done へ昇格して archive へ移す。"""
        try:
            batch = promote_state(
                config, from_state=from_state, to_state=to_state, project=project,
                dry_run=dry_run, due_expired_only=due_expired_only,
            )
            if batch.failed or batch.ref_updates or batch.ref_failed:
                return {
                    "moved": [m.to_dict() for m in batch], "failed": batch.failed,
                    "ref_updates": batch.ref_updates, "ref_failed": batch.ref_failed,
                }
            return [m.to_dict() for m in batch]
        except ValueError as e:
            return [{"error": str(e)}]

    @mcp.tool(description=t("mcp.tool.index", langs=langs))
    def index() -> dict:
        """INDEX を書き出して集計を返す。"""
        write_index(config)
        return build_index(config).counts

    @mcp.tool(description=t("mcp.tool.summary", langs=langs))
    def summary(project: str | None = None) -> str:
        """render_summary の結果。"""
        return render_summary(config, project=project)

    @mcp.tool(description=t("mcp.tool.inject", langs=langs))
    def inject(project: str, preset: str | None = None, include_guidance: bool = True,
               write_yaml: bool = True, lang: str | None = None, dry_run: bool = False) -> dict:
        """project へ管理ブロックと .docsweep.yaml を注入する。"""
        project_dir = _valid_project_dir(project, config)
        if project_dir is None:
            return {"error": t("mcp_server.project_outside_roots"), "kind": "project_scope"}
        try:
            r = do_inject(project_dir, preset=preset, include_guidance=include_guidance,
                          write_yaml=write_yaml, lang=lang, dry_run=dry_run)
        except (OSError, ValueError) as e:
            return {"error": str(e), "kind": "write"}
        return {"project": r.project, "written": r.written, "skipped": r.skipped,
                "warnings": r.warnings, "yaml": r.yaml_path}

    @mcp.tool(description=t("mcp.tool.eject", langs=langs))
    def eject(project: str, purge: bool = False, dry_run: bool = False) -> dict:
        """注入した管理ブロックを剥がす。"""
        project_dir = _valid_project_dir(project, config)
        if project_dir is None:
            return {"error": t("mcp_server.project_outside_roots"), "kind": "project_scope"}
        try:
            r = do_eject(project_dir, purge=purge, dry_run=dry_run)
        except OSError as e:
            return {"error": str(e), "kind": "write"}
        return {"project": r.project, "removed": r.removed, "warnings": r.warnings,
                "purged_yaml": r.purged_yaml}

    @mcp.tool(description=t("mcp.tool.inject_global", langs=langs))
    def inject_global(agent: str = "claude", lang: str | None = None, dry_run: bool = False) -> dict:
        """agent 規定のグローバル設定へ導線を注入する。"""
        try:
            # MCP では任意 target を受け付けない。CLI の --global-target は人間が
            # 明示する互換経路として残し、AI 面は agent 規定の個人設定先へ固定する。
            r = do_inject_global(agent=agent, lang=lang or config.document_lang(), dry_run=dry_run)
        except (OSError, ValueError) as e:
            return {"error": str(e)}
        return {"project": r.project, "written": r.written, "skipped": r.skipped, "warnings": r.warnings}

    @mcp.tool(description=t("mcp.tool.eject_global", langs=langs))
    def eject_global(agent: str = "claude", dry_run: bool = False) -> dict:
        """グローバル設定から導線を剥がす。"""
        try:
            r = do_eject_global(agent=agent, dry_run=dry_run)
        except (OSError, ValueError) as e:
            return {"error": str(e)}
        return {"project": r.project, "removed": r.removed, "warnings": r.warnings}

    # ------------------------------------------------------------------
    # 書き込み系（C3 で追加）— services 層のラッパ。
    # 共通: スコープ境界違反は {"error": ..., "kind": "path_scope"} で返す
    # （MCP は raise しないでエラー dict 返却が AI に解釈しやすい）。
    # ------------------------------------------------------------------

    @mcp.tool(description=t("mcp.tool.update_status", langs=langs))
    def update_status(
        path: str,
        new_status: str,
        expected_mtime: float | None = None,
        watching_days: int | None = None,
    ) -> dict:
        """H1 ラベルを書き換える（完了・廃止なら archive まで進める）。"""
        resolved, err = _resolve_or_error(path, config)
        if err is not None or resolved is None:
            return err or {"error": "unresolved_path", "path": path, "kind": "path_scope"}
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
                file_type=file_type,
                expected_mtime=expected_mtime,
                watching_days=watching_days,
            )
        except StatusValidationError as e:
            return {"error": str(e), "path": path, "kind": "validation"}
        except ConflictError as e:
            return {
                "error": str(e), "path": path, "kind": "conflict",
                "expected_mtime": e.expected, "actual_mtime": e.actual,
            }
        out: dict[str, object] = {
            "path": res.path,
            "old_label": res.old_label,
            "new_label": res.new_label,
            "due_set": res.due_set,
            "new_mtime_iso": _mtime_iso(res.new_mtime),
            "postpone_count_reset": res.postpone_count_reset,
            "archive_triggered": res.archive_triggered,
        }
        if res.archive_triggered:
            # 内部で archive_done を 1 ファイル指定で呼ぶ（同じ閉じた口を通る）。ラベルの書き換えも
            # 渡し、undo で場所と一緒にラベルも戻せるようにする。
            arch = svc_archive_done(config=config, paths=[res.path], state_changes={res.path: res})
            out["archive"] = arch.to_dict()
        return out

    @mcp.tool(description=t("mcp.tool.update_due", langs=langs))
    def update_due(
        path: str, new_due: str, reason: str | None = None,
        expected_mtime: float | None = None,
    ) -> dict:
        """due を書き換えて postpone_count を増やす。"""
        resolved, err = _resolve_or_error(path, config)
        if err is not None or resolved is None:
            return err or {"error": "unresolved_path", "path": path, "kind": "path_scope"}
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

    @mcp.tool(description=t("mcp.tool.update_content", langs=langs))
    def update_content(
        path: str, new_content: str, expected_mtime: float | None = None,
        allow_sensitive: bool = False,
    ) -> dict:
        """本文を全置換する（楽観ロック）。"""
        resolved, err = _resolve_or_error(path, config)
        if err is not None or resolved is None:
            return err or {"error": "unresolved_path", "path": path, "kind": "path_scope"}
        try:
            res = svc_update_content(
                resolved,
                new_content,
                expected_mtime=expected_mtime,
                config=config,
                allow_sensitive=allow_sensitive,
            )
        except (ContentValidationError, PermissionError) as e:
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

    @mcp.tool(description=t("mcp.tool.archive_done", langs=langs))
    def archive_done(paths: list[str] | None = None, auto: bool = False) -> dict:
        """完了・廃止のファイルを archive へ移す（様子見は拒否）。"""
        validated_paths: list[str] = []
        errors: list[dict] = []
        if paths:
            for p in paths:
                resolved, err = _resolve_or_error(p, config)
                if err is not None or resolved is None:
                    if err is not None:
                        errors.append(err)
                    else:
                        errors.append({
                            "error": "unresolved_path",
                            "path": p,
                            "kind": "path_scope",
                        })
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
