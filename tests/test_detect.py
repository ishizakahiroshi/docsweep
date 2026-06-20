from docSweep.detect import detect_status, extract_summary
from docSweep.states import StateModel

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


def test_filename_prefix():
    d = detect_status(text="本文だけ\n", filename="done_plan_x.md", sm=SM)
    assert d.state_key == "done"
    assert d.source == "filename"


def test_extract_summary_skips_quote_meta():
    text = "# [計画] t\n> 最終更新: 2026-01-01\n\n## 概要\n\nこれが概要の一行目。\n二行目。\n\n## 次\n"
    assert extract_summary(text, "概要") == "これが概要の一行目。 二行目。"
