"""Locate the transcript that the AI CLI writing this document keeps for itself.

Only paths are produced. A transcript holds prompts, source snippets, and
credentials, so nothing here reads conversation content: the JSONL probes read
the first line only, and the remaining probes read small metadata files.

**Every resolver refuses to guess.** The wrapper that launches these CLIs
(many-ai-cli) can match a session by the start time it holds as the launching
process. docsweep runs *inside* the session and has no such reference point, so
unless the candidates narrow to exactly one recently-written session for this
working directory, nothing is returned. A path pointing at a neighbouring
session is worse than no path at all, because the record looks equally
authoritative either way.

Layouts mirror ``internal/hub/agent_log_handler.go`` and
``internal/hub/grok_history_handler.go`` in many-ai-cli. Keeping a second copy of
that knowledge is deliberate: it lets docsweep work without many-ai-cli
installed, at the cost of updating both when a vendor moves its logs.

opencode is intentionally unsupported: it keeps every session in one SQLite
store (``~/.local/share/opencode/opencode.db``), so a single session cannot be
identified at all.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import yaml

# 「今このセッションが書いているログ」を、同じ cwd に残る過去セッションのログから
# 区別するための窓。動いているセッションのログは書かれ続け、終了したものは止まる。
FRESH_WINDOW = timedelta(minutes=10)

CLAUDE_SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
CLAUDE_CONFIG_ENV = "CLAUDE_CONFIG_DIR"
CODEX_HOME_ENV = "CODEX_HOME"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Codex Desktop と VS Code 拡張のセッションも ~/.codex/sessions に混ざる（実測で
# ``originator: Codex Desktop`` / ``source: vscode`` を確認）。CLI 側が名乗る値は
# 環境やバージョンで変わりうるため、許可リストではなく除外リストで弾く。
_CODEX_NON_CLI_SOURCES = frozenset({"vscode", "desktop", "ide"})
_CODEX_NON_CLI_ORIGINATOR_HINTS = ("desktop", "vscode", "vs code")

# Grok はセッションディレクトリ名に cwd を URL エンコードして埋める。
_GROK_CWD_ENCODING = ((":", "%3A"), ("\\", "%5C"), ("/", "%2F"))


def resolve_current_session_log(
    *, cwd: Path | None = None, now: datetime | None = None
) -> str | None:
    """Return the transcript path of the session running docsweep, if certain."""
    for resolver in (
        resolve_claude_transcript,
        resolve_codex_rollout,
        resolve_grok_history,
        resolve_copilot_session,
        resolve_cursor_chat,
    ):
        try:
            hit = resolver(cwd=cwd, now=now)
        except OSError:
            continue
        if hit:
            return hit
    return None


def resolve_claude_transcript(
    *, cwd: Path | None = None, now: datetime | None = None
) -> str | None:
    """Resolve the Claude Code transcript from the session ID it exports.

    This is the one runtime that needs no narrowing: the ID identifies the file.
    The transcript lives under a directory named after the cwd, but ``docsweep
    new --project-dir`` can create a document in a different repository than the
    one the session runs in, so the directory is matched by glob rather than by
    reproducing that mapping.
    """
    del cwd, now  # An exported ID beats any heuristic; neither is needed.
    session_id = os.environ.get(CLAUDE_SESSION_ENV, "").strip()
    # The ID becomes a filename component in the glob, so accept UUIDs only.
    if not _UUID_RE.match(session_id):
        return None
    roots = []
    configured = os.environ.get(CLAUDE_CONFIG_ENV, "").strip()
    if configured:
        roots.append(Path(configured))
    roots.append(_home() / ".claude")
    for root in roots:
        try:
            hits = sorted((root / "projects").glob(f"*/{session_id}.jsonl"))
        except OSError:
            continue
        for hit in hits:
            if hit.is_file():
                return str(hit)
    return None


def resolve_codex_rollout(
    *, cwd: Path | None = None, now: datetime | None = None
) -> str | None:
    """Resolve the Codex rollout JSONL under ``~/.codex/sessions/YYYY/MM/DD/``.

    Codex writes the file itself and records ``cwd`` / ``timestamp`` /
    ``session_id`` in the ``session_meta`` first line. ``session_id`` equals the
    uuid in the filename, so if a future Codex release exports it to the
    environment this whole scan collapses into one glob.
    """
    root = _provider_home(CODEX_HOME_ENV, ".codex") / "sessions"
    if not root.is_dir():
        return None
    cwd = _cwd(cwd)
    now = now or datetime.now()
    candidates: list[Path] = []
    for day_dir in _codex_day_dirs(root, now):
        for entry in _iter_files(day_dir, ".jsonl"):
            meta = _codex_session_meta(entry)
            if meta is None or not _same_path(meta.get("cwd"), cwd):
                continue
            if _codex_is_non_cli(meta) or not _is_fresh(entry, now):
                continue
            candidates.append(entry)
    return _only(candidates)


def resolve_grok_history(
    *, cwd: Path | None = None, now: datetime | None = None
) -> str | None:
    """Resolve ``~/.grok/sessions/<encoded cwd>/<session id>/chat_history.jsonl``."""
    cwd = _cwd(cwd)
    now = now or datetime.now()
    root = _home() / ".grok" / "sessions" / _grok_encode(str(cwd))
    candidates = [
        history
        for session_dir in _iter_dirs(root)
        if (history := session_dir / "chat_history.jsonl").is_file()
        and _is_fresh(history, now)
    ]
    return _only(candidates)


def resolve_copilot_session(
    *, cwd: Path | None = None, now: datetime | None = None
) -> str | None:
    """Resolve ``~/.copilot/session-state/<uuid>/`` via its ``workspace.yaml``.

    Copilot keeps several files per session, so the directory is the useful
    handle here (the same choice many-ai-cli makes).
    """
    cwd = _cwd(cwd)
    now = now or datetime.now()
    candidates = []
    for session_dir in _iter_dirs(_home() / ".copilot" / "session-state"):
        meta = _read_mapping(session_dir / "workspace.yaml", yaml.safe_load)
        if meta is None or not _same_path(meta.get("cwd"), cwd):
            continue
        if not _is_fresh(session_dir, now):
            continue
        candidates.append(session_dir)
    return _only(candidates)


def resolve_cursor_chat(
    *, cwd: Path | None = None, now: datetime | None = None
) -> str | None:
    """Resolve ``~/.cursor/chats/<hash>/<uuid>/`` via its ``meta.json``."""
    cwd = _cwd(cwd)
    now = now or datetime.now()
    candidates = []
    for hash_dir in _iter_dirs(_home() / ".cursor" / "chats"):
        for chat_dir in _iter_dirs(hash_dir):
            meta = _read_mapping(chat_dir / "meta.json", json.loads)
            if meta is None or not _same_path(meta.get("cwd"), cwd):
                continue
            if not _is_fresh(chat_dir, now):
                continue
            candidates.append(chat_dir)
    return _only(candidates)


def _only(candidates: list[Path]) -> str | None:
    """Return the single candidate, or nothing when it is not single.

    Zero and two-or-more are the same answer on purpose: both mean "this cannot
    be established", and picking one of two would produce a confident-looking
    pointer to someone else's session.
    """
    unique = {os.path.normcase(os.path.normpath(str(path))): path for path in candidates}
    if len(unique) != 1:
        return None
    return str(next(iter(unique.values())))


def _cwd(cwd: Path | None) -> Path:
    if cwd is not None:
        return cwd
    try:
        return Path.cwd()
    except OSError:
        return Path(".")


def _home() -> Path:
    try:
        return Path.home()
    except RuntimeError:
        return Path(os.path.expanduser("~"))


def _provider_home(env_name: str, default_name: str) -> Path:
    configured = os.environ.get(env_name, "").strip()
    if configured:
        return Path(configured)
    return _home() / default_name


def _same_path(recorded: object, cwd: Path) -> bool:
    text = str(recorded or "").strip()
    if not text:
        return False
    candidate = Path(text)
    if not candidate.is_absolute():
        return False
    return _normalize(candidate) == _normalize(cwd)


def _normalize(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _latest_mtime(path: Path) -> float | None:
    """Newest write under ``path`` — the file itself, or its direct children.

    A directory's own mtime does not move when a file inside it is appended to,
    which is exactly what an active session does, so directories are judged by
    their contents.
    """
    try:
        if path.is_file():
            return path.stat().st_mtime
        if path.is_dir():
            stamps = [entry.stat().st_mtime for entry in path.iterdir() if entry.is_file()]
            return max(stamps) if stamps else path.stat().st_mtime
    except OSError:
        return None
    return None


def _is_fresh(path: Path, now: datetime) -> bool:
    stamp = _latest_mtime(path)
    if stamp is None:
        return False
    try:
        written = datetime.fromtimestamp(stamp)
    except (OverflowError, OSError, ValueError):
        return False
    age = now - written
    # The caller may capture ``now`` just before a provider writes its file;
    # tolerate a small clock/mtime skew without accepting materially future
    # timestamps as live sessions.
    return -timedelta(minutes=1) <= age <= FRESH_WINDOW


def _iter_dirs(root: Path):
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir():
            yield entry


def _iter_files(root: Path, suffix: str):
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.suffix == suffix and entry.is_file():
            yield entry


def _codex_day_dirs(root: Path, now: datetime) -> list[Path]:
    """Yesterday, today, tomorrow — a session can straddle midnight."""
    days = [now - timedelta(days=1), now, now + timedelta(days=1)]
    return [root / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}" for d in days]


def _codex_session_meta(path: Path) -> dict | None:
    line = _first_line(path)
    if not line:
        return None
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("type") != "session_meta":
        return None
    payload = data.get("payload")
    return payload if isinstance(payload, dict) else None


def _codex_is_non_cli(meta: dict) -> bool:
    source = str(meta.get("source") or "").strip().lower()
    if source in _CODEX_NON_CLI_SOURCES:
        return True
    originator = str(meta.get("originator") or "").strip().lower()
    return any(hint in originator for hint in _CODEX_NON_CLI_ORIGINATOR_HINTS)


def _first_line(path: Path, *, limit: int = 4 * 1024 * 1024) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.readline(limit)
    except OSError:
        return ""


def _read_mapping(path: Path, loader) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        data = loader(text)
    except (ValueError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _grok_encode(cwd: str) -> str:
    encoded = cwd
    for raw, replacement in _GROK_CWD_ENCODING:
        encoded = encoded.replace(raw, replacement)
    return encoded
