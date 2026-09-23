"""配布物 pre-commit hook (``templates/.githooks/docsweep-check.py``) のテスト。

hook 単体を ``python <path> <targets>`` の形で起動し、frontmatter 不整合で exit 1、
正常 md で exit 0 になることを確認する。docsweep 本体への import 依存は持たない実装なので、
ここでは subprocess 経由で起動して exit code を見る。

hook の文言と語彙は隣の ``docsweep-check.i18n.json`` にある。表示言語は子プロセスの
環境で決まるので（conftest の日本語固定は子プロセスに効かない）、``DOCSWEEP_LANG`` を
明示して渡す。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import string
import subprocess
import sys
from pathlib import Path


HOOK = (
    Path(__file__).resolve().parents[1]
    / "templates"
    / ".githooks"
    / "docsweep-check.py"
)
I18N = HOOK.with_name("docsweep-check.i18n.json")
_JAPANESE_RE = re.compile(r"[　-ヿ㐀-䶿一-鿿＀-￯]")


def _run(
    args: list[Path], *, lang: str = "ja", hook: Path = HOOK
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DOCSWEEP_LANG"] = lang
    return subprocess.run(
        [sys.executable, str(hook), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )


def test_hook_passes_valid_frontmatter(tmp_path: Path):
    p = tmp_path / "plan_ok.md"
    p.write_text(
        "---\n"
        "type: plan\n"
        "status: planned\n"
        "review_status: draft\n"
        "related: []\n"
        "---\n"
        "# [計画] OK\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 0, r.stderr


def test_hook_passes_when_no_frontmatter(tmp_path: Path):
    """frontmatter が無いファイル（旧来の H1 ラベルのみ）はスキップで OK。"""
    p = tmp_path / "plan_h1only.md"
    p.write_text("# [計画] H1 only\n", encoding="utf-8")
    r = _run([p])
    assert r.returncode == 0


def test_hook_allows_unknown_type(tmp_path: Path):
    p = tmp_path / "plan_bad.md"
    p.write_text(
        "---\ntype: weirdtype\nstatus: planned\n---\n# [計画] bad\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 0, r.stderr


def test_hook_fails_on_invalid_status(tmp_path: Path):
    p = tmp_path / "plan_badstatus.md"
    p.write_text(
        "---\ntype: plan\nstatus: notavalue\n---\n# [計画] x\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 1
    assert "status=" in r.stderr


def test_hook_fails_on_invalid_review_status(tmp_path: Path):
    p = tmp_path / "plan_badreview.md"
    p.write_text(
        "---\ntype: plan\nstatus: planned\nreview_status: weird\n---\n# [計画] x\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 1
    assert "review_status" in r.stderr


def test_hook_fails_on_missing_related(tmp_path: Path):
    p = tmp_path / "plan_relbad.md"
    p.write_text(
        "---\ntype: plan\nstatus: planned\nrelated: [does_not_exist.md]\n---\n"
        "# [計画] x\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 1
    assert "related" in r.stderr


def test_hook_resolves_related_by_filename_after_archive_move(tmp_path: Path):
    """archive へ移った参照先を、ファイル名だけの related で解決できる。

    `related` の正本はファイル名でありパスではない（templates/CLAUDE.md）。
    docsweep は完了した md を archive/ へ移送するのが仕事なので、パスで書いた参照は
    移送のたびに切れる。hook 側に basename 探索が無かったため、規約どおり
    ファイル名で書くと移送後に「存在しない」と言われる食い違いがあった。
    """
    local = tmp_path / "docs" / "local"
    archive = local / "archive" / "v0.5.x"
    archive.mkdir(parents=True)
    (archive / "plan_moved.md").write_text("# [完了] moved\n", encoding="utf-8")

    p = local / "plan_main.md"
    p.write_text(
        "---\ntype: plan\nstatus: planned\nrelated: [plan_moved.md]\n---\n"
        "# [計画] main\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 0, r.stderr


def test_hook_still_fails_when_the_filename_exists_nowhere(tmp_path: Path):
    """basename 探索を足しても、どこにも無い参照は落とす。"""
    local = tmp_path / "docs" / "local"
    (local / "archive").mkdir(parents=True)
    p = local / "plan_main.md"
    p.write_text(
        "---\ntype: plan\nstatus: planned\nrelated: [plan_never_existed.md]\n---\n"
        "# [計画] main\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 1
    assert "related" in r.stderr


def test_hook_passes_with_existing_related(tmp_path: Path):
    other = tmp_path / "plan_other.md"
    other.write_text("# [計画] other\n", encoding="utf-8")
    p = tmp_path / "plan_main.md"
    p.write_text(
        "---\ntype: plan\nstatus: planned\nrelated: [plan_other.md]\n---\n"
        "# [計画] main\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 0, r.stderr


def _delegated_plan(*, c_heading: str = "### C1 実装", completion: str = "観測可能な完了結果。") -> str:
    return (
        "---\n"
        "type: plan\n"
        "status: draft\n"
        "docsweep_state: planned\n"
        "review_status: draft\n"
        "related: []\n"
        "docsweep_delegation: external\n"
        "---\n"
        "# [計画] delegated\n\n"
        "## context配分\n\n"
        "| C | 種別 | 内容 | 備考/注意点 |\n"
        "|---|---|---|---|\n"
        "| C1 | planned | 実装 | — |\n\n"
        "## 概要\n\n概要。\n\n"
        "## C 詳細\n\n"
        f"{c_heading}\n\n"
        "#### 目的\n\n目的を一つに定める。\n\n"
        "#### 調査で確認した現在の実装\n\n現在の実装を確認した。\n\n"
        "#### 作業内容\n\n対象を変更する。\n\n"
        "#### 変更予定ファイル\n\n- `docsweep/example.py`\n\n"
        "#### 維持する仕様\n\n既存の仕様を維持する。\n\n"
        "#### スコープ外\n\n関連しない変更は扱わない。\n\n"
        "#### 検証方法\n\npytest を実行する。\n\n"
        f"#### 完了条件\n\n{completion}\n"
    )


def test_hook_rejects_delegated_plan_without_c_detail(tmp_path: Path):
    p = tmp_path / "plan_missing_detail.md"
    p.write_text(
        "---\ntype: plan\nstatus: draft\ndocsweep_delegation: external\n---\n"
        "# [計画] missing\n",
        encoding="utf-8",
    )

    r = _run([p])

    assert r.returncode == 1
    assert "C 詳細" in r.stderr


def test_hook_rejects_context_detail_mismatch_and_todo(tmp_path: Path):
    p = tmp_path / "plan_bad_delegate.md"
    body = _delegated_plan().replace("| C1 | planned", "| C2 | planned")
    p.write_text(body.replace("#### 検証方法", "#### 維持する仕様"), encoding="utf-8")

    r = _run([p])

    assert r.returncode == 1
    assert "context配分" in r.stderr
    assert "がありません" in r.stderr or "未記入" in r.stderr


def test_hook_accepts_filled_delegated_plan(tmp_path: Path):
    p = tmp_path / "plan_delegate_ok.md"
    p.write_text(_delegated_plan(), encoding="utf-8")

    r = _run([p])

    assert r.returncode == 0, r.stderr


def test_hook_warns_on_ambiguous_completion_without_failing(tmp_path: Path):
    p = tmp_path / "plan_delegate_ambiguous.md"
    p.write_text(_delegated_plan(completion="正しく動く"), encoding="utf-8")

    r = _run([p])

    assert r.returncode == 0
    assert "正しく動く" in r.stderr


def test_hook_warns_on_classification_word_in_h3_without_failing(tmp_path: Path):
    p = tmp_path / "plan_delegate_heading_warning.md"
    p.write_text(_delegated_plan(c_heading="### C1 検証"), encoding="utf-8")

    r = _run([p])

    assert r.returncode == 0
    assert "分類語" in r.stderr


def test_hook_does_not_warn_on_canonical_sections(tmp_path: Path):
    """正規セクションそのものは分類語警告の対象外。

    `## 完了条件` / `## 検証` / `## 受入条件` は closeout-check に分類させるための
    必須節なので、分類語を含むこと自体が目的。ここを除外していないと、
    規約どおりに書いた plan がフックに毎回警告される。
    """
    p = tmp_path / "plan_canonical_sections.md"
    p.write_text(
        _delegated_plan()
        + "\n## 完了条件\n\n- [x] a\n"
        + "\n## 検証\n\n- [x] b\n"
        + "\n## 受入条件\n\n- [x] c\n",
        encoding="utf-8",
    )

    r = _run([p])

    assert r.returncode == 0
    assert "分類語" not in r.stderr


# --- 英語の委譲 plan（見出しはどちらの言語でも受け付ける） ---

_EN_H4_SECTIONS = (
    ("Goal", "Settle on a single goal."),
    ("Current implementation (from investigation)", "The current parser was read."),
    ("Work", "Change the target module."),
    ("Files to change", "- `pkg/example.py`"),
    ("Behavior to keep", "Keep the existing behavior."),
    ("Out of scope", "Unrelated changes are not handled."),
    ("How to verify", "Run pytest."),
    ("Completion criteria", "`pytest -q` exits with code 0."),
)


def _delegated_plan_en(
    *,
    c_heading: str = "### C1 Parser",
    completion: str | None = None,
    drop: tuple[str, ...] = (),
    extra_h4: str = "",
    status: str = "draft",
    c_details: bool = True,
) -> str:
    sections = "".join(
        f"#### {title}\n\n"
        f"{completion if completion is not None and title == 'Completion criteria' else text}\n\n"
        for title, text in _EN_H4_SECTIONS
        if title not in drop
    )
    detail = f"## C details\n\n{c_heading}\n\n{sections}{extra_h4}" if c_details else ""
    return (
        "---\n"
        "type: plan\n"
        f"status: {status}\n"
        "docsweep_state: planned\n"
        "review_status: draft\n"
        "related: []\n"
        "docsweep_delegation: external\n"
        "---\n"
        "# [Planned] delegated\n\n"
        "## Context allocation\n\n"
        "| C | Status | Description | Notes |\n"
        "|---|---|---|---|\n"
        "| C1 | planned | Parser | — |\n\n"
        "## Summary\n\nSummary.\n\n"
        f"{detail}"
    )


def test_hook_accepts_english_delegated_plan(tmp_path: Path):
    p = tmp_path / "plan_delegate_en.md"
    p.write_text(_delegated_plan_en(), encoding="utf-8")

    for lang in ("ja", "en"):
        r = _run([p], lang=lang)
        assert r.returncode == 0, r.stderr
        assert r.stderr == ""


def test_hook_names_missing_heading_in_the_plan_language(tmp_path: Path):
    """見出し名は表示言語ではなく plan の言語で出す（英語の plan に「検証方法」と言わない）。"""
    p = tmp_path / "plan_delegate_en_missing.md"
    p.write_text(_delegated_plan_en(drop=("How to verify",)), encoding="utf-8")

    r = _run([p], lang="ja")

    assert r.returncode == 1
    assert "### C1 に #### How to verify がありません" in r.stderr
    assert "検証方法" not in r.stderr

    no_detail = tmp_path / "plan_delegate_en_no_detail.md"
    no_detail.write_text(_delegated_plan_en(c_details=False), encoding="utf-8")

    r = _run([no_detail], lang="en")

    assert r.returncode == 1
    assert "docsweep_delegation: external requires ## C details" in r.stderr
    assert "C 詳細" not in r.stderr


def test_hook_messages_follow_display_language(tmp_path: Path):
    p = tmp_path / "plan_delegate_en_bad.md"
    p.write_text(
        _delegated_plan_en(
            status="notavalue",
            c_heading="### C1 Verification tweaks",
            completion="Works correctly as expected.",
            drop=("How to verify",),
        ),
        encoding="utf-8",
    )

    en = _run([p], lang="en")

    assert en.returncode == 1
    assert "docsweep-check: warning (delegated plan format)" in en.stderr
    assert "completion criteria contain vague wording: correctly" in en.stderr
    assert "completion criteria contain vague wording: as expected" in en.stderr
    assert "H3 heading contains a classification word: C1 Verification tweaks" in en.stderr
    assert "docsweep-check: frontmatter problems detected" in en.stderr
    assert "status='notavalue' is neither" in en.stderr
    assert "### C1 is missing #### How to verify" in en.stderr
    assert "git commit --no-verify" in en.stderr
    assert not _JAPANESE_RE.search(en.stderr), en.stderr

    ja = _run([p], lang="ja")

    assert ja.returncode == 1
    assert "docsweep-check: warning（委譲 plan の書式）" in ja.stderr
    assert "の完了条件に曖昧表現があります: correctly" in ja.stderr
    assert "H3 見出しに分類語があります: C1 Verification tweaks" in ja.stderr
    assert "docsweep-check: frontmatter 不整合を検出しました" in ja.stderr
    assert "status='notavalue' は OKF lifecycle または旧 docsweep 値ではありません" in ja.stderr
    assert "修正してから再度 git commit してください。" in ja.stderr


def test_hook_reports_same_h4_in_both_languages_as_duplicate(tmp_path: Path):
    mixed = tmp_path / "plan_delegate_mixed.md"
    mixed.write_text(
        _delegated_plan_en(extra_h4="#### 目的\n\n別の目的。\n\n"), encoding="utf-8"
    )

    r = _run([mixed])

    assert r.returncode == 1
    assert "### C1 の #### Goal / #### 目的 が重複しています" in r.stderr

    # 同じ言語の重複は従来どおりの文言
    same = tmp_path / "plan_delegate_same.md"
    same.write_text(
        _delegated_plan().replace("#### 作業内容", "#### 目的\n\n二つ目。\n\n#### 作業内容"),
        encoding="utf-8",
    )

    r = _run([same])

    assert r.returncode == 1
    assert "### C1 の #### 目的 が重複しています" in r.stderr


def test_hook_matches_english_vague_words_as_whole_words(tmp_path: Path):
    ok = tmp_path / "plan_delegate_en_whole_word.md"
    ok.write_text(
        _delegated_plan_en(
            completion="Rejects incorrectly formatted rows and improperly quoted cells."
        ),
        encoding="utf-8",
    )

    r = _run([ok], lang="en")

    assert r.returncode == 0, r.stderr
    assert "vague wording" not in r.stderr

    vague = tmp_path / "plan_delegate_en_vague.md"
    vague.write_text(_delegated_plan_en(completion="It Works PROPERLY."), encoding="utf-8")

    r = _run([vague], lang="en")

    assert r.returncode == 0
    assert "vague wording: properly" in r.stderr


def test_hook_does_not_warn_on_english_canonical_sections(tmp_path: Path):
    p = tmp_path / "plan_delegate_en_canonical.md"
    p.write_text(
        _delegated_plan_en()
        + "\n## Completion criteria\n\n- [x] a\n"
        + "\n## Verification\n\n- [x] b\n"
        + "\n## Acceptance criteria\n\n- [x] c\n"
        + "\n## Overall verification steps\n\n- [x] d\n",
        encoding="utf-8",
    )

    r = _run([p], lang="en")

    assert r.returncode == 0
    assert "classification word" not in r.stderr


# --- 文言辞書（docsweep-check.i18n.json） ---


def _catalog() -> dict[str, dict[str, object]]:
    return json.loads(I18N.read_text(encoding="utf-8"))


def _placeholders(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {field for _text, field, _spec, _conv in string.Formatter().parse(value) if field}


def test_i18n_languages_have_same_keys_and_placeholders():
    catalog = _catalog()
    ja, en = catalog["ja"], catalog["en"]

    assert set(ja) == set(en)
    for key in ja:
        assert type(ja[key]) is type(en[key]), key
        assert _placeholders(ja[key]) == _placeholders(en[key]), key
    for name in (
        "context", "c_details", "goal", "current_impl", "work", "files_to_change",
        "keep", "out_of_scope", "how_to_verify", "completion", "changed_files", "verification",
    ):
        assert f"doc.heading.{name}" in ja


def test_i18n_headings_match_the_package_terms():
    """hook は docsweep を import しないので見出しを自分の JSON に持つ。本体の terms.json とずらさない。"""
    from docsweep.i18n import CATALOGS

    catalog = _catalog()
    for lang, entries in catalog.items():
        headings = {key: value for key, value in entries.items() if key.startswith("doc.heading.")}
        assert headings, lang
        for key, value in headings.items():
            assert CATALOGS[lang][key] == value, (lang, key)


def test_i18n_english_values_contain_no_japanese():
    for key, value in _catalog()["en"].items():
        for item in value if isinstance(value, list) else [value]:
            assert not _JAPANESE_RE.search(item), (key, item)


def test_hook_stops_with_one_line_when_i18n_json_is_missing(tmp_path: Path):
    """配置先（.git/hooks/pre-commit）の隣に辞書が無ければ 1 行出して止まる。"""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    installed = hooks / "pre-commit"
    shutil.copy(HOOK, installed)
    p = tmp_path / "plan_ok.md"
    p.write_text("---\ntype: plan\nstatus: draft\n---\n# [計画] OK\n", encoding="utf-8")

    r = _run([p], hook=installed)

    assert r.returncode == 1
    lines = r.stderr.splitlines()
    assert len(lines) == 1
    assert "docsweep-check.i18n.json" in lines[0]
    assert "install-hooks" in lines[0]

    # install-hooks と同じ並び（hook の隣に辞書）なら動く
    shutil.copy(I18N, hooks / "docsweep-check.i18n.json")

    r = _run([p], hook=installed)

    assert r.returncode == 0, r.stderr
