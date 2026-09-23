"""文書・メタデータまわり（closeout・okf・provenance・services ほか）の文言が表示言語で切り替わる。

conftest は表示言語を ja に固定する。ここでは ``use_lang("en")`` の中で呼んで、英語の
利用者に日本語が出ないことを 1 ファイル 1 件以上確かめる。
"""

from __future__ import annotations

import io
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from docsweep.config import Config, load_config
from docsweep.i18n import t, use_lang

JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


def _cfg(root: Path):
    return load_config(explicit_roots=[str(root)], global_path=root / "no-global.yaml")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


# ---- closeout.py ----------------------------------------------------------------


def test_closeout_input_error_is_english() -> None:
    from docsweep.closeout import CloseoutInputError, check_closeout

    with use_lang("en"), pytest.raises(CloseoutInputError, match="--to must be watching or done"):
        check_closeout("plan_x.md", target_state="planned")


def test_closeout_legacy_status_conflict_blocks_in_english(tmp_path: Path) -> None:
    """detect の警告文が英語でも、docsweep_state と旧 status の食い違いは blocker になる。"""
    from docsweep.closeout import check_closeout

    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    parent = _write(
        project / "docs" / "local" / "plan_parent.md",
        "---\ntype: plan\nstatus: in-progress\ndocsweep_state: watching\n---\n"
        "# [様子見] parent\n\n## 完了条件\n\n- [x] done\n\n## 検証\n\n- [x] pytest -q passed\n",
    )

    with use_lang("en"):
        result = check_closeout(parent, project_dir=project, config=_cfg(project))

    conflict = [item for item in result.blockers if item["code"] == "state_conflict"]
    assert [item["message"] for item in conflict] == [
        "The work state in the H1 and in the frontmatter do not match"
    ]


# ---- detect.py ------------------------------------------------------------------


def test_detect_warnings_are_english() -> None:
    from docsweep.detect import detect_status
    from docsweep.states import StateModel

    text = "---\ntype: plan\ndocsweep_state: bogus\n---\n# [計画] x\n"
    with use_lang("en"):
        detection = detect_status(text=text, filename="plan_x.md", sm=StateModel())

    assert detection.frontmatter_warnings == [
        "docsweep_state='bogus' does not resolve to a docsweep state"
    ]


# ---- okf.py / okf_check.py / export.py -----------------------------------------


def test_okf_profile_error_is_english() -> None:
    from docsweep.okf import OkfProfileError, load_okf_profile

    with use_lang("en"), pytest.raises(OkfProfileError, match="must be 64 hexadecimal digits"):
        load_okf_profile(sha256="not-a-digest")


def test_okf_check_issue_is_english(tmp_path: Path) -> None:
    from docsweep.okf import bundled_okf_profile
    from docsweep.okf_check import check_bundle

    bundle = tmp_path / "bundle"
    _write(bundle / "concept.md", "# concept\n")

    with use_lang("en"):
        result = check_bundle(bundle, bundled_okf_profile())

    assert [issue.message for issue in result.errors] == [
        "Markdown that is not a reserved file needs YAML frontmatter"
    ]


def test_export_normalize_error_is_english() -> None:
    from docsweep.export import _normalize_export_text
    from docsweep.okf import bundled_okf_profile

    with use_lang("en"), pytest.raises(ValueError, match="Cannot parse the frontmatter YAML"):
        _normalize_export_text(
            "---\nkey: [unclosed\n---\nbody\n",
            doc_type="plan",
            state=None,
            profile=bundled_okf_profile(),
        )


# ---- provenance.py / provenance_hint.py ------------------------------------------


def test_provenance_errors_are_english(tmp_path: Path) -> None:
    from docsweep.provenance import ProvenanceError, check_document, finish_execution

    with use_lang("en"):
        with pytest.raises(ProvenanceError, match="AI provenance is disabled"):
            finish_execution("AIX-1", config=Config(roots=[tmp_path]), result="completed")
        delegated = check_document(
            tmp_path / "plan_x.md",
            project_dir=tmp_path,
            config=Config(roots=[tmp_path], provenance_manager="repo"),
        )

    assert delegated["warnings"] == ["Delegate to the repository's own validator"]


def test_provenance_context_table_error_names_every_accepted_heading(tmp_path: Path) -> None:
    """見出し名は文書の中身なので、受け付ける全言語の表記を示す（表示言語では訳さない）。"""
    from docsweep.provenance import ProvenanceError, _append_context_execution

    with use_lang("en"), pytest.raises(ProvenanceError) as excinfo:
        _append_context_execution("# x\n", ["C1"], "AIX-1")

    message = str(excinfo.value)
    assert message.startswith("The Markdown needs a ")
    assert "## context配分" in message
    assert "## Context allocation" in message


def test_provenance_hint_is_english() -> None:
    from docsweep.provenance import AIMetadata
    from docsweep.provenance_hint import warn_if_unresolved

    out = io.StringIO()
    with use_lang("en"):
        shown = warn_if_unresolved(
            AIMetadata(),
            config=SimpleNamespace(provenance_enabled=True),
            command="other",
            stream=out,
        )

    assert shown is True
    text = out.getvalue()
    assert text.startswith("warning: The authoring AI will be recorded as unknown (other).")
    assert "--agent, etc." in text
    assert not JAPANESE.search(text)


