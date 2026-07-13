"""JSON 契約の単一正本（dataclass）。

CLI ``--json`` / Web API / MCP ツールが同じ構造を共有する。core は重い依存を持たない
方針なので、ここは dataclass で表現し、Web 層（FastAPI）が必要なら pydantic で包む。

allowed_actions は「閉じた集合」にして AI エージェントの返事を機械実行可能にする。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class Action(str, Enum):
    """triage で AI / 人間が選べる閉じた操作集合。"""

    DISCARD = "discard"  # [廃止] にして archive へ
    KEEP = "keep"  # 現状維持（何もしない）
    RESUME = "resume"  # 様子見/廃止候補 → 実行中 へ戻す
    RELABEL = "relabel"  # 任意ラベルへ書き換え（to を伴う）
    PROMOTE = "promote"  # 様子見 → 完了 へ昇格し archive へ（release sweep）


class Flag(str, Enum):
    """自動判定できない/注意が要るファイルの種類。"""

    NEEDS_FIX = "needs_fix"  # ラベル欠落・パース不能
    CONFLICT = "conflict"  # frontmatter/H1/filename が食い違う
    TYPE_MISMATCH = "type_mismatch"  # plan_ なのに bugfix 内容 等
    NEEDS_DECISION = "needs_decision"  # 陳腐化した [計画]（stale 超え）
    STALE = "stale"  # stale_days 超過
    OVERDUE_TODO = "overdue_todo"  # 計画/実行中/保留で due 超過（やり忘れ）
    OVERDUE_GRADUATE = "overdue_graduate"  # 様子見で due 超過（卒業判定どき）
    DUE_PARSE_ERROR = "due_parse_error"  # due フィールドがあるがパース不能


@dataclass
class FileRecord:
    """スキャンで得た 1 ファイルの判定結果。"""

    path: str  # 絶対パス（POSIX 表記）
    project: str  # プロジェクト名（マーカーで判定した境界フォルダ名）
    project_root: str  # プロジェクト境界の絶対パス（archive 移送先の基準）
    type: str | None  # plan/bugfix/pending/...（None=種別不明）
    state: str | None  # 内部状態キー（None=ラベル検出不能）
    state_label: str | None  # 表示ラベル（例 "[完了]"）
    state_source: str  # frontmatter | h1 | filename | none
    title: str | None  # H1 タイトル（ラベル除去後）
    summary: str | None  # 概要セクション先頭 1〜2 行
    mtime: float  # 最終更新（epoch 秒）
    age_days: int  # 最終更新からの経過日数
    archivable: bool  # この状態は archive 対象か
    auto_movable: bool  # --auto で自動移送してよいか
    due: str | None = field(default=None)  # frontmatter due: YYYY-MM-DD（archive には絡めない）
    due_parse_error: bool = field(default=False)  # due フィールドがあるがパース不能
    flags: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)
    # OKF（Open Knowledge Format）併用フィールド。frontmatter にあれば取り込む。
    # H1 ラベルのみのファイルでは空値で、既存挙動を一切変えない。
    tags: list[str] = field(default_factory=list)
    owner: str | None = field(default=None)
    review_status: str | None = field(default=None)
    related: list[str] = field(default_factory=list)
    last_reviewed: str | None = field(default=None)
    # sweep 挙動の指示。既定 None = ``archive_with_release`` 相当（通常の archive 対象）。
    # ``never_archive`` を指定するとリリース sweep / promote / apply_action(discard/promote)
    # で archive 移送されない（可視化はする）。archive_with_release を明示指定した場合も
    # None と同じ挙動（コード上は "既定" と同義）。
    docsweep_policy: str | None = field(default=None)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def path_obj(self) -> Path:
        return Path(self.path)


@dataclass
class MoveLogEntry:
    """移動ログ JSONL の 1 行。eject/復元・将来の SQLite 移行の土台。

    ``batch_id`` は同一の一括操作で同時に書かれたエントリをまとめる ID（Undo に使う）。
    1 件単位の archive では None（既存挙動互換）。bulk_archive 等の services 経由は
    実行ごとに同じ ID を全件に振る。
    """

    ts: str  # ISO8601 ローカル日時
    op: str  # archive | relabel | eject | restore | promote
    project: str
    status: str | None  # 移送時点の内部状態
    src: str
    dst: str | None  # relabel 等で移動を伴わない場合 None
    batch_id: str | None = None  # 同時実行バッチの識別子（Undo 用・任意）

    def to_dict(self) -> dict:
        return asdict(self)
