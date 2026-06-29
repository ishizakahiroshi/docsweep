"""``docsweep migrate-frontmatter`` — 既存 md に OKF frontmatter を非破壊で挿入する。

H1 ステータスラベルとファイル名プレフィックスから ``type`` / ``status`` を導出し、
``tags: []`` / ``owner: `` / ``review_status: draft`` / ``related: []`` /
``last_reviewed: <today>`` の最小ブロックを先頭に追加する。

不変条件:
- H1 ラベルは絶対に書き換えない（後方互換 100%）
- 既に frontmatter がある md はスキップする（``--force`` 等は持たない・誤上書き防止）
- 本文・末尾改行も触らない
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config
from .detect import _FRONTMATTER_RE, detect_status
from .engine import run_scan


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _state_to_status(state_key: str | None) -> str:
    """内部 state key → frontmatter ``status:`` 値（OKF 規約）。

    ``new_doc`` テンプレと同じ語彙を採用（plan→planned / bugfix→in-progress / pending→pending）。
    """
    if not state_key:
        return "planned"
    mapping = {
        "planned": "planned",
        "in-progress": "in-progress",
        "watching": "watching",
        "done": "done",
        "discarded": "discarded",
        "pending": "pending",
    }
    return mapping.get(state_key, state_key)


def _build_frontmatter_block(*, doc_type: str, status: str, today: str) -> str:
    """先頭に挿入する最小 frontmatter ブロック（末尾改行 1 つ・本文側に空行は足さない）。"""
    return (
        "---\n"
        f"type: {doc_type}\n"
        f"status: {status}\n"
        "tags: []\n"
        "owner: \n"
        "review_status: draft\n"
        "related: []\n"
        f"last_reviewed: {today}\n"
        "---\n"
    )


@dataclass
class MigratePlan:
    """1 ファイルへの挿入予定。"""

    path: str
    doc_type: str
    status: str
    skipped_reason: str | None = None  # None なら適用可能


@dataclass
class MigrateResult:
    planned: list[MigratePlan] = field(default_factory=list)
    skipped: list[MigratePlan] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "planned": [
                {"path": p.path, "type": p.doc_type, "status": p.status} for p in self.planned
            ],
            "skipped": [
                {"path": p.path, "reason": p.skipped_reason} for p in self.skipped
            ],
            "applied": list(self.applied),
        }


def plan_migration(config: Config, *, project: str | None = None) -> MigrateResult:
    """全 plan/bugfix/pending を走査して、frontmatter 未挿入のものを抽出する。"""
    result = MigrateResult()
    scan_result = run_scan(config)
    for doc in scan_result.docs:
        rec = doc.record
        if project and rec.project != project:
            continue
        if rec.type is None:
            continue
        path = Path(rec.path)
        try:
            text = path.read_text(encoding="utf-8", newline="")
        except (OSError, UnicodeDecodeError) as e:
            result.skipped.append(MigratePlan(
                path=rec.path, doc_type=rec.type or "?", status="?",
                skipped_reason=f"読み取り失敗: {e}",
            ))
            continue
        if _FRONTMATTER_RE.match(text):
            result.skipped.append(MigratePlan(
                path=rec.path, doc_type=rec.type, status=rec.state or "?",
                skipped_reason="既に frontmatter があります",
            ))
            continue
        status = _state_to_status(rec.state)
        result.planned.append(MigratePlan(
            path=rec.path, doc_type=rec.type, status=status,
        ))
    return result


def apply_migration(
    config: Config, *, project: str | None = None, today: str | None = None
) -> MigrateResult:
    """``plan_migration`` の結果を実際に各 md へ挿入する。"""
    today = today or _today()
    result = plan_migration(config, project=project)
    for plan in result.planned:
        path = Path(plan.path)
        try:
            text = path.read_text(encoding="utf-8", newline="")
        except (OSError, UnicodeDecodeError) as e:
            plan.skipped_reason = f"読み取り失敗: {e}"
            result.skipped.append(plan)
            continue
        # 二重チェック（plan 後にユーザーが手で frontmatter を入れたケース）。
        if _FRONTMATTER_RE.match(text):
            plan.skipped_reason = "既に frontmatter があります（再検出）"
            result.skipped.append(plan)
            continue
        block = _build_frontmatter_block(
            doc_type=plan.doc_type, status=plan.status, today=today,
        )
        new_text = block + text
        # H1 ラベルが書き換わらないこと: 単純な前置挿入なので不変。
        from .atomic import update_line

        def _xform(_t: str, _block: str = block) -> str:
            return _block + _t

        update_line(path, transform=_xform)
        result.applied.append(plan.path)
    # planned は適用後も「予定だった」リストとして残す（CLI 表示はそれを使う）。
    return result


def detect_doc_type(filename: str, config: Config) -> str | None:
    """ファイル名から doc_type を引く（migrate の単発判定用）。"""
    td = config.match_type(filename)
    return td.name if td else None


def detect_status_for_path(path: Path, config: Config) -> str | None:
    """指定ファイル単体に対する frontmatter status 値を割り出す（テスト用エントリポイント）。"""
    text = path.read_text(encoding="utf-8", newline="")
    td = config.match_type(path.name)
    det = detect_status(text=text, filename=path.name, sm=config.state_model, _type=td)
    return _state_to_status(det.state_key)