# ---- services -------------------------------------------------------------------


def test_services_archive_undo_error_is_english(tmp_path: Path) -> None:
    from docsweep.services.archive import archive_done, undo_last_batch

    root = tmp_path / "dev"
    src = _write(root / "proj" / "plan_done.md", "# [完了] x\n\n## 概要\n\na\n")
    cfg = _cfg(root)
    archive_done(config=cfg, paths=[str(src)])
    src.write_text("# new\n", encoding="utf-8")

    with use_lang("en"):
        result = undo_last_batch(config=cfg)

    assert [item["error"] for item in result.failed] == [
        "A file already exists at the restore destination"
    ]


def test_services_archive_undo_errors_keep_the_japanese_wording() -> None:
    """tests/test_undo.py などが照合する日本語は一字一句そのまま。"""
    assert t("services_archive.undo_source_missing", lang="ja") == "archive 先ファイルが見つかりません"
    assert t("services_archive.undo_destination_exists", lang="ja") == "復元先に既にファイルがあります"


def test_services_content_errors_are_english(tmp_path: Path) -> None:
    from docsweep.services.content import ContentValidationError, update_content

    with use_lang("en"), pytest.raises(ContentValidationError, match="new_content is empty"):
        update_content(tmp_path / "plan_x.md", "")


def test_services_due_error_is_english() -> None:
    from docsweep.services.due import DueParseError, resolve_due

    with use_lang("en"), pytest.raises(DueParseError, match="Cannot parse new_due: 'someday'"):
        resolve_due("someday")


def test_services_frontmatter_errors_are_english(tmp_path: Path) -> None:
    from docsweep.services.frontmatter import (
        FrontmatterValidationError,
        _validate_list_item,
        update_frontmatter_field,
    )

    with use_lang("en"):
        with pytest.raises(FrontmatterValidationError, match="Field name not allowed: 'bogus'"):
            update_frontmatter_field(tmp_path / "plan_x.md", "bogus", "x")
        with pytest.raises(FrontmatterValidationError, match="A list item cannot be empty"):
            _validate_list_item(" ")


def test_services_status_error_is_english() -> None:
    from docsweep.services.status import StatusValidationError, validate_state_transition

    with use_lang("en"), pytest.raises(StatusValidationError) as excinfo:
        validate_state_transition("pending", "done")

    assert str(excinfo.value) == (
        "State 'done' is not allowed for a pending file (allowed: ['discarded', 'pending', 'planned'])"
    )


def test_secret_warning_follows_the_display_language() -> None:
    from docsweep.secrets_guard import format_warnings

    hits = [{"kind": "token", "confidence": "low"}]
    with use_lang("en"):
        assert format_warnings(hits) == ["possible secret detected (token, low confidence)"]
    with use_lang("ja"):
        assert format_warnings(hits) == ["秘密情報らしき記述があります（種別: token・確度: low）"]


# ---- context.py / related.py / fix_conflict.py ----------------------------------


def test_context_render_is_english(tmp_path: Path) -> None:
    from docsweep.context import ContextBundle, render_context

    doc = _write(tmp_path / "plan_x.md", "---\ntype: plan\n---\n# [計画] x\n\nbody text\n")
    target = SimpleNamespace(path=str(doc), title="x", state_label="[計画]")
    bundle = ContextBundle(target=target, parent=None, related_recs=[], backrefs=[target])  # type: ignore[arg-type]

    with use_lang("en"):
        text = render_context(bundle, fmt="markdown")
        with pytest.raises(ValueError, match="Unknown format: xml"):
            render_context(bundle, fmt="xml")

    assert text.splitlines()[:4] == [
        "# Target: plan_x.md",
        f"Path: {doc}",
        "Title: x",
        "State: [計画]",
    ]
    assert "## Body\n\n# [計画] x" in text
    assert "## Backlinks (files that list this file in related)" in text


def test_related_fix_failure_is_english(tmp_path: Path) -> None:
    from docsweep.related import apply_fix_related

    root = tmp_path / "root"
    _write(root / "proj" / "plan_a.md", "---\ntype: plan\nrelated: [plan_b.md]\n---\n# [計画] a\n")
    target = _write(root / "proj" / "plan_b.md", "# [計画] b\n")

    with use_lang("en"):
        result = apply_fix_related(_cfg(root))

    assert result.failed == [{"path": target.resolve().as_posix(), "error": "No frontmatter"}]


def test_fix_conflict_warning_is_english(tmp_path: Path, capsys) -> None:
    from docsweep.fix_conflict import fix_conflicts

    root = tmp_path / "root"
    _write(root / "proj" / "plan_a.md", "# [計画] a\n")

    with use_lang("en"):
        fix_conflicts(_cfg(root), prefer="frontmatter", paths=[str(root / "proj" / "plan_nope.md")])

    err = capsys.readouterr().err
    assert "warning: 1 --path value(s) did not match the conflict list" in err
    assert not JAPANESE.search(err)


