"""``related:`` の双方向化 + 逆参照解決の共有モジュール。

CLI ``docsweep show`` / ``docsweep context`` / ``docsweep fix-related`` と
Web ``GET /api/cards/detail`` の逆参照集計が同じロジックを使う。
``server/routes/board.py::_backref_map`` で書かれていた索引方法をここに切り出し、
both ends から参照する形にする。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .atomic import update_line
from .config import Config
from .engine import run_scan
from .models import FileRecord
from .services.frontmatter import update_frontmatter_field


def _index_records(records: list[FileRecord]) -> dict[str, str]:
    """``related[]`` の各値が指しうるキー（basename / 絶対 POSIX パス）→ 正本パス。"""
    index: dict[str, str] = {}
    for r in records:
        p = Path(r.path)
        index.setdefault(p.name, r.path)
        index[r.path] = r.path
    return index


def resolve_ref(ref: str, index: dict[str, str]) -> str | None:
    """``related[]`` 内の 1 要素 → 正本パス（解決できなければ None）。"""
    target = index.get(ref) or index.get(Path(ref).name)
    return target


def backref_records(target: FileRecord, records: list[FileRecord]) -> list[FileRecord]:
    """target を ``related:`` に挙げている他レコードを返す。"""
    out: list[FileRecord] = []
    name = Path(target.path).name
    for r in records:
        if r.path == target.path:
            continue
        for ref in r.related:
            if ref == target.path or Path(ref).name == name:
                out.append(r)
                break
    return out


def forward_records(target: FileRecord, records: list[FileRecord]) -> list[FileRecord]:
    """target.related が指している他レコードを返す（解決できなかった ref は無視）。"""
    index = _index_records(records)
    out: list[FileRecord] = []
    seen: set[str] = set()
    for ref in target.related:
        path = resolve_ref(ref, index)
        if not path or path in seen:
            continue
        seen.add(path)
        match = next((r for r in records if r.path == path), None)
        if match:
            out.append(match)
    return out


def backref_counts(records: list[FileRecord]) -> dict[str, int]:
    """各 record の path → 「他から related に挙げられている件数」（板の集計と同じ仕様）。"""
    index = _index_records(records)
    counts: dict[str, int] = {}
    for r in records:
        for ref in r.related:
            target = resolve_ref(ref, index)
            if target:
                counts[target] = counts.get(target, 0) + 1
    return counts


# ------------------------------------------------------------------
# fix-related: 片側参照を双方向に対称化する
# ------------------------------------------------------------------


@dataclass
class RelatedFix:
    """1 ファイルに追加されるべき related 要素。"""

    path: str  # 書き換え対象（B 側）
    additions: list[str] = field(default_factory=list)  # B.related に足す basename たち


@dataclass
class FixRelatedResult:
    """``docsweep fix-related`` の dry-run / apply 結果。"""

    fixes: list[RelatedFix] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)  # 実適用したファイルの path

    def to_dict(self) -> dict:
        return {
            "fixes": [
                {"path": f.path, "additions": list(f.additions)} for f in self.fixes
            ],
            "applied": list(self.applied),
        }


def plan_fix_related(config: Config) -> FixRelatedResult:
    """対称化が必要な片側参照を全 records にわたって洗い出す。

    A.related に B があるが B.related に A が無いケースを集める。書き戻すときは
    basename（``plan_x.md``）で揃える（絶対パスはリポ移植性が悪いため）。
    """
    result = run_scan(config)
    records = list(result.records)
    index = _index_records(records)
    name_by_path: dict[str, str] = {r.path: Path(r.path).name for r in records}
    related_by_path: dict[str, set[str]] = {
        r.path: {Path(ref).name for ref in r.related} for r in records
    }

    fixes_map: dict[str, RelatedFix] = {}
    for a in records:
        a_name = name_by_path[a.path]
        for ref in a.related:
            b_path = resolve_ref(ref, index)
            if not b_path or b_path == a.path:
                continue
            b_refs = related_by_path.get(b_path, set())
            if a_name in b_refs:
                continue
            fix = fixes_map.setdefault(b_path, RelatedFix(path=b_path))
            if a_name not in fix.additions:
                fix.additions.append(a_name)
    fix_list = sorted(fixes_map.values(), key=lambda f: f.path)
    return FixRelatedResult(fixes=fix_list)


def apply_fix_related(config: Config) -> FixRelatedResult:
    """``plan_fix_related`` の結果を frontmatter に書き戻す。"""
    plan = plan_fix_related(config)
    result = run_scan(config)
    records_by_path = {r.path: r for r in result.records}
    for fix in plan.fixes:
        rec = records_by_path.get(fix.path)
        if rec is None:
            continue
        # 既存 related + additions（順序を保ったまま重複除去）。
        merged: list[str] = []
        seen: set[str] = set()
        for ref in list(rec.related) + list(fix.additions):
            key = Path(ref).name
            if key in seen:
                continue
            seen.add(key)
            merged.append(ref if ref else key)
        path = Path(fix.path)
        text = path.read_text(encoding="utf-8", newline="")
        from .detect import _FRONTMATTER_RE

        if not _FRONTMATTER_RE.match(text):
            # frontmatter が無い md には書けない（migrate-frontmatter を先に走らせる前提）。
            continue
        try:
            update_frontmatter_field(path, "related", merged)
            plan.applied.append(fix.path)
        except Exception:  # noqa: BLE001 - 個別ファイル失敗で全体を止めない
            continue
    return plan


# 既存呼び出し（board.py の _backref_map）が import している型の互換維持用に、
# 同名関数 ``backref_map`` でも公開する（旧名 → 新名のリダイレクト）。
def backref_map(records: list[FileRecord]) -> dict[str, int]:
    return backref_counts(records)
