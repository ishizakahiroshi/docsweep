"""C5: linkcheck — plan の「変更予定ファイル」セクションと実装実態の整合チェック。

plan_*.md の ``## 変更予定ファイル`` セクションを正規表現抽出し、各ファイルについて:
- 実在確認（ファイル / ディレクトリパスとして存在するか）
- plan 作成日以降の変更量（git log の touch 回数）
- commit message での plan 名言及

を集計して、plan の「実装進捗推測」を出す。CLI ``docsweep linkcheck`` の中身。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import Config
from .engine import scan_records
from .models import FileRecord

# plan の「変更予定ファイル」セクションを切り出すマーカー。
_SECTION_RE = re.compile(
    r"^##\s*変更予定ファイル\s*$.*?(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)

# 行単位でファイル名/パスを拾うパターン (バックティック / リスト記号も剥がす)。
_FILE_LINE_RE = re.compile(r"`([^`]+\.(?:py|md|html|js|ts|tsx|css|toml|yaml|yml|json))`")


@dataclass
class FileStatus:
    """1 ファイル分の整合チェック結果。"""

    path: str
    exists: bool
    touches_since_plan: int  # plan 作成日以降の commit touch 回数 (推測)
    mentioned_in_commit: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LinkCheckResult:
    """1 plan 分の整合チェック結果。"""

    plan_path: str
    plan_name: str
    declared_files: list[FileStatus] = field(default_factory=list)
    progress_hint: str = "unknown"  # "implemented" / "partial" / "not_started" / "unknown"

    def to_dict(self) -> dict:
        return {
            "plan_path": self.plan_path,
            "plan_name": self.plan_name,
            "declared_files": [f.to_dict() for f in self.declared_files],
            "progress_hint": self.progress_hint,
        }


def _extract_section(text: str) -> str | None:
    m = _SECTION_RE.search(text)
    if not m:
        return None
    return m.group(0)


def _extract_files_from_section(section: str) -> list[str]:
    """セクション本文からファイル名候補を抽出する。

    1) バックティック囲みの拡張子付き名を最優先（最も誤検出が少ない）
    2) フォールバック: 行頭の `- ` 箇条書きの最初のトークン
    """
    files = list(_FILE_LINE_RE.findall(section))
    if files:
        # dedupe 順序保持
        seen: set[str] = set()
        out: list[str] = []
        for f in files:
            if f not in seen:
                seen.add(f)
                out.append(f)
        return out
    # フォールバック: 箇条書きの先頭トークン
    out: list[str] = []
    for line in section.splitlines():
        m = re.match(r"^\s*[-*]\s*([\w./\\-]+\.\w+)", line)
        if m:
            out.append(m.group(1))
    return out


def _git_log_count(repo: Path, file_path: Path, since_iso: str | None) -> int:
    """git log でファイルの touch 回数を数える。リポジトリ外なら 0。"""
    args = ["git", "-C", str(repo), "log", "--pretty=oneline"]
    if since_iso:
        args.extend(["--since", since_iso])
    args.extend(["--", str(file_path)])
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return sum(1 for line in r.stdout.splitlines() if line.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return 0


def _commits_mention(repo: Path, plan_name: str) -> bool:
    """commit message に plan_name が含まれるかを確認。"""
    args = ["git", "-C", str(repo), "log", "--pretty=format:%s%n%b", "--all"]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return plan_name in r.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def _resolve_repo(record: FileRecord) -> Path:
    """plan の所属プロジェクトルートを返す（git があれば git root 優先）。"""
    candidate = Path(record.project_root) if record.project_root else Path(record.path).parent
    try:
        r = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            return Path(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return candidate


def _plan_created_iso(record: FileRecord) -> str | None:
    """plan の作成日（ISO）。簡易: filename の YYYY-MM-DD パターン、なければ git log 最古、なければ None。"""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", record.path)
    if m:
        return m.group(1)
    return None


def _check_one(record: FileRecord) -> LinkCheckResult:
    plan_path = Path(record.path)
    plan_name = plan_path.name
    try:
        text = plan_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return LinkCheckResult(plan_path=str(plan_path), plan_name=plan_name)

    section = _extract_section(text)
    if not section:
        return LinkCheckResult(
            plan_path=str(plan_path), plan_name=plan_name, progress_hint="no_section",
        )

    raw_files = _extract_files_from_section(section)
    repo = _resolve_repo(record)
    since = _plan_created_iso(record)

    statuses: list[FileStatus] = []
    exists_count = 0
    touched_count = 0
    for raw in raw_files:
        candidate = repo / raw if not Path(raw).is_absolute() else Path(raw)
        exists = candidate.exists()
        if exists:
            exists_count += 1
        touches = _git_log_count(repo, candidate, since)
        if touches > 0:
            touched_count += 1
        statuses.append(FileStatus(
            path=str(candidate),
            exists=exists,
            touches_since_plan=touches,
            mentioned_in_commit=False,  # plan_name 言及は plan 全体で 1 回判定
        ))

    mentioned = _commits_mention(repo, plan_name)
    if mentioned:
        # 1 件でも言及があれば全 status に同じフラグを立てる（plan 全体メタとして使う）
        for s in statuses:
            s.mentioned_in_commit = True

    # 進捗推測:
    # - declared が 0: no_section（上で return 済）
    # - exists + touch 両方が >= 80%: implemented
    # - 半数以上: partial
    # - それ以下: not_started
    total = len(statuses)
    if total == 0:
        hint = "no_files_declared"
    else:
        exists_ratio = exists_count / total
        touch_ratio = touched_count / total
        if exists_ratio >= 0.8 and touch_ratio >= 0.6:
            hint = "implemented"
        elif exists_ratio >= 0.5 or touch_ratio >= 0.3:
            hint = "partial"
        else:
            hint = "not_started"

    return LinkCheckResult(
        plan_path=str(plan_path),
        plan_name=plan_name,
        declared_files=statuses,
        progress_hint=hint,
    )


def linkcheck(config: Config, *, target: str | None = None) -> list[LinkCheckResult]:
    """指定 plan（または全 plan）の linkcheck を実行する。

    Args:
        config: ロード済み Config
        target: 単一 plan の相対パス / basename / 絶対パス。None で全 plan_*.md

    Returns:
        各 plan の ``LinkCheckResult`` のリスト。
    """
    records = scan_records(config)
    plans = [r for r in records if r.type == "plan"]
    if target:
        t = Path(target)
        plans = [
            r for r in plans
            if r.path == str(t) or Path(r.path).name == t.name
        ]
    return [_check_one(r) for r in plans]
