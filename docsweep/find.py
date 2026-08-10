"""``docsweep find`` — 自由クエリ。

``--owner X`` / ``--tag Y`` / ``--type plan`` / ``--status 実行中`` /
``--review-status draft`` / ``--project P`` を AND で組み合わせる。

``triage --tag`` の上位コマンドとして位置づけ、``triage`` は内部で ``find_records`` を
呼ぶ形に整理する（既存挙動の互換は ``filters`` ＝空の呼び出しで保つ）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .engine import run_scan
from .models import FileRecord


@dataclass
class FindFilters:
    """``find`` の AND クエリ。空 = 全件通過。"""

    owner: str | None = None  # ``me`` で current user 名に展開（CLI 側で解決）
    tags: list[str] = field(default_factory=list)  # 1 つでも一致すれば通過（OR）
    types: list[str] = field(default_factory=list)  # plan / bugfix / pending
    states: list[str] = field(default_factory=list)  # state key またはラベル文字列
    review_statuses: list[str] = field(default_factory=list)  # draft / review / published
    project: str | None = None
    q: str | None = None  # 全文（title/summary/body 部分一致・大小無視）UX W2 / P44 MVP

    def is_empty(self) -> bool:
        return not (
            self.owner
            or self.tags
            or self.types
            or self.states
            or self.review_statuses
            or self.project
            or self.q
        )


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _match_state(rec: FileRecord, wanted: list[str], cfg: Config) -> bool:
    """state key または state ラベル（``[実行中]`` / ``実行中`` / ``in-progress``）で一致判定。"""
    if not wanted:
        return True
    rec_key = _norm(rec.state)
    rec_label = _norm(rec.state_label or "")
    rec_label_inner = rec_label.strip("[]")
    for token in wanted:
        t = _norm(token).strip("[]")
        if not t:
            continue
        if t == rec_key:
            return True
        if t == rec_label_inner:
            return True
        # ラベル→state key 解決（ja/en エイリアス込み）
        st = cfg.state_model.match(t)
        if st is not None and st.key == rec.state:
            return True
    return False


def _match_tags(rec: FileRecord, wanted: list[str]) -> bool:
    if not wanted:
        return True
    rec_tags = {_norm(t) for t in (rec.tags or [])}
    return any(_norm(t) in rec_tags for t in wanted)


def _match_owner(rec: FileRecord, wanted: str | None) -> bool:
    if not wanted:
        return True
    return _norm(rec.owner) == _norm(wanted)


def _match_review_status(rec: FileRecord, wanted: list[str]) -> bool:
    if not wanted:
        return True
    rv = _norm(rec.review_status)
    return any(_norm(t) == rv for t in wanted)


def _match_type(rec: FileRecord, wanted: list[str]) -> bool:
    if not wanted:
        return True
    rt = _norm(rec.type)
    return any(_norm(t) == rt for t in wanted)


def _match_project(rec: FileRecord, wanted: str | None) -> bool:
    if not wanted:
        return True
    return rec.project == wanted


def _match_q(rec: FileRecord, q: str | None) -> bool:
    """title / summary / ファイル本文に部分一致（P44 MVP・SQLite FTS 前の簡易版）。"""
    if not q:
        return True
    needle = q.strip().lower()
    if not needle:
        return True
    hay = " ".join(
        filter(None, [rec.title or "", rec.summary or "", Path(rec.path).name])
    ).lower()
    if needle in hay:
        return True
    try:
        body = Path(rec.path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return needle in body.lower()


def find_records(config: Config, filters: FindFilters) -> list[FileRecord]:
    """AND クエリで FileRecord を絞り込む（経過日数の降順 = 古い順で返す）。"""
    from .engine import scan_records

    records = scan_records(config, project=filters.project)
    out: list[FileRecord] = []
    for rec in records:
        if not _match_project(rec, filters.project):
            continue
        if not _match_type(rec, filters.types):
            continue
        if not _match_owner(rec, filters.owner):
            continue
        if not _match_tags(rec, filters.tags):
            continue
        if not _match_review_status(rec, filters.review_statuses):
            continue
        if not _match_state(rec, filters.states, config):
            continue
        if not _match_q(rec, filters.q):
            continue
        out.append(rec)
    out.sort(key=lambda r: r.age_days, reverse=True)
    return out


def resolve_owner_alias(token: str | None, *, cwd: Path | None = None) -> str | None:
    """``--owner me`` を現在ユーザー名に展開する（それ以外はそのまま返す）。"""
    if not token:
        return token
    if token.strip().lower() == "me":
        from .services.frontmatter import current_owner

        return current_owner(cwd=cwd)
    return token
