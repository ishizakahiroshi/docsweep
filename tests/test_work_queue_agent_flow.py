"""設定化 work queue / private queue / secret guard の回帰テスト。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from docsweep.capture import save_drafts
from docsweep.capture.models import Draft
from docsweep.config import archive_dir_for_project, load_config
from docsweep.doctor import run_doctor
from docsweep.export import run_export
from docsweep.inject import generate_guidance_block, preview_inject
from docsweep.services.content import update_content


def _draft(body: str, name: str = "plan_x.md") -> Draft:
    return Draft(
        id="d1",
        kind="plan",
        title="x",
        body=body,
        suggested_filename=name,
    )


def test_project_work_dir_precedence_and_archive_coupling(tmp_path: Path):
    global_cfg = tmp_path / "global.yaml"
    global_cfg.write_text(
        "work_dir: docs/global\nwork_policy: shared\nsecret_policy: warn\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / ".docsweep.yaml").write_text(
        "work_dir: docs/ai\nwork_policy: private\nsecret_policy: block\n",
        encoding="utf-8",
    )
    cfg = load_config(project_dir=project, global_path=global_cfg)
    assert cfg.work_dir == "docs/ai"
    assert cfg.work_policy == "private"
    assert cfg.secret_policy == "block"
    assert archive_dir_for_project(project, cfg) == "docs/ai/archive"


def test_capture_custom_queue_and_secret_is_checked_before_mkdir(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = load_config(
        project_dir=project,
        explicit_roots=[str(project)],
        global_path=tmp_path / "missing.yaml",
    )
    cfg.work_dir = "docs/ai"
    cfg.work_policy = "shared"
    cfg.secret_policy = "block"
    secret = "# [計画] x\n\nopenai key: sk-" + ("A" * 28) + "\n"
    with pytest.raises(PermissionError):
        save_drafts([_draft(secret)], config=cfg, target_dir=project / "docs" / "ai", project_dir=project)
    assert not (project / "docs" / "ai").exists()

    saved = save_drafts(
        [_draft(secret, "plan_allowed.md")],
        config=cfg,
        target_dir=project / "docs" / "ai",
        project_dir=project,
        allow_sensitive=True,
    )
    assert saved[0].parent == project / "docs" / "ai"


def test_queue_root_link_is_allowed_but_nested_escape_is_rejected(tmp_path: Path):
    """設定済み queue root の junction は許可し、内側の脱出 symlink は拒否する。"""
    from docsweep.work_queue import WorkQueueError, resolve_work_target

    project = tmp_path / "project"
    project.mkdir()
    queue_real = tmp_path / "private-queue"
    queue_real.mkdir()
    link = project / "docs" / "local"
    link.parent.mkdir()
    try:
        link.symlink_to(queue_real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("ディレクトリ junction/symlink を作成できない環境")

    cfg = load_config(
        project_dir=project,
        explicit_roots=[str(tmp_path)],
        global_path=tmp_path / "missing.yaml",
    )
    root, target = resolve_work_target(cfg, project_dir=project)
    assert root == project
    assert target == link

    outside = tmp_path / "outside"
    outside.mkdir()
    escape = link / "escape"
    try:
        escape.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("配下 symlink を作成できない環境")
    with pytest.raises(WorkQueueError):
        resolve_work_target(cfg, project_dir=project, explicit_dir=escape)


def test_private_queue_unignored_is_rejected_before_write(tmp_path: Path):
    project = tmp_path / "project"
    queue = project / "docs" / "ai"
    queue.mkdir(parents=True)
    subprocess.run(["git", "init", str(project)], capture_output=True, check=True)
    cfg = load_config(
        project_dir=project,
        explicit_roots=[str(project)],
        global_path=tmp_path / "missing.yaml",
    )
    (project / ".docsweep.yaml").write_text(
        "work_dir: docs/ai\nwork_policy: private\nsecret_policy: off\n",
        encoding="utf-8",
    )
    cfg = load_config(
        project_dir=project,
        explicit_roots=[str(project)],
        global_path=tmp_path / "missing.yaml",
    )
    with pytest.raises(PermissionError, match="ignore"):
        save_drafts([_draft("# [計画] x\n")], config=cfg, target_dir=queue, project_dir=project)
    assert not list(queue.glob("plan_*.md"))


def test_low_confidence_warns_without_secret_value_and_content_update_blocks(tmp_path: Path, capsys):
    project = tmp_path / "project"
    queue = project / "docs" / "ai"
    queue.mkdir(parents=True)
    (project / ".gitignore").write_text("docs/ai/\n", encoding="utf-8")
    subprocess.run(["git", "init", str(project)], capture_output=True, check=True)
    cfg = load_config(
        project_dir=project,
        explicit_roots=[str(project)],
        global_path=tmp_path / "missing.yaml",
    )
    (project / ".docsweep.yaml").write_text(
        "work_dir: docs/ai\nwork_policy: private\nsecret_policy: block\n",
        encoding="utf-8",
    )
    cfg = load_config(
        project_dir=project,
        explicit_roots=[str(project)],
        global_path=tmp_path / "missing.yaml",
    )
    low = "# [計画] x\n\ntoken: abcdefgh\n"
    saved = save_drafts([_draft(low)], config=cfg, target_dir=queue, project_dir=project)
    assert saved
    stderr = capsys.readouterr().err
    assert "possible secret" in stderr
    assert "abcdefgh" not in stderr

    target = queue / "plan_edit.md"
    target.write_text("# [計画] edit\n", encoding="utf-8")
    high = "# [計画] edit\n\nkey: ghp_" + ("b" * 36) + "\n"
    with pytest.raises(PermissionError):
        update_content(target, high, config=cfg)
    assert target.read_text(encoding="utf-8") == "# [計画] edit\n"


def test_inject_project_mentions_resolved_queue_global_does_not(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".docsweep.yaml").write_text(
        "work_dir: docs/ai\nwork_policy: shared\nsecret_policy: warn\n",
        encoding="utf-8",
    )
    preview = preview_inject(project, preset="claude-jp")
    text = "\n".join(block["text"] for block in preview["blocks"])
    assert "docs/ai" in text
    global_text = generate_guidance_block("ja")
    assert "docs/ai" not in global_text
    assert "docsweep" in global_text and "new" in global_text


def test_private_queue_is_excluded_from_export(tmp_path: Path):
    project = tmp_path / "project"
    queue = project / "docs" / "ai"
    queue.mkdir(parents=True)
    (project / ".git").mkdir()
    (project / ".gitignore").write_text("docs/ai/\n", encoding="utf-8")
    (project / ".docsweep.yaml").write_text(
        "work_dir: docs/ai\nwork_policy: private\nsecret_policy: block\n",
        encoding="utf-8",
    )
    (queue / "plan_private.md").write_text("# [計画] private\n", encoding="utf-8")
    cfg = load_config(
        explicit_roots=[str(tmp_path)],
        global_path=tmp_path / "missing.yaml",
    )
    result = run_export(cfg, out=tmp_path / "bundle.zip")
    assert result.file_count == 0


def test_doctor_reports_unignored_private_queue(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", str(project)], capture_output=True, check=True)
    (project / ".docsweep.yaml").write_text(
        "work_dir: docs/ai\nwork_policy: private\nsecret_policy: block\n",
        encoding="utf-8",
    )
    cfg = load_config(
        project_dir=project,
        explicit_roots=[str(project)],
        global_path=tmp_path / "missing.yaml",
    )
    report = run_doctor(config=cfg, project_dir=project, index_db=tmp_path / "missing.db")
    item = next(item for item in report.items if item.id == "work_queue")
    assert item.status == "fail"


def test_precommit_hook_blocks_staged_private_queue_without_secret_value(tmp_path: Path):
    project = tmp_path / "project"
    queue = project / "docs" / "ai"
    queue.mkdir(parents=True)
    (project / ".gitignore").write_text("docs/ai/\n", encoding="utf-8")
    (project / ".docsweep.yaml").write_text(
        "work_dir: docs/ai\nwork_policy: private\nsecret_policy: block\n",
        encoding="utf-8",
    )
    secret = "ghp_" + ("z" * 36)
    (queue / "plan_staged.md").write_text(
        f"# [計画] staged\n\nkey: {secret}\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", str(project)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(project), "add", ".docsweep.yaml"], check=True)
    subprocess.run(
        ["git", "-C", str(project), "add", "-f", "docs/ai/plan_staged.md"],
        check=True,
    )
    hook = Path(__file__).parents[1] / "templates" / ".githooks" / "docsweep-check.py"
    result = subprocess.run(
        [sys.executable, str(hook)],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 1
    assert "private work_dir" in result.stderr
    assert "secret" in result.stderr
    assert secret not in result.stderr
