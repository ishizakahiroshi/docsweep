"""C5: auto-triage — md の状態遷移提案を LLM に委譲し、承認済みを一括適用する。

設計:
- ``suggest`` モード: md 本文 + 索引メタ + linkcheck 結果 を LLM に渡して
  「[完了] / 維持 / [廃止] / 不明」と根拠を返してもらう
- ``apply`` モード: 提案を ``engine.apply_action`` で一括実行する

実 LLM は本 plan では呼ばない。``ruleset`` ベースの decider（フラグから推測）と
Mock LLM 経路の両方を用意する。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .config import Config
from .engine import apply_action, run_scan
from .linkcheck import linkcheck
from .models import Action, FileRecord, Flag


@dataclass
class TriageSuggestion:
    """1 ファイル分の遷移提案。"""

    path: str
    project: str
    current_state: str | None
    proposed_action: str  # "discard" / "keep" / "resume" / "relabel" / "promote" / "skip"
    proposed_to: str | None  # relabel 時の宛先ラベル名（例 "[完了]"）
    reason: str
    confidence: float  # 0..1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TriageSuggestResult:
    suggestions: list[TriageSuggestion] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"suggestions": [s.to_dict() for s in self.suggestions]}


def _ruleset_decide(rec: FileRecord, lc_progress: str | None) -> TriageSuggestion | None:
    """ヒューリスティック decider。LLM 不要の最小実装。

    判定ルール:
      - linkcheck progress = "implemented" の plan → "promote" (完了候補)
      - NEEDS_DECISION + age > 180 → "discard" 候補（陳腐化が長すぎる）
      - state=watching + age > 14 → "promote" (release sweep 候補)
      - それ以外 → 提案無し
    """
    flags = set(rec.flags or [])
    age = rec.age_days or 0

    if rec.type == "plan" and lc_progress == "implemented" and rec.state in {"planned", "in-progress"}:
        return TriageSuggestion(
            path=rec.path, project=rec.project, current_state=rec.state,
            proposed_action="relabel",
            proposed_to="[完了]",
            reason="linkcheck で「変更予定ファイル」がほぼ実装済み + commit 言及あり",
            confidence=0.75,
        )

    if Flag.NEEDS_DECISION.value in flags and age > 180:
        return TriageSuggestion(
            path=rec.path, project=rec.project, current_state=rec.state,
            proposed_action="discard",
            proposed_to=None,
            reason=f"陳腐化フラグが立ってから 180 日超 (age={age}d) — 廃止判断を提案",
            confidence=0.6,
        )

    if rec.state == "watching" and age > 14:
        return TriageSuggestion(
            path=rec.path, project=rec.project, current_state=rec.state,
            proposed_action="promote",
            proposed_to=None,
            reason=f"様子見 → 完了 へ昇格候補 (age={age}d, release sweep に該当)",
            confidence=0.5,
        )

    return None


def suggest_transitions(
    config: Config, *, target: str | None = None,
) -> TriageSuggestResult:
    """状態遷移提案を生成する。

    Args:
        config: ロード済み Config
        target: 単一ファイルの相対パス / basename。None で全件

    Returns:
        ``TriageSuggestResult``。
    """
    # linkcheck 結果を map にまとめておく（plan 進捗を判断材料に）
    lc_map: dict[str, str] = {}
    for lc in linkcheck(config):
        lc_map[lc.plan_path] = lc.progress_hint

    result = run_scan(config)
    suggestions: list[TriageSuggestion] = []
    for doc in result.docs:
        rec = doc.record
        if target:
            from pathlib import Path
            t = Path(target)
            if rec.path != str(t) and Path(rec.path).name != t.name:
                continue
        s = _ruleset_decide(rec, lc_map.get(rec.path))
        if s is not None:
            suggestions.append(s)
    return TriageSuggestResult(suggestions=suggestions)


@dataclass
class ApplyResult:
    applied: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def apply_suggestions(
    config: Config,
    decisions: list[dict],
    *,
    dry_run: bool = False,
) -> ApplyResult:
    """承認済み提案を一括適用する。

    Args:
        config: ロード済み Config
        decisions: ``[{path, action, to?}]`` のリスト。CLI / MCP で承認したものを渡す
        dry_run: True で実際の移送/書換は行わず計画だけ返す

    Returns:
        ``ApplyResult``。
    """
    result = run_scan(config)
    by_path = {d.record.path: d for d in result.docs}
    applied: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for d in decisions:
        path = d.get("path")
        action = d.get("action")
        to = d.get("to")
        if not path or not action:
            failed.append({"path": path, "reason": "path/action 欠落"})
            continue
        doc = by_path.get(path)
        if doc is None:
            failed.append({"path": path, "reason": "対象ファイルが見つからない"})
            continue
        if action == "skip":
            skipped.append({"path": path})
            continue
        try:
            entry = apply_action(doc, action, config, to=to, dry_run=dry_run)
            applied.append(entry.to_dict())
        except ValueError as e:
            failed.append({"path": path, "reason": str(e)})

    return ApplyResult(applied=applied, skipped=skipped, failed=failed)
