"""対話 UI・ヒント・doctor・cookbook・capture・MCP の応答が表示言語 en で英語になること。

既定（conftest）は ja 固定なので、既存テストが日本語の文言を見ている。ここでは
``use_lang("en")`` の下で英語が出ること（と、日本語の文言が混ざらないこと）を確かめる。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from docsweep.config import Config, load_config
from docsweep.i18n import use_lang

JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


def _config(root: Path) -> Config:
    return load_config(explicit_roots=[str(root)], global_path=root / "no-such-config.yaml")


# ---- interactive.py（triage --review） --------------------------------------------


def test_interactive_summary_in_english() -> None:
    from docsweep.interactive import DecisionResult, summarize

    results = [
        DecisionResult(path="a", decision="done", action=None, archived=True),
        DecisionResult(path="b", decision="skip", action=None, archived=False, error="boom"),
    ]
    with use_lang("en"):
        assert summarize(results) == "Results: Done 1 / Skipped 1  (1 error(s))"
        assert summarize([]) == "(nothing to process)"


def test_interactive_loop_in_english(tmp_path: Path) -> None:
    from docsweep.interactive import run_interactive_triage

    (tmp_path / "pending_a.md").write_text("# [保留] a\n", encoding="utf-8")
    keys = iter(["?", "s"])
    captured: list[str] = []
    with use_lang("en"):
        rc = run_interactive_triage(
            _config(tmp_path),
            input_func=lambda _prompt: next(keys),
            output_func=captured.append,
            dry_run=True,
        )
    assert rc == 0
    joined = "\n".join(captured)
    assert "Starting interactive triage. Keys: c=done" in joined
    assert "  → Unknown key. Enter one of c/w/x/s/l/o/q." in joined
    assert "Results: Skipped 1" in joined
    assert not JAPANESE.search(joined.replace("[保留]", ""))


def test_interactive_nothing_to_decide_in_english(tmp_path: Path) -> None:
    from docsweep.interactive import run_interactive_triage

    captured: list[str] = []
    with use_lang("en"):
        run_interactive_triage(_config(tmp_path), output_func=captured.append, docs=[])
    assert captured == ["No files need a decision."]


# ---- review.py（--review のチェックリスト） ---------------------------------------------


def test_review_extra_hint_in_english(tmp_path: Path, monkeypatch, capsys) -> None:
    from docsweep.review import run_review

    monkeypatch.setitem(sys.modules, "questionary", None)  # import を ImportError にする
    with use_lang("en"):
        assert run_review(_config(tmp_path)) == 3
    assert capsys.readouterr().out.strip() == (
        "--review requires the review extra: pip install 'docsweep[review]'"
    )


def test_review_nothing_to_decide_in_english(tmp_path: Path, capsys) -> None:
    pytest.importorskip("questionary")
    from docsweep.review import run_review

    with use_lang("en"):
        assert run_review(_config(tmp_path)) == 0
    assert capsys.readouterr().out.strip() == "No files need a decision."


# ---- hints.py ------------------------------------------------------------------


def test_hints_in_english(monkeypatch) -> None:
    from docsweep.hints import suggest_after_command

    monkeypatch.delenv("DOCSWEEP_HINTS", raising=False)
    with use_lang("en"):
        assert suggest_after_command("init") == (
            "hint: next: python -m docsweep doctor && python -m docsweep brief"
        )
        assert suggest_after_command("sweep") == (
            "hint: moved something by mistake? python -m docsweep undo"
        )
        # conftest が DOCSWEEP_INDEX_DB を存在しない tmp パスへ向けている
        assert suggest_after_command("scan") == (
            "hint: no index yet → python -m docsweep index-sync"
        )


# ---- doc_links.py --------------------------------------------------------------


def test_doc_hint_links_to_the_english_readme_in_english() -> None:
    from docsweep.doc_links import DOC_BASE, doc_hint

    with use_lang("en"):
        line = doc_hint("cli.unknown_command")
    assert line is not None
    assert line.startswith("hint: Check the subcommand name")
    # 英語版がある文書は英語版の節へ飛ばす
    assert f"→ {DOC_BASE}README.en.md#usage  (help id: cli.unknown_command)" in line
    with use_lang("ja"):
        ja_line = doc_hint("cli.unknown_command")
    assert ja_line is not None
    assert ja_line.startswith("ヒント: ")
    assert f"→ {DOC_BASE}README.md#使い方  (ヘルプ ID: cli.unknown_command)" in ja_line


# ---- doctor.py -----------------------------------------------------------------


def test_doctor_in_english(tmp_path: Path) -> None:
    from docsweep.doctor import format_human, run_doctor

    missing = tmp_path / "no-such-config.yaml"
    with use_lang("en"):
        report = run_doctor(
            config=Config(roots=[]), global_path=missing, index_db=tmp_path / "missing.db"
        )
        text = format_human(report)
    items = {item.id: item for item in report.items}
    assert items["config"].detail == f"not found: {missing}"
    assert items["roots"].detail == "no scan roots are configured"
    assert items["roots"].fix == "python -m docsweep init  # or edit roots in config.yaml"
    assert items["work_queue"].detail.startswith("Could not tell which project to check")
    assert items["index"].detail.startswith("not created yet: ")
    assert items["mcp_hint"].label == "MCP setup"
    assert all(not JAPANESE.search(item.label) for item in report.items)
    assert all(not JAPANESE.search(item.fix or "") for item in report.items)
    assert "MCP setup" in text


def test_doctor_needs_attention_in_english(tmp_path: Path) -> None:
    from docsweep.doctor import format_human, run_doctor

    config = Config(roots=[tmp_path / "missing-root"])
    with use_lang("en"):
        report = run_doctor(
            config=config, global_path=tmp_path / "none.yaml", index_db=tmp_path / "x.db"
        )
        text = format_human(report)
    assert not report.ok
    assert "!! NEEDS ATTENTION" in text
    roots = next(item for item in report.items if item.id == "roots")
    assert roots.detail.startswith("paths that do not exist: ")


# ---- cookbook.py ---------------------------------------------------------------


def test_cookbook_in_english() -> None:
    from docsweep.cookbook import get_scenario, render_cookbook

    with use_lang("en"):
        everything = render_cookbook()
        ai = render_cookbook("ai")
        unknown = render_cookbook("nope")
        hygiene = get_scenario("hygiene")
    assert not JAPANESE.search(everything)
    assert '$ docsweep intent "what did I do yesterday"\n  # Intent → command' in ai
    assert unknown.startswith("unknown scenario: nope (known: ai, closeout,")
    assert hygiene is not None
    assert {"cmd": 'docsweep find --q "auth"', "why": "Full-text search"} in hygiene


def test_cookbook_in_japanese_is_unchanged() -> None:
    from docsweep.cookbook import get_scenario

    ai = get_scenario("ai")
    assert ai is not None
    assert ai[0] == {"cmd": 'docsweep intent "昨日何やった"', "why": "意図→コマンド"}
    assert get_scenario("nope") is None


# ---- init_cmd.py ---------------------------------------------------------------


def test_init_messages_in_english(tmp_path: Path) -> None:
    from docsweep.init_cmd import run_init

    cfg = tmp_path / "config.yaml"
    root = tmp_path / "work"
    root.mkdir()
    with use_lang("en"):
        created = run_init(yes=True, root=str(root), lang="en", agent="none", global_path=cfg)
        again = run_init(yes=True, root=str(root), lang="en", agent="none", global_path=cfg)
    assert created.message == f"Created the config: {cfg}"
    assert created.next_steps[-1] == "python -m docsweep brief   # or  python -m docsweep serve"
    assert again.message.startswith(f"A config already exists: {cfg} (not overwritten;")


@pytest.mark.parametrize("display", ["en", "ja"])
def test_init_writes_the_display_language_when_lang_is_omitted(tmp_path: Path, display: str) -> None:
    """lang を省いた init は表示言語を書く（固定の ja を書くと英語の利用者が日本語に固まる）。"""
    import yaml

    from docsweep.init_cmd import run_init

    cfg = tmp_path / "config.yaml"
    with use_lang(display):
        run_init(yes=True, root=str(tmp_path), agent="none", global_path=cfg)
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["lang"] == display


def test_cli_init_yes_without_lang_writes_english_for_english_users(tmp_path: Path, monkeypatch) -> None:
    import yaml

    from docsweep.cli import main

    monkeypatch.setenv("DOCSWEEP_LANG", "en")
    cfg = tmp_path / "config.yaml"
    assert main(["init", "--yes", "--root", str(tmp_path), "--config", str(cfg)]) == 0
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["lang"] == "en"


# ---- mcp_server.py -------------------------------------------------------------


def test_mcp_errors_in_english(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from docsweep.mcp_server import build_server

    server = build_server(_config(tmp_path))
    tools = {name: tool.fn for name, tool in server._tool_manager._tools.items()}
    outside = str(tmp_path / "not-a-project")
    with use_lang("en"):
        assert tools["eject"](outside)["error"] == "project outside scan roots"
        assert tools["inject"](outside)["error"] == "project outside scan roots"
    with use_lang("ja"):
        assert tools["eject"](outside)["error"] == "スキャンルートの外の project です"


# ---- presets.py ----------------------------------------------------------------


def test_presets_in_english() -> None:
    from docsweep.presets import PRESETS, get_preset

    with use_lang("en"):
        with pytest.raises(ValueError, match=r"^Unknown preset 'nope' \(available: claude-jp, frontmatter\)$"):
            get_preset("nope")
        # 説明は注入する .docsweep.yaml に書くので、表示言語ではなくプリセットの言語で出る
        assert PRESETS["frontmatter"].description.startswith("General purpose.")
        assert PRESETS["claude-jp"].description.startswith("Claude Code 向け日本語ルール")


# ---- inject/agent_codex.py -----------------------------------------------------


def test_agent_codex_messages_in_english(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from docsweep.inject.agent_codex import _warn_if_shadowed, resolve_global_target

    with use_lang("en"):
        with pytest.raises(ValueError, match=r"^Unknown agent: nope \(use claude / codex"):
            resolve_global_target("nope")
    (tmp_path / "AGENTS.override.md").write_text("x", encoding="utf-8")
    result = SimpleNamespace(warnings=[])
    with use_lang("en"):
        _warn_if_shadowed(tmp_path / "AGENTS.md", result, agent="codex")
    assert result.warnings == [
        "AGENTS.override.md exists in the same directory. Codex reads it instead of AGENTS.md."
        " To make the guidance take effect, merge it into the override file"
        " or pass the override file with --global-target."
    ]


# ---- capture/llm.py・capture/service.py ----------------------------------------


def test_capture_llm_errors_in_english() -> None:
    from docsweep.capture.llm import get_llm

    with use_lang("en"):
        with pytest.raises(NotImplementedError, match=r"^LLM provider 'openai' is not implemented yet"):
            get_llm("openai")
        with pytest.raises(ValueError, match=r"^Unknown LLM provider: nope$"):
            get_llm("nope")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("", "suggested_filename is empty or invalid: ''"),
        ("../evil.md", "suggested_filename must not contain a path: '../evil.md'"),
        ("foo.txt", "suggested_filename may contain only letters, digits, hyphens,"),
    ],
)
def test_capture_filename_errors_in_english(name: str, expected: str) -> None:
    from docsweep.capture.service import CaptureScopeError, _sanitize_filename

    with use_lang("en"):
        with pytest.raises(CaptureScopeError) as info:
            _sanitize_filename(name)
    assert str(info.value).startswith(expected)


def test_capture_filename_error_in_japanese_is_unchanged() -> None:
    from docsweep.capture.service import CaptureScopeError, _sanitize_filename

    with pytest.raises(CaptureScopeError) as info:
        _sanitize_filename("a/b.md")
    assert str(info.value) == "suggested_filename にパスを含めることはできません: 'a/b.md'"


# ---- resurrect/embedding.py ----------------------------------------------------


def test_embedding_unavailable_in_english(monkeypatch) -> None:
    from docsweep.resurrect import embedding

    monkeypatch.setattr(embedding, "_MODEL", None)
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with use_lang("en"):
        with pytest.raises(embedding.EmbeddingUnavailable) as info:
            embedding.get_model()
    assert str(info.value) == (
        "sentence-transformers is not installed: pip install 'docsweep[resurrect]'"
    )


# ---- intent.py -----------------------------------------------------------------


def test_intent_reasons_in_english() -> None:
    from docsweep.intent import route_intent

    with use_lang("en"):
        yesterday = route_intent("what did I do yesterday")
        empty = route_intent("")
        nothing = route_intent("zzz")
    assert (yesterday.command, yesterday.reason) == (
        "activity",
        "See the md files you touched yesterday, by date",
    )
    assert empty.reason == "Empty intent → run doctor to check the environment"
    assert nothing.reason == "No match → defaulting to brief, the morning entry point"


def test_intent_keywords_still_match_japanese_in_english() -> None:
    """キーワードの表はデータ。表示言語が en でも日本語の自然文を読める。"""
    from docsweep.intent import route_intent

    with use_lang("en"):
        route = route_intent("昨日何やった？")
    assert route.command == "activity"
    assert not JAPANESE.search(route.reason)
