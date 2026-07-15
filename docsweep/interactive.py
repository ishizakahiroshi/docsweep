"""インタラクティブ triage（``docsweep triage --review``）— OKF 採用後の 1 キー判定ループ。

各 item に対して以下のキーを 1 文字読み、終了時に一括処理する。

| key | 意味 | 効果 |
|---|---|---|
| c | 完了 | H1 ラベルを ``[完了]`` に書換 + frontmatter status 更新 + archive へ移送 |
| w | 様子見 | H1 ラベルを ``[様子見]`` に書換 + frontmatter status 更新 |
| x | 廃止 | H1 ラベルを ``[廃止]`` に書換 + frontmatter status 更新 + archive へ移送 |
| s | スキップ | 何もしない |
| l | 後で | 何もしない（スキップと同じだがログに区別を残す） |
| o | md を開く | OS デフォルトアプリで開く（cross-platform） |
| q | 終了 | ループを早期終了 |

プロンプト UI は plan_okf-adoption_2026-06-29.md C1 で「``prompt_toolkit`` 採用が有力」
と書かれているが、初期実装では追加依存を増やさず標準入力 (``input``) ベースで構成する。
これによりキー判定とディスパッチをロジック層で純粋関数として分離でき、テストが書ける。

cross-platform 動作: ``input`` / ``print`` / ``os.startfile`` / ``subprocess.Popen`` のみ使用。
Windows / macOS / Linux で追加 dep なしに動く。
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .config import Config
from .engine import ScanResult, apply_action, archive_doc, run_scan
from .models import Action, Flag
from .scan import ScannedDoc

# キー → 抽象的な判定アクション。CLI/対話 UI 共通の正本。
KEY_DONE = "done"  # c
KEY_WATCHING = "watching"  # w
KEY_DISCARD = "discard"  # x
KEY_SKIP = "skip"  # s
KEY_LATER = "later"  # l
KEY_OPEN = "open"  # o
KEY_QUIT = "quit"  # q

KEY_TO_DECISION: dict[str, str] = {
    "c": KEY_DONE,
    "w": KEY_WATCHING,
    "x": KEY_DISCARD,
    "s": KEY_SKIP,
    "l": KEY_LATER,
    "o": KEY_OPEN,
    "q": KEY_QUIT,
}

# 各判定の人間可読ラベル（ログ要約に使う）。
DECISION_LABELS: dict[str, str] = {
    KEY_DONE: "完了",
    KEY_WATCHING: "様子見",
    KEY_DISCARD: "廃止",
    KEY_SKIP: "スキップ",
    KEY_LATER: "後で",
    KEY_OPEN: "開いた",
    KEY_QUIT: "終了",
}


@dataclass
class DecisionResult:
    """1 件の判定処理結果。標準出力の要約と将来のログ集計に使う。"""

    path: str
    decision: str  # KEY_DONE / KEY_WATCHING / ...
    action: str | None  # 実際に走った apply_action 名（promote / discard / relabel など）
    archived: bool
    error: str | None = None


def parse_key(raw: str) -> str | None:
    """ユーザー入力 1 行を判定キーへ正規化する。空文字 / 未知キーは None。"""
    if not raw:
        return None
    ch = raw.strip().lower()[:1]
    return KEY_TO_DECISION.get(ch)


def candidates_for_review(result: ScanResult) -> list[ScannedDoc]:
    """``--review`` の対象 = 要判断 + 保留 + 自動移送可能（古い順）。

    既存 ``review.run_review`` の ``_candidates`` と同じ集合を流用するが、
    こちらはモジュール public で参照できるように切り出す（テスト容易性のため）。
    """
    out: list[ScannedDoc] = []
    for d in result.docs:
        r = d.record
        if Flag.NEEDS_DECISION.value in r.flags or r.state == "pending" or (r.archivable and r.auto_movable):
            out.append(d)
    out.sort(key=lambda d: d.record.age_days, reverse=True)
    return out


def _update_frontmatter_status(path: Path, new_status: str) -> bool:
    """frontmatter の ``status:`` 行を ``new_status`` に書換える（無ければ何もしない）。

    H1 ラベルの書換は engine.relabel_file が担うので、ここは frontmatter 側だけを面倒見る。
    無事更新したら True、frontmatter が無い / status 行が無い場合は False。
    """
    try:
        text = path.read_text(encoding="utf-8", newline="")
    except (OSError, UnicodeDecodeError):
        return False
    # 先頭 frontmatter ブロックの中の ``status:`` 行だけ置換する。
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return False
    # 終了 ``---`` を探す。
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return False
    changed = False
    for i in range(1, end):
        stripped = lines[i].lstrip()
        if stripped.startswith("status:"):
            indent = lines[i][: len(lines[i]) - len(stripped)]
            lines[i] = f"{indent}status: {new_status}"
            changed = True
            break
    if not changed:
        return False
    path.write_text("\n".join(lines), encoding="utf-8", newline="")
    return True


def _open_in_os(path: Path) -> str | None:
    """OS デフォルトのアプリで md ファイルを開く。失敗時はエラー文字列を返す。"""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])  # noqa: S603,S607
        else:
            subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607
    except OSError as e:
        return f"open failed: {e}"
    return None


def apply_decision(
    doc: ScannedDoc, decision: str, config: Config, *, dry_run: bool = False
) -> DecisionResult:
    """1 件の判定 → 副作用（ラベル書換 / frontmatter / archive）を実行する純関数。

    引数の ``decision`` は ``KEY_DONE`` / ``KEY_WATCHING`` / ``KEY_DISCARD`` / ``KEY_SKIP`` /
    ``KEY_LATER`` / ``KEY_OPEN`` のいずれか（``KEY_QUIT`` はループ側で処理する想定）。
    H1 ラベル書換は ``apply_action`` / ``archive_doc`` が担い、frontmatter status の
    書換だけここで補助する（既存 engine を肥らせない方針）。

    テストでは ``dry_run=True`` で「実際の副作用は走らず、決定だけ確定する」ことを確認する。
    """
    rec = doc.record
    path = Path(rec.path)

    if decision in (KEY_SKIP, KEY_LATER):
        return DecisionResult(path=rec.path, decision=decision, action=None, archived=False)

    if decision == KEY_OPEN:
        err = _open_in_os(path) if not dry_run else None
        return DecisionResult(
            path=rec.path, decision=decision, action=None, archived=False, error=err
        )

    if decision == KEY_DONE:
        # 様子見 → 完了昇格は PROMOTE、その他終端化は手動 relabel + archive。
        if rec.state == "watching" and Action.PROMOTE.value in rec.allowed_actions:
            try:
                if not dry_run:
                    apply_action(doc, Action.PROMOTE.value, config)
                    _update_frontmatter_status(path, "done")
                return DecisionResult(
                    path=rec.path, decision=decision, action=Action.PROMOTE.value, archived=True
                )
            except ValueError as e:
                return DecisionResult(
                    path=rec.path, decision=decision, action=None, archived=False,
                    error=str(e),
                )
        # それ以外は (a) state を done に書換 → archive、または (b) 既に done なら素直に archive。
        try:
            if not dry_run:
                if rec.state != "done":
                    apply_action(doc, Action.RELABEL.value, config, to="done")
                    _update_frontmatter_status(path, "done")
                archive_doc(doc, config)
            return DecisionResult(
                path=rec.path, decision=decision, action="relabel+archive", archived=True
            )
        except ValueError as e:
            return DecisionResult(
                path=rec.path, decision=decision, action=None, archived=False, error=str(e)
            )

    if decision == KEY_DISCARD:
        try:
            if not dry_run:
                if Action.DISCARD.value in rec.allowed_actions:
                    apply_action(doc, Action.DISCARD.value, config)
                else:
                    apply_action(doc, Action.RELABEL.value, config, to="discarded")
                    archive_doc(doc, config)
                _update_frontmatter_status(path, "discarded")
            return DecisionResult(
                path=rec.path, decision=decision, action=Action.DISCARD.value, archived=True
            )
        except ValueError as e:
            return DecisionResult(
                path=rec.path, decision=decision, action=None, archived=False, error=str(e)
            )

    if decision == KEY_WATCHING:
        try:
            if not dry_run:
                apply_action(doc, Action.RELABEL.value, config, to="watching")
                _update_frontmatter_status(path, "watching")
            return DecisionResult(
                path=rec.path, decision=decision, action=Action.RELABEL.value, archived=False
            )
        except ValueError as e:
            return DecisionResult(
                path=rec.path, decision=decision, action=None, archived=False, error=str(e)
            )

    return DecisionResult(
        path=rec.path, decision=decision, action=None, archived=False,
        error=f"未知の決定: {decision}",
    )


def dispatch_decisions(
    pairs: Iterable[tuple[ScannedDoc, str]], config: Config, *, dry_run: bool = False
) -> list[DecisionResult]:
    """(doc, decision) 列を一括処理して結果を返す（テスト用エントリポイント）。"""
    return [apply_decision(d, decision, config, dry_run=dry_run) for d, decision in pairs]


def summarize(results: list[DecisionResult]) -> str:
    """判定結果を「判定別 N 件」「エラー M 件」に要約した 1〜数行文字列を返す。"""
    if not results:
        return "（処理対象なし）"
    counts: dict[str, int] = {}
    errors = 0
    for r in results:
        counts[r.decision] = counts.get(r.decision, 0) + 1
        if r.error:
            errors += 1
    parts = [f"{DECISION_LABELS.get(k, k)} {v}" for k, v in counts.items()]
    head = "判定結果: " + " / ".join(parts)
    if errors:
        head += f"  (エラー {errors} 件)"
    return head


def _format_item(doc: ScannedDoc) -> str:
    r = doc.record
    label = r.state_label or "[?]"
    name = Path(r.path).name
    flags = f" !{','.join(r.flags)}" if r.flags else ""
    title = f" {r.title}" if r.title else ""
    return f"{label} {r.project}/{name} {r.age_days}d{flags}{title}"


def run_interactive_triage(
    config: Config,
    *,
    input_func: Callable[[str], str] | None = None,
    output_func: Callable[[str], None] | None = None,
    docs: list[ScannedDoc] | None = None,
    dry_run: bool = False,
) -> int:
    """``docsweep triage --review`` 本体: 1 件ずつキー判定を読み、終了時に一括処理する。

    ``input_func`` / ``output_func`` をモック注入できるようにしてある（テスト容易性）。
    実運用ではそれぞれ ``input`` / ``print`` が使われる。
    """
    rd = input_func or input
    wr = output_func or print

    if docs is None:
        result = run_scan(config)
        docs = candidates_for_review(result)

    if not docs:
        wr("判断が要るファイルはありません。")
        return 0

    wr(
        "インタラクティブ triage を開始します。"
        " キー: c=完了 / w=様子見 / x=廃止 / s=スキップ / l=後で / o=md を開く / q=終了"
    )

    pairs: list[tuple[ScannedDoc, str]] = []
    open_results: list[DecisionResult] = []
    quit_loop = False
    try:
        for i, d in enumerate(docs, start=1):
            if quit_loop:
                break
            wr(f"\n[{i}/{len(docs)}] {_format_item(d)}")
            # `o` は副作用がその場で必要なので、判定ループの中で即時 dispatch する。
            # 開いた後は同じ item で再度キーを読む（c/w/x/s/l/q のいずれかが来るまで）。
            while True:
                try:
                    raw = rd("  [c/w/x/s/l/o/q]? ")
                except EOFError:
                    wr("（入力ストリーム終了 → q として終了）")
                    raw = "q"
                key = parse_key(raw)
                if key is None:
                    wr("  → 不明なキーです。c/w/x/s/l/o/q のいずれかを入力してください。")
                    continue
                if key == KEY_QUIT:
                    wr("  → 中断しました（ここまでの判定だけ適用します）")
                    quit_loop = True
                    break
                if key == KEY_OPEN:
                    res = apply_decision(d, KEY_OPEN, config, dry_run=dry_run)
                    open_results.append(res)
                    if res.error:
                        wr(f"  → 開けませんでした: {res.error}")
                    else:
                        wr("  → 開きました。続けてキーを入力してください。")
                    continue
                pairs.append((d, key))
                break
    except KeyboardInterrupt:
        # 蓄積した pairs を捨てず、ここまでの判定を一括処理する（docstring の「終了時に
        # 一括処理する」設計と整合させる）。EOFError は q 相当で扱っているが、Ctrl+C は
        # 無捕捉だったため蓄積分が全消失していた。
        wr("\n  → Ctrl+C を検知しました（ここまでの判定だけ適用します）")

    results = dispatch_decisions(pairs, config, dry_run=dry_run)
    wr("")
    wr(summarize(results + open_results))
    for r in results:
        if r.error:
            wr(f"  ! {r.path}: {r.error}")
    return 0