# ---- notify.py / bulk_confirm.py / migrate.py / auto_triage.py ------------------


def test_notify_message_is_english(tmp_path: Path) -> None:
    from docsweep.notify import build_overdue_message

    with use_lang("en"):
        assert build_overdue_message(_cfg(tmp_path)) == ("docsweep", "No overdue items")


def test_bulk_confirm_message_is_english() -> None:
    from docsweep.bulk_confirm import BulkConfirmRequired

    with use_lang("en"):
        error = BulkConfirmRequired("promote", 30, 20, "PROMOTE")

    assert error.to_dict()["message"] == (
        "30 items meet the bulk confirmation threshold of 20. Enter 'PROMOTE' to confirm."
    )


def test_migrate_skip_reason_is_english(tmp_path: Path) -> None:
    from docsweep.migrate import plan_migration

    root = tmp_path / "root"
    _write(root / "proj" / "plan_a.md", "---\ntype: [unclosed\n---\n# [計画] a\n")

    with use_lang("en"):
        result = plan_migration(_cfg(root))

    assert [plan.skipped_reason for plan in result.skipped] == [
        "Cannot parse the frontmatter (invalid YAML)"
    ]


def test_auto_triage_reasons_are_english() -> None:
    from docsweep.auto_triage import _ruleset_decide

    rec = SimpleNamespace(
        path="plan_x.md", project="p", type="plan", state="planned",
        flags=[], age_days=3, due=None,
    )
    with use_lang("en"):
        suggestion = _ruleset_decide(rec, "implemented")  # type: ignore[arg-type]

    assert suggestion is not None
    assert suggestion.reason == (
        "linkcheck shows the planned files are mostly implemented and mentioned in commits"
    )
    # 提案は画面に出すので表示言語のラベル（relabel はどちらの言語のラベルも受け付ける）
    assert suggestion.proposed_to == "[Done]"


# ---- aggregate_index.py / reports.py / timeline.py / activity.py ----------------


def test_aggregate_index_markdown_is_english() -> None:
    from docsweep.aggregate_index import IndexData, render_markdown

    counts = {
        "projects": 1, "total": 2, "needs_decision": 0, "needs_fix": 0,
        "pending": 0, "archivable": 1,
    }
    idx = IndexData(
        roots=[], counts=counts, by_state={}, pending=[], needs_decision=[], needs_fix=[],
    )
    with use_lang("en"):
        text = render_markdown(idx, None)

    assert "> Across projects: 1 project(s) / 2 file(s) = " in text
    assert "## By status" in text
    assert not JAPANESE.search(text)


def test_report_is_english(tmp_path: Path) -> None:
    from docsweep.reports import render_report

    with use_lang("en"):
        text = render_report(_cfg(tmp_path))

    assert text.startswith("docsweep report\n")
    assert "Projects: 0   Total files: 0" in text
    assert not JAPANESE.search(text)


def test_timeline_is_english() -> None:
    from docsweep.timeline import TimelineResult, render_timeline

    with use_lang("en"):
        assert render_timeline(TimelineResult(topic="x"), fmt="plain") == "timeline: x\n(no matching files)"
        with pytest.raises(ValueError, match="Unknown format: xml"):
            render_timeline(TimelineResult(topic="x"), fmt="xml")


def test_activity_date_error_is_english() -> None:
    from docsweep.activity import ActivityDateError, _resolve_date_token

    with use_lang("en"), pytest.raises(ActivityDateError, match="Cannot parse --date: 'someday'"):
        _resolve_date_token("someday", today=date(2026, 9, 24))
    assert _resolve_date_token("Today", today=date(2026, 9, 24)) == date(2026, 9, 24)


# ---- completion.py / secrets_guard.py / security/path.py ------------------------


def test_completion_error_is_english(tmp_path: Path) -> None:
    from docsweep.completion import render_completion

    with use_lang("en"), pytest.raises(ValueError, match="Unsupported shell: 'fish'"):
        render_completion("fish", Config(roots=[tmp_path]))


def test_secrets_guard_error_is_english() -> None:
    from docsweep.secrets_guard import SensitiveContentError, enforce_secret_policy

    content = "token ghp_" + ("a" * 36)
    with use_lang("en"), pytest.raises(SensitiveContentError) as excinfo:
        enforce_secret_policy(content, policy="block")

    assert str(excinfo.value).startswith("Cannot save content that looks like it contains secrets")
    assert not JAPANESE.search(str(excinfo.value))


def test_security_path_errors_are_english(tmp_path: Path) -> None:
    from docsweep.security.path import PathScopeError, resolve_writable_md

    with use_lang("en"):
        with pytest.raises(PathScopeError, match="path is empty or invalid"):
            resolve_writable_md("", roots=[tmp_path])
        with pytest.raises(PathScopeError, match=r"path cannot contain '\.\.'"):
            resolve_writable_md("../x.md", roots=[tmp_path])
