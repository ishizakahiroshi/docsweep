"""一括破壊操作の 2 段階確認（UX W4 / P59）。

`promote` の release sweep や Web の一括 archive / 一括ラベル変更は、1 クリック・
1 コマンドで数十件を動かせる。件数がしきい値以上のときだけ「打ち込ませる」段を挟む。

- Web: ``confirm`` フォーム値が要求フレーズと一致しなければ 409 を返す。
- CLI: 非対話（AI 委譲・cron）でプロンプトを出さないのが本プロジェクトの不変条件なので、
  しきい値以上のときは ``--yes`` を必須にする（TTY でも同じ。挙動を環境で変えない）。

しきい値未満は従来どおり 1 段階のまま。``bulk_confirm_threshold: 0`` で常時要求にできる。
"""

from __future__ import annotations

from dataclasses import dataclass

# 操作 ID → 打ち込ませるフレーズ。操作ごとに変えて「惰性の Enter」を効かなくする。
PHRASES: dict[str, str] = {
    "promote": "PROMOTE",
    "bulk_archive": "ARCHIVE",
    "bulk_status": "RELABEL",
}


class BulkConfirmRequired(Exception):
    """2 段階確認が必要なのに与えられていないときに送出する。"""

    def __init__(self, operation: str, count: int, threshold: int, phrase: str) -> None:
        self.operation = operation
        self.count = count
        self.threshold = threshold
        self.phrase = phrase
        super().__init__(
            f"{count} 件は一括確認のしきい値 {threshold} 件以上です。"
            f"確認のため {phrase!r} を入力してください。"
        )

    def to_dict(self) -> dict:
        return {
            "error": "confirm_required",
            "operation": self.operation,
            "count": self.count,
            "threshold": self.threshold,
            "phrase": self.phrase,
            "message": str(self),
        }


@dataclass(frozen=True)
class ConfirmDecision:
    required: bool
    phrase: str
    count: int
    threshold: int


def phrase_for(operation: str) -> str:
    return PHRASES.get(operation, "CONFIRM")


def evaluate(operation: str, count: int, threshold: int) -> ConfirmDecision:
    """件数としきい値から「2 段階確認が要るか」を決める純粋関数。"""
    required = count > 0 and count >= threshold
    return ConfirmDecision(
        required=required,
        phrase=phrase_for(operation),
        count=count,
        threshold=threshold,
    )


def require(operation: str, count: int, threshold: int, supplied: str | None) -> None:
    """必要なら ``BulkConfirmRequired`` を送出する。満たしていれば黙って返る。"""
    decision = evaluate(operation, count, threshold)
    if not decision.required:
        return
    if (supplied or "").strip() == decision.phrase:
        return
    raise BulkConfirmRequired(operation, decision.count, decision.threshold, decision.phrase)
