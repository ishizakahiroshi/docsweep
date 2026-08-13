"""provider ごとの生ログ解決。

fixture はすべて合成データで組み立てる（実環境のセッションログを転記しない）。
判定の要は「1 本に絞れないときは何も返さない」ことなので、返る場合だけでなく
**返らない場合**を同じ密度でテストする。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from docsweep import session_logs
from docsweep.session_logs import (
    resolve_codex_rollout,
    resolve_copilot_session,
    resolve_current_session_log,
    resolve_cursor_chat,
    resolve_grok_history,
)


def _fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(session_logs, "_home", lambda: home)
    return home


def _work_dir(tmp_path: Path, name: str = "sample-project") -> Path:
    cwd = tmp_path / "work" / name
    cwd.mkdir(parents=True, exist_ok=True)
    return cwd


def _short_cwd(name: str = "proj") -> Path:
    """Grok 用の短い絶対パス。

    Grok はセッションディレクトリ名に cwd を丸ごとエンコードして埋めるため、
    ``tmp_path`` のような長いパスを渡すと Windows の 260 文字制限に当たって
    fixture 自体が作れない。解決側は cwd の実在を要求しないので、短い架空の
    絶対パスで足りる（`os.sep` 起点にして Win/Unix 双方で絶対パスにする）。
    """
    return Path(os.path.abspath(os.sep + name))


def _codex_rollout(
    codex_home: Path,
    name: str,
    *,
    cwd: Path,
    when: datetime,
    source: str = "cli",
    originator: str = "codex_cli_rs",
) -> Path:
    day = codex_home / "sessions" / f"{when.year:04d}" / f"{when.month:02d}" / f"{when.day:02d}"
    day.mkdir(parents=True, exist_ok=True)
    path = day / name
    meta = {
        "timestamp": when.isoformat(),
        "type": "session_meta",
        "payload": {
            "session_id": "00000000-0000-7000-8000-000000000000",
            "cwd": str(cwd),
            "timestamp": when.isoformat(),
            "originator": originator,
            "source": source,
        },
    }
    path.write_text(json.dumps(meta) + "\n", encoding="utf-8")
    return path


def _age_minutes(path: Path, minutes: int) -> None:
    stamp = (datetime.now() - timedelta(minutes=minutes)).timestamp()
    os.utime(path, (stamp, stamp))


def test_codex_rollout_resolves_when_one_session_is_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = _fake_home(tmp_path, monkeypatch)
    cwd = _work_dir(tmp_path)
    codex_home = home / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    now = datetime.now()
    want = _codex_rollout(codex_home, "rollout-mine.jsonl", cwd=cwd, when=now)
    _codex_rollout(
        codex_home, "rollout-elsewhere.jsonl", cwd=_work_dir(tmp_path, "other"), when=now
    )

    assert resolve_codex_rollout(cwd=cwd, now=now) == str(want)


def test_codex_refuses_when_two_sessions_share_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """並列に 2 セッション動かしている状態。片方を選ぶくらいなら書かない。"""
    home = _fake_home(tmp_path, monkeypatch)
    cwd = _work_dir(tmp_path)
    codex_home = home / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    now = datetime.now()
    _codex_rollout(codex_home, "rollout-a.jsonl", cwd=cwd, when=now)
    _codex_rollout(codex_home, "rollout-b.jsonl", cwd=cwd, when=now)

    assert resolve_codex_rollout(cwd=cwd, now=now) is None


def test_codex_ignores_a_session_that_stopped_being_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """同じ cwd で今日すでに終わったセッションのログを掴まない。"""
    home = _fake_home(tmp_path, monkeypatch)
    cwd = _work_dir(tmp_path)
    codex_home = home / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    now = datetime.now()
    stale = _codex_rollout(codex_home, "rollout-finished.jsonl", cwd=cwd, when=now)
    _age_minutes(stale, 60)

    assert resolve_codex_rollout(cwd=cwd, now=now) is None


def test_codex_ignores_desktop_and_vscode_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """~/.codex/sessions には Codex Desktop / VS Code 拡張のセッションも混ざる。"""
    home = _fake_home(tmp_path, monkeypatch)
    cwd = _work_dir(tmp_path)
    codex_home = home / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    now = datetime.now()
    _codex_rollout(
        codex_home, "rollout-desktop.jsonl", cwd=cwd, when=now,
        source="vscode", originator="Codex Desktop",
    )

    assert resolve_codex_rollout(cwd=cwd, now=now) is None

    want = _codex_rollout(codex_home, "rollout-cli.jsonl", cwd=cwd, when=now)
    assert resolve_codex_rollout(cwd=cwd, now=now) == str(want)


def test_grok_history_resolves_from_the_encoded_cwd_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = _fake_home(tmp_path, monkeypatch)
    cwd = _short_cwd()
    encoded = str(cwd).replace(":", "%3A").replace("\\", "%5C").replace("/", "%2F")
    session_dir = home / ".grok" / "sessions" / encoded / "0198fc00-0000-7000-8000-000000000001"
    session_dir.mkdir(parents=True)
    history = session_dir / "chat_history.jsonl"
    history.write_text("{}\n", encoding="utf-8")

    assert resolve_grok_history(cwd=cwd, now=datetime.now()) == str(history)


def test_grok_refuses_when_two_sessions_are_live_in_the_same_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = _fake_home(tmp_path, monkeypatch)
    cwd = _short_cwd()
    encoded = str(cwd).replace(":", "%3A").replace("\\", "%5C").replace("/", "%2F")
    root = home / ".grok" / "sessions" / encoded
    for suffix in ("0001", "0002"):
        session_dir = root / f"0198fc00-0000-7000-8000-00000000{suffix}"
        session_dir.mkdir(parents=True)
        (session_dir / "chat_history.jsonl").write_text("{}\n", encoding="utf-8")

    assert resolve_grok_history(cwd=cwd, now=datetime.now()) is None


def test_copilot_session_directory_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Copilot は 1 セッションが複数ファイルを持つのでディレクトリを返す。"""
    home = _fake_home(tmp_path, monkeypatch)
    cwd = _work_dir(tmp_path)
    session_dir = home / ".copilot" / "session-state" / "11111111-2222-4333-8444-555555555555"
    session_dir.mkdir(parents=True)
    (session_dir / "workspace.yaml").write_text(
        yaml.safe_dump({"cwd": str(cwd), "created_at": "2026-08-12T23:00:00+09:00"}),
        encoding="utf-8",
    )

    assert resolve_copilot_session(cwd=cwd, now=datetime.now()) == str(session_dir)


