"""``triage --review``（対話 triage）と ``review`` のチェックリストが表示言語 en で英語だけになること。

操作キー（完了・様子見・廃止・スキップ・後で・開く・終了・未知のキー）と、入力の終わり・Ctrl+C・
一括処理のエラーまで通して、画面に出る文言に日本語が混ざらないことを確かめる。文書は英語の
合成データにして、日本語が出たら docsweep の文言から来たと言えるようにする。

実物の端末（TTY）での描画はここでは見ない。入出力の差し込みと、標準入力を差し替えた CLI で見る。
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
import types
from pathlib import Path

import pytest

from docsweep import interactive
from docsweep.cli import main
from docsweep.config import Config, load_config
from docsweep.i18n import use_lang

JAPANESE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
PROMPT = "  [c/w/x/s/l/o/q]? "

# (ファイル名, 本文, 経過日数)。古い順に並ぶので、キー列はこの順に当たる。
DOCS = [
    ("plan_alpha.md",
     "---\ntype: plan\nstatus: draft\ndocsweep_state: planned\n---\n# [Planned] Alpha rollout\n", 200),
    ("plan_bravo.md", "# [Planned] Bravo cleanup\n", 190),
    ("pending_charlie.md",
     "---\ntype: pending\ndocsweep_policy: never_archive\n---\n# [Pending] Charlie question\n", 180),
    ("pending_delta.md", "# [Pending] Delta idea\n", 170),
    ("bugfix_echo.md", "# [Done] Echo crash fix\n", 160),
    ("pending_golf.md", "# [Pending] Golf note\n", 150),
    ("pending_hotel.md", "# [Pending] Hotel note\n", 140),
]


def _workspace(tmp_path: Path, docs=DOCS) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    now = time.time()
    for name, body, age in docs:
        path = root / name
        path.write_text(body, encoding="utf-8")
        stamp = now - age * 86400
        os.utime(path, (stamp, stamp))
    return root


def _config(root: Path) -> Config:
    return load_config(
        project_dir=root, explicit_roots=[str(root)], global_path=root / "no-such-config.yaml"
    )


def _strip_paths(text: str, *paths: Path) -> str:
    """環境依存のパス（利用者名等で日本語を含みうる）を比較から外す。"""
    for path in paths:
        for form in {str(path), path.as_posix()}:
            text = text.replace(form, "<path>")
    return text


def test_every_key_in_english_has_no_japanese(tmp_path: Path, monkeypatch) -> None:
    root = _workspace(tmp_path)
    opened: list[str] = []

    def fake_startfile(target: str) -> None:
        opened.append(target)
        if len(opened) == 2:
            raise OSError(2, "synthetic failure")

    # 本物のアプリを起動しない。どの OS でも os.startfile の分岐を通す。
    monkeypatch.setattr(interactive, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(interactive.os, "startfile", fake_startfile, raising=False)

    keys = iter([
        "z", "", "o", "o", "c",  # 1 件目: 未知のキー・空の入力・開く（成功）・開く（失敗）・完了
        "w",  # 様子見
        "x",  # 廃止（never_archive なので一括処理でエラーになる）
        "s",  # スキップ
        "l",  # 後で
        "q",  # 終了（7 件目は出ない）
    ])
    prompts: list[str] = []
    captured: list[str] = []

    def read(prompt: str) -> str:
        prompts.append(prompt)
        return next(keys)

    with use_lang("en"):
        rc = interactive.run_interactive_triage(
            _config(root), input_func=read, output_func=captured.append
        )

    assert rc == 0
    assert set(prompts) == {PROMPT}
    joined = _strip_paths("\n".join(captured), root)
    assert captured[0] == (
        "Starting interactive triage. Keys: c=done / w=watching / x=discard / s=skip / "
        "l=later / o=open the md / q=quit"
    )
    # frontmatter から決めたラベルは docsweep が表示言語で作る
    assert "[1/7] [Planned] ws/plan_alpha.md 200d" in joined
    assert joined.count("  → Unknown key. Enter one of c/w/x/s/l/o/q.") == 2
    assert "  → Opened. Enter a key to continue." in joined
    # 失敗の文は 1 回だけ包む（以前は "Could not open: open failed: ..." と二重だった）
    assert "  → Could not open: [Errno 2] synthetic failure" in joined
    assert "open failed" not in joined
    assert "  → Stopped (applying only the decisions made so far)" in joined
    assert "[7/7]" not in joined
    assert (
        "Results: Done 1 / Watching 1 / Discarded 1 / Skipped 1 / Later 1 / Opened 2"
        "  (2 error(s))"
    ) in joined
    assert "Cannot archive because docsweep_policy is never_archive" in joined
    assert not JAPANESE.search(joined), joined


def test_ctrl_c_in_english(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    answers = iter(["s", None])
    captured: list[str] = []

    def read(_prompt: str) -> str:
        answer = next(answers)
        if answer is None:
            raise KeyboardInterrupt
        return answer

    with use_lang("en"):
        interactive.run_interactive_triage(
            _config(root), input_func=read, output_func=captured.append
        )

    joined = _strip_paths("\n".join(captured), root)
    assert "  → Ctrl+C detected (applying only the decisions made so far)" in joined
    assert "Results: Skipped 1" in joined
    assert not JAPANESE.search(joined), joined


@pytest.mark.parametrize(
    ("stdin", "expected"),
    [
        # 非対話（標準入力が空）: 入力の終わりを q として扱い、何も適用せずに終わる
        ("", ["(end of input → quitting as if q was pressed)", "(nothing to process)"]),
        # 標準入力からキーを流す（ファイルを動かさないキーだけ）
        ("z\n\ns\nl\nq\n", ["  → Unknown key. Enter one of c/w/x/s/l/o/q.", "Results: Skipped 1 / Later 1"]),
    ],
)
def test_cli_triage_review_in_english(
    tmp_path: Path, monkeypatch, capsys, stdin: str, expected: list[str]
) -> None:
    root = _workspace(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))

    rc = main([
        "triage", "--review", "--root", str(root), "--project-dir", str(root), "--lang", "en",
    ])

    captured = capsys.readouterr()
    text = _strip_paths(captured.out + captured.err, root)
    assert rc == 0
    assert text.startswith("Starting interactive triage. Keys: c=done")
    assert PROMPT in text
    assert "  → Stopped (applying only the decisions made so far)" in text
    for line in expected:
        assert line in text
    # 入力の終わりの案内はプロンプトの行に続けず、次の行に出す
    assert PROMPT + "(" not in text
    assert not JAPANESE.search(text), text
    # ファイルは動いていない
    assert sorted(p.name for p in root.rglob("*.md")) == sorted(name for name, _, _ in DOCS)


@pytest.mark.parametrize(
    ("lang", "cannot_open", "eof"),
    [
        ("en", "Could not open: {error}", "(end of input → quitting as if q was pressed)"),
        ("ja", "開けませんでした: {error}", "（入力ストリーム終了 → q として終了）"),
    ],
)
def test_open_failure_and_end_of_input_lines(
    tmp_path: Path, monkeypatch, lang: str, cannot_open: str, eof: str
) -> None:
    root = _workspace(tmp_path, DOCS[:1])

    def fake_startfile(_target: str) -> None:
        raise OSError(2, "synthetic failure")

    monkeypatch.setattr(interactive, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(interactive.os, "startfile", fake_startfile, raising=False)
    answers = iter(["o"])
    captured: list[str] = []

    def read(_prompt: str) -> str:
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    with use_lang(lang):
        interactive.run_interactive_triage(
            _config(root), input_func=read, output_func=captured.append
        )

    # OS のエラーを 1 回だけ包む
    assert "  → " + cannot_open.format(error="[Errno 2] synthetic failure") in captured
    # 入力の終わりは、プロンプトの行に続けず改行してから字下げして出す
    assert "\n  " + eof in captured


def test_review_checklist_in_english(tmp_path: Path, monkeypatch, capsys) -> None:
    from docsweep.review import run_review

    # 移送禁止の文書（pending_charlie）も選ぶ。移せなかった行も英語で出ること
    docs = DOCS
    seen: list[str] = []
    pick_all = {"value": False}

    class Choice:
        def __init__(self, title: str, value: object) -> None:
            self.title, self.value = title, value

    def checkbox(message: str, choices: list[Choice], instruction: str | None = None, **_kwargs):
        seen.append(message)
        # 渡さないと questionary が自前の英語の操作案内を出す
        assert instruction is not None
        seen.append(instruction)
        seen.extend(choice.title for choice in choices)
        picked = [choice.value for choice in choices] if pick_all["value"] else []
        return types.SimpleNamespace(ask=lambda: picked)

    monkeypatch.setitem(
        sys.modules, "questionary", types.SimpleNamespace(Choice=Choice, checkbox=checkbox)
    )

    root = _workspace(tmp_path, docs)
    with use_lang("en"):
        assert run_review(_config(root)) == 0
        pick_all["value"] = True
        assert run_review(_config(root)) == 0

    out = capsys.readouterr().out
    shown = _strip_paths("\n".join([*seen, out]), root)
    assert seen[0] == "Select files to move to archive (space to select, enter to confirm)"
    assert seen[1] == "(arrow keys to move, <a> to select or clear all, <i> to invert)"
    assert "Nothing selected. Cancelled." in out
    assert f"Moved {len(docs) - 1} file(s) to archive. 1 file(s) could not be moved:" in out
    assert "Cannot archive because docsweep_policy is never_archive" in out
    assert not JAPANESE.search(shown), shown
