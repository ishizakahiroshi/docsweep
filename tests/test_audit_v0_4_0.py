"""v0.4.0 リリース前監査（未リリース新規分）で見つかった問題の回帰テスト。

対象は `secrets_guard` / `work_queue` / `export` の private・secret まわり。
いずれも「守っているつもりで守れていない」「黙って落とす」型で、
テストが無かったために気付けなかったもの。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.config import load_config
from docsweep.export import collect_export
from docsweep.secrets_guard import (
    SensitiveContentError,
    enforce_secret_policy,
    high_confidence_hits,
    scan_secrets,
)
from docsweep.work_queue import ensure_write_allowed

# --- secrets_guard --------------------------------------------------------


def _kinds(text: str) -> set[str]:
    return {h["kind"] for h in high_confidence_hits(scan_secrets(text))}


def test_modern_openai_project_key_is_detected() -> None:
    """`sk-proj-...` 形式。旧パターンは `sk-` 直後に英数 20 文字を要求して取りこぼしていた。"""
    assert "openai_sk" in _kinds("key: sk-proj-" + "A" * 30)
    assert "openai_sk" in _kinds("key: sk-svcacct-" + "B" * 30)
    # 旧形式も引き続き検出する
    assert "openai_sk" in _kinds("key: sk-" + "C" * 30)


def test_other_common_credential_formats_are_detected() -> None:
    assert "slack_token" in _kinds("xoxb-" + "1" * 20)
    assert "google_api_key" in _kinds("AIza" + "a" * 35)
    assert "stripe_secret" in _kinds("sk_live_" + "9" * 20)


def test_high_confidence_contextual_credentials_are_detected() -> None:
    jwt = "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12
    assert "jwt" in _kinds(jwt)
    assert "authorization_bearer" in _kinds(
        "Authorization: Bearer " + "A" * 32
    )
    assert "aws_secret_access_key" in _kinds(
        "aws_secret_access_key = " + "A" * 40
    )
    assert "password_assignment" in _kinds(
        "password: " + "p" * 24
    )


def test_private_key_block_is_not_limited_to_known_algorithms() -> None:
    for header in (
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN DSA PRIVATE KEY-----",
        "-----BEGIN PGP PRIVATE KEY BLOCK-----",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----",
    ):
        assert "private_key_block" in _kinds(header), header


def test_unknown_secret_policy_fails_closed() -> None:
    """設定の綴り違いで保護が黙って無効化されないこと。"""
    text = "token: sk-ant-" + "A" * 30
    with pytest.raises(SensitiveContentError):
        enforce_secret_policy(text, policy="blcok")  # typo
    with pytest.raises(SensitiveContentError):
        enforce_secret_policy(text, policy="")
    # 明示的な off だけが無効化できる
    assert enforce_secret_policy(text, policy="off") == []


def test_detection_result_never_carries_the_secret_value() -> None:
    secret = "sk-ant-" + "Z" * 30
    hits = scan_secrets(f"token: {secret}")
    blob = repr(hits)
    assert secret not in blob
    assert "Z" * 10 not in blob


# --- work_queue -----------------------------------------------------------


def test_directory_only_gitignore_pattern_counts_as_ignored(tmp_path: Path) -> None:
    """`.gitignore` が `docs/local/`（末尾 /）で queue が未作成でも ignore 済みと判定する。

    `git check-ignore` は実在しないパスをディレクトリと判断できないため、
    ディレクトリ限定パターンに一致しない。作業 queue の初回作成時はまさにこの状態で、
    **新規プロジェクトの 1 本目が「Git ignore されていません」で作れなかった**。
    docsweep 自身のテンプレート・ドキュメントが `docs/local/` 表記なので必ず踏む。
    """
    import subprocess

    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], capture_output=True, check=True)
    (project / ".gitignore").write_text("docs/local/\n", encoding="utf-8")
    (project / ".docsweep.yaml").write_text(
        "work_dir: docs/local\nwork_policy: private\nsecret_policy: block\n", encoding="utf-8"
    )
    queue = project / "docs" / "local"
    assert not queue.exists()  # 初回なので未作成

    cfg = load_config(
        project_dir=project,
        explicit_roots=[str(project)],
        global_path=tmp_path / "missing.yaml",
    )
    result = ensure_write_allowed(config=cfg, project_dir=project, target_dir=queue)
    assert result.ignored is True
    assert result.ok


def test_downgraded_private_error_becomes_a_visible_warning(tmp_path: Path) -> None:
    """互換 fallback で private queue の error を落とすとき、黙って消さない。"""
    project = tmp_path / "proj"
    queue = project / "docs" / "local"
    queue.mkdir(parents=True)
    global_cfg = tmp_path / "global.yaml"
    # roots だけを持つ「既存利用者」の設定。work_dir / work_policy は未指定。
    global_cfg.write_text(f"roots:\n  - {project.as_posix()}\n", encoding="utf-8")

    cfg = load_config(project_dir=project, global_path=global_cfg)
    assert cfg.work_policy == "private"

    result = ensure_write_allowed(config=cfg, project_dir=project, target_dir=queue)
    # Git 管理外なので ignore 状態は不明。error にはならないが警告は残る。
    assert result.warnings, "互換 fallback の内容が warnings に残っていない"


# --- export ---------------------------------------------------------------


def _make_workspace(tmp_path: Path, *, explicit_policy: bool) -> tuple[Path, Path]:
    root = tmp_path / "dev"
    project = root / "demo"
    queue = project / "docs" / "local"
    queue.mkdir(parents=True)
    (queue / "plan_alpha.md").write_text(
        "---\ntype: plan\nstatus: draft\ndocsweep_state: planned\n---\n\n# [計画] alpha\n",
        encoding="utf-8",
    )
    if explicit_policy:
        (project / ".docsweep.yaml").write_text("work_policy: private\n", encoding="utf-8")
    return root, project


def test_export_excludes_private_queue_when_policy_is_explicit(tmp_path: Path) -> None:
    root, _project = _make_workspace(tmp_path, explicit_policy=True)
    cfg = load_config(explicit_roots=[str(root)], global_path=tmp_path / "no.yaml")
    files, _pairs = collect_export(cfg)
    assert files == [], "work_policy を明示した private queue が export に含まれている"


def test_export_warns_when_nominally_private_queue_is_included(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """互換 fallback で除外を強制しない場合は、含めたことを必ず通知する。

    zip は共有・添付が主用途なので、「private のはずが入っていた」に後で気付くのでは遅い。
    """
    root, _project = _make_workspace(tmp_path, explicit_policy=False)
    global_cfg = tmp_path / "global.yaml"
    global_cfg.write_text(f"roots:\n  - {root.as_posix()}\n", encoding="utf-8")

    cfg = load_config(explicit_roots=[str(root)], global_path=global_cfg)
    files, _pairs = collect_export(cfg)
    assert len(files) == 1  # 既存利用者の export を空にしない（挙動は変えない）

    err = capsys.readouterr().err
    assert "work_policy=private" in err
    assert "demo" in err