def test_copilot_ignores_another_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = _fake_home(tmp_path, monkeypatch)
    cwd = _work_dir(tmp_path)
    session_dir = home / ".copilot" / "session-state" / "11111111-2222-4333-8444-555555555555"
    session_dir.mkdir(parents=True)
    (session_dir / "workspace.yaml").write_text(
        yaml.safe_dump({"cwd": str(_work_dir(tmp_path, "other")), "created_at": ""}),
        encoding="utf-8",
    )

    assert resolve_copilot_session(cwd=cwd, now=datetime.now()) is None


def test_cursor_chat_directory_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = _fake_home(tmp_path, monkeypatch)
    cwd = _work_dir(tmp_path)
    chat_dir = home / ".cursor" / "chats" / "abc123" / "11111111-2222-4333-8444-555555555555"
    chat_dir.mkdir(parents=True)
    (chat_dir / "meta.json").write_text(
        json.dumps({"cwd": str(cwd), "createdAtMs": 1_800_000_000_000}), encoding="utf-8"
    )

    assert resolve_cursor_chat(cwd=cwd, now=datetime.now()) == str(chat_dir)


def test_claude_transcript_wins_over_other_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Claude は ID 一致で確定するので、推定に頼る provider より先に採る。"""
    home = _fake_home(tmp_path, monkeypatch)
    cwd = _work_dir(tmp_path)
    session_id = "11111111-2222-4333-8444-555555555555"
    transcript = home / ".claude" / "projects" / "D--example" / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session_id)
    codex_home = home / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    now = datetime.now()
    _codex_rollout(codex_home, "rollout-mine.jsonl", cwd=cwd, when=now)

    assert resolve_current_session_log(cwd=cwd, now=now) == str(transcript)


def test_nothing_is_resolved_on_a_clean_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _fake_home(tmp_path, monkeypatch)

    assert resolve_current_session_log(cwd=_work_dir(tmp_path), now=datetime.now()) is None


def test_future_mtime_is_not_fresh(tmp_path: Path):
    path = tmp_path / "future.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    now = datetime.now()
    future = (now + timedelta(minutes=5)).timestamp()
    os.utime(path, (future, future))

    assert session_logs._is_fresh(path, now) is False
