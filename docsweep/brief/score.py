"""brief / cross 共通のスコア式（C3 + C4 共有モジュール）。

「今日の 1 個」を決めるための単一スコア式を 1 か所に集める。式は ``score_record(rec)`` で
1 つの FileRecord に対して float を返す。式自体は **断定する** ことを優先しており、
同点時はタイブレーカ（``rec.path`` 昇順）でも必ず 1 件が決まる。

設計指針:
- 入力は ``FileRecord`` のみ（索引から復元された FileRecord でもそのまま動く）
- 外部 LLM を呼ばない（決定性・速度のため）
- フィールドの欠落は 0 として扱い、例外を投げない
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..models import FileRecord, Flag

# スコア重み — 「今日の 1 個」の意思決定要素ごとの寄与度。値の妥当性はテストで固定する。
W_URGENCY = 40.0           # due 切れ・要判断（陳腐化）への重み
W_STALE = 1.5              # 経過日数の寄与（1 日 = 1.5 点）
W_REVIEW_STATUS = 5.0      # review_status 別の追加点（review > draft > published）
W_TOUCHED_DECAY = 0.3      # 直近触ったものを下げる（同じファイルに張り付かない誘導）
W_DEP_CHAIN = 8.0          # related の本数（プロジェクト中枢ファイルを浮かせる）

# stale 寄与の上限日数（無制限だと古い凍結ファイルが top_pick を独占してしまう）。
# 30 日 = 最大 45 点。urgency boost (= NEEDS_DECISION 40 点) と同等のオーダーになる。
STALE_DAY_CAP = 30

# state ごとの基礎点。done / discarded は brief の対象外として 0 にする
# （これらは archive 候補なので brief には浮かべない）。
_STATE_BASE: dict[str, float] = {
    "in-progress": 12.0,
    "planned": 10.0,
    "watching": 6.0,
    "pending": 5.0,
    "done": 0.0,
    "discarded": 0.0,
}

_REVIEW_STATUS_WEIGHT: dict[str, float] = {
    "review": 2.0,   # 「レビュー待ち」は催促したい
    "draft": 1.0,
    "published": 0.0,
}


@dataclass(frozen=True)
class ScoreBreakdown:
    """スコア内訳。``brief --explain`` / Web で内訳を見せるときに使う。"""

    total: float
    state_base: float
    urgency: float
    stale: float
    review_status: float
    touched_decay: float
    dep_chain: float

    def to_dict(self) -> dict:
        return {
            "total": round(self.total, 2),
            "state_base": round(self.state_base, 2),
            "urgency": round(self.urgency, 2),
            "stale": round(self.stale, 2),
            "review_status": round(self.review_status, 2),
            "touched_decay": round(self.touched_decay, 2),
            "dep_chain": round(self.dep_chain, 2),
        }


def _urgency_score(rec: FileRecord, *, today: date | None = None) -> float:
    """due 超過 / NEEDS_DECISION (陳腐化) を urgency 軸として加点する。

    NEEDS_DECISION は「陳腐化した未終端 = 即判断が要る」を意味する強いシグナル。
    stale (経過日数の連続値) より優先するため、40 点をつける（W_URGENCY 同等）。
    """
    score = 0.0
    base = today or date.today()
    if rec.due:
        try:
            d = date.fromisoformat(rec.due)
            days_over = (base - d).days
            if days_over > 0:
                # 1 日 1 点、上限 +30 まで（青天井にしない）
                score += min(30.0, float(days_over))
        except ValueError:
            pass
    if Flag.NEEDS_DECISION.value in (rec.flags or []):
        score += 40.0  # 強いシグナル: stale cap 寄与より大きく
    if Flag.OVERDUE_TODO.value in (rec.flags or []):
        score += 20.0
    if Flag.OVERDUE_GRADUATE.value in (rec.flags or []):
        score += 12.0
    return score


def score_record(rec: FileRecord, *, today: date | None = None) -> ScoreBreakdown:
    """FileRecord を入力に ``ScoreBreakdown`` を返す。``total`` が大きいほど「今日の 1 個」候補。

    done / discarded はスコア 0 で固定（brief / cross の対象外）。
    """
    state_base = _STATE_BASE.get(rec.state or "", 0.0)
    if state_base == 0.0 and rec.state in {"done", "discarded"}:
        return ScoreBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    urgency = _urgency_score(rec, today=today) * (W_URGENCY / 40.0)
    # stale は cap 付き（古い凍結ファイルが top_pick を独占しないよう抑える）。
    capped_age = min(float(rec.age_days or 0), float(STALE_DAY_CAP))
    stale = capped_age * W_STALE
    review_status = _REVIEW_STATUS_WEIGHT.get(rec.review_status or "", 0.0) * W_REVIEW_STATUS
    # 触ったばかりのファイルは下げる（同じものに張り付かない誘導）
    touched_decay = max(0.0, 14.0 - float(rec.age_days or 0)) * W_TOUCHED_DECAY
    dep_chain = float(len(rec.related or [])) * W_DEP_CHAIN

    total = state_base + urgency + stale + review_status - touched_decay + dep_chain
    return ScoreBreakdown(
        total=total,
        state_base=state_base,
        urgency=urgency,
        stale=stale,
        review_status=review_status,
        touched_decay=-touched_decay,
        dep_chain=dep_chain,
    )


def tiebreak_key(rec: FileRecord) -> tuple:
    """同点時のタイブレーカ。決定性を確保するため ``path`` 昇順で 1 件に絞る。"""
    return (rec.project or "", Path(rec.path).name, rec.path)
