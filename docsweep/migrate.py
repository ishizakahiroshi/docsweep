"""``docsweep migrate-frontmatter`` — 既存 md に OKF frontmatter を非破壊で挿入する。

H1 ステータスラベルとファイル名プレフィックスから ``type`` / ``status`` を導出し、
``tags: []`` / ``owner: `` / ``review_status: draft`` / ``related: []`` /
``last_reviewed: <today>`` の最小ブロックを先頭に追加する。

「どんな素の md でも OKF 形式に整える」フォーマッタとして動く:
- frontmatter が無い md → ブロックごと先頭挿入（mode=``insert``）
- frontmatter はあるが OKF キーが欠けている md（``due:`` だけ・``type:`` だけ等、
  形は問わない）→ 既存ブロックへ不足キーだけ追記（mode=``upgrade``）
- OKF キーが全部揃っている md → スキップ（何もしない）

不変条件:
- H1 ラベルは絶対に書き換えない（後方互換 100%）
- 既存 frontmatter の既存キーは値・行とも一切書き換えない（不足分の追記のみ・誤上書き防止）
- 本文・末尾改行も触らない
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config
from .detect import detect_status
from .engine import run_scan
from .services.frontmatter import read_frontmatter, read_frontmatter_text

# 書き換え用の厳密版（共通 reader は閉じフェンス後の空行まで \s* で飲み込むため、
# 再構築に使うと本文側の空行が失われる。行内空白のみ許容し、本文をバイト位置で温存する）。
_FRONTMATTER_SPLIT_RE = re.compile(r"^---[ \t]*\n(.*?\n)---[ \t]*\n", re.DOTALL)

# OKF frontmatter の必須キー集合（_okf_key_lines と同順・同内容の正典）。
_OKF_KEYS = ("type", "status", "tags", "owner", "review_status", "related", "last_reviewed")


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


def _okf_key_lines(*, doc_type: str, status: str, today: str) -> list[tuple[str, str]]:
    """OKF キーと行表現の対（挿入順の正典。new_doc テンプレと同順）。"""
    return [
        ("type", f"type: {doc_type}"),
        ("status", f"status: {status}"),
        ("tags", "tags: []"),
        ("owner", "owner: "),
        ("review_status", "review_status: draft"),
        ("related", "related: []"),
        ("last_reviewed", f"last_reviewed: {today}"),
    ]


def _build_frontmatter_block(*, doc_type: str, status: str, today: str) -> str:
    """先頭に挿入する最小 frontmatter ブロック（末尾改行 1 つ・本文側に空行は足さない）。"""
    lines = "\n".join(line for _, line in _okf_key_lines(doc_type=doc_type, status=status, today=today))
    return f"---\n{lines}\n---\n"


def _parse_frontmatter_keys(text: str, path: Path) -> set[str] | None:
    """先頭 frontmatter のトップレベルキー集合を返す。無い/壊れている場合は None。"""
    _data, body = read_frontmatter_text(text)
    if body == text:
        return None
    data = read_frontmatter(path)
    if data is None:
        return None
    return {str(k) for k in data.keys()}


def _upgrade_frontmatter(
    text: str, *, doc_type: str, status: str, today: str, existing_keys: set[str]
) -> str | None:
    """既存 frontmatter へ不足 OKF キーを追記した全文を返す（既存行は不変）。

    再構築に失敗する形（閉じフェンスが厳密版で取れない等）は None を返し、呼び出し側でスキップする。
    """
    m = _FRONTMATTER_SPLIT_RE.match(text)
    if not m:
        return None
    missing = [
        line
        for key, line in _okf_key_lines(doc_type=doc_type, status=status, today=today)
        if key not in existing_keys
    ]
    if not missing:
        return None
    # 不足キーを正典順で先頭に置き、既存行（due: 等）はそのまま後ろへ温存する。
    return "---\n" + "\n".join(missing) + "\n" + m.group(1) + "---\n" + text[m.end():]


@dataclass
class MigratePlan:
    """1 ファイルへの挿入予定。"""

    path: str
    doc_type: str
    status: str
    skipped_reason: str | None = None  # None なら適用可能
    mode: str = "insert"  # insert=frontmatter 無し / upgrade=旧形式（due: 等のみ）へ追記


@dataclass
class MigrateResult:
    planned: list[MigratePlan] = field(default_factory=list)
    skipped: list[MigratePlan] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "planned": [
                {"path": p.path, "type": p.doc_type, "status": p.status, "mode": p.mode}
                for p in self.planned
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
            text = path.open("r", encoding="utf-8", newline="").read()
        except (OSError, UnicodeDecodeError) as e:
            result.skipped.append(MigratePlan(
                path=rec.path, doc_type=rec.type or "?", status="?",
                skipped_reason=f"読み取り失敗: {e}",
            ))
            continue
        keys = _parse_frontmatter_keys(text, path)
        status = _state_to_status(rec.state)
        _data, body = read_frontmatter_text(text)
        if keys is None and body != text:
            result.skipped.append(MigratePlan(
                path=rec.path, doc_type=rec.type, status=rec.state or "?",
                skipped_reason="frontmatter を解析できません（YAML 不正）",
            ))
            continue
        if keys is not None:
            missing = [k for k in _OKF_KEYS if k not in keys]
            if not missing:
                result.skipped.append(MigratePlan(
                    path=rec.path, doc_type=rec.type, status=rec.state or "?",
                    skipped_reason="OKF frontmatter が揃っています",
                ))
                continue
            # 部分 frontmatter（due: だけ・type: だけ等）→ 不足キーを追記する upgrade 対象。
            result.planned.append(MigratePlan(
                path=rec.path, doc_type=rec.type, status=status, mode="upgrade",
            ))
            continue
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
            text = path.open("r", encoding="utf-8", newline="").read()
        except (OSError, UnicodeDecodeError) as e:
            plan.skipped_reason = f"読み取り失敗: {e}"
            result.skipped.append(plan)
            continue
        from .atomic import update_line

        if plan.mode == "upgrade":
            # 二重チェック（plan 後に手で frontmatter が完成された/壊れたケースは触らない）。
            keys = _parse_frontmatter_keys(text, path)
            if keys is None or all(k in keys for k in _OKF_KEYS):
                plan.skipped_reason = "frontmatter が変化しています（再検出・スキップ）"
                result.skipped.append(plan)
                continue
            new_text = _upgrade_frontmatter(
                text, doc_type=plan.doc_type, status=plan.status,
                today=today, existing_keys=keys,
            )
            if new_text is None:
                plan.skipped_reason = "frontmatter を再構築できません（スキップ）"
                result.skipped.append(plan)
                continue

            def _xform_upgrade(_t: str, _new: str = new_text) -> str:
                return _new

            update_line(path, transform=_xform_upgrade)
            result.applied.append(plan.path)
            continue

        # 二重チェック（plan 後にユーザーが手で frontmatter を入れたケース）。
        _data, body = read_frontmatter_text(text)
        if body != text:
            plan.skipped_reason = "既に frontmatter があります（再検出）"
            result.skipped.append(plan)
            continue
        block = _build_frontmatter_block(
            doc_type=plan.doc_type, status=plan.status, today=today,
        )

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
    text = path.open("r", encoding="utf-8", newline="").read()
    td = config.match_type(path.name)
    det = detect_status(text=text, filename=path.name, sm=config.state_model, _type=td)
    return _state_to_status(det.state_key)
