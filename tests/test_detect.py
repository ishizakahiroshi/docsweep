from docsweep.detect import detect_status, extract_summary
from docsweep.states import StateModel

SM = StateModel()


def test_h1_label_detected():
    d = detect_status(text="# [完了] 認証リファクタ\n", filename="plan_auth.md", sm=SM)
    assert d.state_key == "done"
    assert d.state_label == "[完了]"
    assert d.source == "h1"
    assert d.title == "認証リファクタ"


def test_english_label_alias():
    d = detect_status(text="# [Watching] something\n", filename="bugfix_x_2026-01-01.md", sm=SM)
    assert d.state_key == "watching"


def test_frontmatter_wins_over_h1():
    text = "---\nstatus: discarded\n---\n# [計画] タイトル\n"
    d = detect_status(text=text, filename="plan_x.md", sm=SM)
    assert d.state_key == "discarded"
    assert d.source == "frontmatter"
    assert d.conflict is True


def test_unknown_label_is_parse_error():
    d = detect_status(text="# [なにか] タイトル\n", filename="plan_x.md", sm=SM)
    assert d.state_key is None
    assert d.parse_error is True


def test_version_prefixed_label_resolves_to_state():
    """`[v0.1.0 完了]` のようなバージョン情報付きラベルも末尾の `完了` で完了判定する。"""
    d = detect_status(text="# [v0.1.0 完了] PlainSheet 実装計画\n", filename="plan_x.md", sm=SM)
    assert d.state_key == "done"
    assert d.state_label == "[v0.1.0 完了]"  # 表示用は元の H1 トークンを保持
    assert d.parse_error is False


def test_annotation_prefixed_label_resolves_to_state():
    """注釈付きラベル（例 `[draft 計画]`）も末尾の `計画` で計画判定する。"""
    d = detect_status(text="# [draft 計画] 仮タイトル\n", filename="plan_x.md", sm=SM)
    assert d.state_key == "planned"
    assert d.parse_error is False


def test_suffix_match_rejects_unspaced_concat_label():
    """`[計画完了]` のような **空白なしで連結された造語** は誤判定しない。

    末尾の `完了` の直前が `画`（Unicode 上は isalnum() = True に分類される）なので、
    境界条件「直前が非英数字」を満たさず棄却される。ユーザーが意図せず連結造語を
    書いた場合に「完了扱い」する事故を防ぐ。"""
    d = detect_status(text="# [計画完了] x\n", filename="plan_x.md", sm=SM)
    assert d.state_key is None
    assert d.parse_error is True


def test_suffix_match_does_not_swallow_alphanumeric_prefix():
    """末尾境界が英数字接続のときは誤マッチしない（例 `[notdone]` は done にならない）。"""
    d = detect_status(text="# [notdone] x\n", filename="plan_x.md", sm=SM)
    assert d.state_key is None
    assert d.parse_error is True


def test_filename_prefix():
    d = detect_status(text="本文だけ\n", filename="done_plan_x.md", sm=SM)
    assert d.state_key == "done"
    assert d.source == "filename"


def test_extract_summary_skips_quote_meta():
    text = "# [計画] t\n> 最終更新: 2026-01-01\n\n## 概要\n\nこれが概要の一行目。\n二行目。\n\n## 次\n"
    assert extract_summary(text, "概要") == "これが概要の一行目。 二行目。"
