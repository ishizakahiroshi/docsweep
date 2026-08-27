"""AI author/execution provenance for work Markdown.

The generic store is a private user-level CSV. Repositories that already own a
provenance schema can set ``provenance.manager: repo`` and docsweep will delegate
without writing a second ledger.
"""

from __future__ import annotations

import csv
import io
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .atomic import update_line, write_atomic
from .config import Config, privacy_enforced
from .services.frontmatter import (
    FrontmatterValidationError,
    _format_value,
    _replace_or_insert,
    _validate_list_item,
    read_frontmatter,
)
from .session_logs import resolve_current_session_log

PROVENANCE_VERSION = "1"
ROLES = frozenset({"authoring", "implementation", "review", "verification"})
RESULTS = frozenset({"started", "completed", "partial", "failed", "cancelled"})
LEDGER_FIELDS = (
    "execution_id",
    "project_id",
    "work_id",
    "work_path",
    "context_id",
    "actor_key",
    "role",
    "agent",
    "runtime",
    "provider",
    "model_id",
    "model_display",
    "reasoning_profile",
    "model_source",
    "started_at",
    "ended_at",
    "result",
    "evidence_refs",
    "notes",
)
AUTHOR_FIELDS = (
    "ai_author_agent",
    "ai_author_runtime",
    "ai_author_provider",
    "ai_author_model_id",
    "ai_author_model_display",
    "ai_author_reasoning",
    "ai_author_model_source",
)
ENV_FIELDS = {
    "agent": "DOCSWEEP_AI_AGENT",
    "runtime": "DOCSWEEP_AI_RUNTIME",
    "provider": "DOCSWEEP_AI_PROVIDER",
    "model_id": "DOCSWEEP_AI_MODEL_ID",
    "model_display": "DOCSWEEP_AI_MODEL_DISPLAY",
    "reasoning_profile": "DOCSWEEP_AI_REASONING",
    "model_source": "DOCSWEEP_AI_MODEL_SOURCE",
    "actor_key": "DOCSWEEP_AI_ACTOR_KEY",
}
SESSION_LOG_FIELD = "ai_session_logs"
SESSION_LOG_ENV = "DOCSWEEP_AI_SESSION_LOG"
_CONTEXT_RE = re.compile(r"^C[1-9][0-9]*$")


class ProvenanceError(ValueError):
    """Provenance input, consistency, or storage error."""


def resolve_session_logs(*, explicit: str | None = None) -> tuple[str, ...]:
    """Locate the provider-owned transcript of the session writing this document.

    Only the path is recorded; contents are never read. The per-provider layouts
    and the "refuse to guess when it cannot be narrowed to one" rule live in
    :mod:`docsweep.session_logs`.

    ``DOCSWEEP_AI_SESSION_LOG`` overrides everything, for runtimes that cannot be
    identified from inside their own session.
    """
    for candidate in (explicit, os.environ.get(SESSION_LOG_ENV)):
        resolved = _existing_log_path(candidate)
        if resolved:
            return (resolved,)
    resolved = resolve_current_session_log()
    return (resolved,) if resolved else ()


def _existing_log_path(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        return None
    try:
        # Copilot と Cursor Agent は 1 セッションがディレクトリなので、
        # is_file() で判定するとその 2 つを取りこぼす。
        if not path.exists():
            return None
    except OSError:
        return None
    return str(path)


@dataclass(frozen=True)
class AIMetadata:
    agent: str = "unknown"
    runtime: str = "unknown"
    provider: str = "unknown"
    model_id: str = "unknown"
    model_display: str = "unknown"
    reasoning_profile: str = "unknown"
    model_source: str = "unavailable"
    actor_key: str = "unknown"
    session_logs: tuple[str, ...] = ()

    @classmethod
    def resolve(cls, *, actor_default: str | None = None, **overrides: str | None) -> AIMetadata:
        session_log = overrides.pop("session_log", None)
        values: dict[str, str] = {}
        for name, env_name in ENV_FIELDS.items():
            raw = overrides.get(name) or os.environ.get(env_name)
            if name == "actor_key" and not raw:
                raw = actor_default
            fallback = "unavailable" if name == "model_source" else "unknown"
            values[name] = _clean(raw or fallback, name)
        if values["model_source"] == "unavailable":
            values["model_id"] = "unknown"
            values["model_display"] = "unknown"
        return cls(**values, session_logs=resolve_session_logs(explicit=session_log))

    @classmethod
    def unknown(cls, *, actor_key: str | None = None) -> AIMetadata:
        # Backfill path: the caller is filling in a document written earlier, so
        # the transcript of the current session is not this document's evidence.
        return cls(actor_key=_clean(actor_key or "unknown", "actor_key"))


def _clean(value: str, field: str) -> str:
    text = str(value).strip()
    if not text:
        return "unknown"
    if "\n" in text or "\r" in text or len(text) > 240:
        raise ProvenanceError(f"{field} に改行または長すぎる値は指定できません")
    return text


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f")[:-3]
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def _project_id(config: Config, project_dir: Path) -> str:
    if config.provenance_project_id:
        return _clean(config.provenance_project_id, "project_id")
    slug = re.sub(r"[^a-z0-9]+", "-", project_dir.name.lower()).strip("-")
    return slug or "project"


def _relative_work_path(path: Path, project_dir: Path) -> str:
    # Keep the lexical project-relative route. ``docs/local`` may intentionally
    # be a junction to a private workspace; Path.resolve() would turn that valid
    # route into an apparent project escape.
    resolved = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
    root = Path(os.path.abspath(os.path.normpath(os.fspath(project_dir))))
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ProvenanceError(f"対象MDがプロジェクト外です: {resolved}") from exc


def _delegated(config: Config) -> dict | None:
    if config.provenance_manager == "repo":
        return {
            "status": "delegated",
            "manager": "repo",
            "delegate_skill": config.provenance_delegate_skill,
            "changed": False,
        }
    if not config.provenance_enabled or config.provenance_manager == "disabled":
        raise ProvenanceError(
            "AI provenance は無効です。config の provenance.enabled=true / manager=docsweep を確認してください"
        )
    return None


@contextmanager
def _ledger_lock(path: Path, timeout: float = 5.0):
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 120:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise ProvenanceError(
                    f"provenance 台帳のlock取得がtimeoutしました: {lock}"
                ) from None
            time.sleep(0.05)
    try:
        os.write(fd, f"pid={os.getpid()}\n".encode("ascii"))
        os.close(fd)
        fd = None
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _read_ledger(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != LEDGER_FIELDS:
            raise ProvenanceError(f"provenance 台帳の列が現行schemaと一致しません: {path}")
        return [{key: row.get(key, "") for key in LEDGER_FIELDS} for row in reader]


def _write_ledger(path: Path, rows: list[dict[str, str]]) -> None:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=LEDGER_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    write_atomic(path, stream.getvalue())


def _append_row(path: Path, row: dict[str, str]) -> list[dict[str, str]]:
    rows = _read_ledger(path)
    if any(existing["execution_id"] == row["execution_id"] for existing in rows):
        raise ProvenanceError(f"execution ID が重複しています: {row['execution_id']}")
    previous = [dict(existing) for existing in rows]
    rows.append(row)
    _write_ledger(path, rows)
    return previous


def _frontmatter_refs(data: dict) -> list[str]:
    raw = data.get("ai_execution_refs")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        value = raw.strip().strip("[]")
        return [part.strip() for part in value.split(",") if part.strip()]
    raise ProvenanceError("ai_execution_refs は YAML list である必要があります")


def _session_log_lines(config: Config, metadata: AIMetadata) -> list[str]:
    """Return the transcript paths that may be written into this document.

    An absolute path carries the OS user name, so it stays in private queues.
    """
    if (
        str(getattr(config, "work_policy", "private")).strip().lower() != "private"
        or not privacy_enforced(config)
    ):
        return []
    safe: list[str] = []
    for path in metadata.session_logs:
        try:
            safe.append(_validate_list_item(path))
        except FrontmatterValidationError:
            # Flow-style YAML cannot carry a path containing `,` or a quote.
            # The field is supplementary, so drop it instead of failing the
            # document generation it is attached to.
            continue
    return safe


def _patch_frontmatter(path: Path, fields: dict[str, str | list[str]]) -> None:
    def transform(text: str) -> str:
        updated = text
        for field, value in fields.items():
            updated = _replace_or_insert(updated, field, _format_value(field, value))
        return updated

    update_line(path, transform=transform)


def _execution_model_cell(role: str, metadata: AIMetadata) -> str:
    def display(value: str | None) -> str:
        text = str(value or "").strip()
        return text or "unknown"

    return (
        f"{display(role)}: {display(metadata.provider)} / "
        f"{display(metadata.model_id)} / {display(metadata.reasoning_profile)}"
    )


# 実行モデル列が導入される前に記録された実行には、md 側にモデル情報が無い。
# 同じ helper で作ることで、埋め草もセルの書式（role: provider / model / reasoning）を保つ。
_UNKNOWN_EXECUTION_MODEL = _execution_model_cell("unknown", AIMetadata())


def _append_context_execution(
    text: str,
    contexts: list[str],
    execution_id: str,
    *,
    execution_model: str | None = None,
) -> str:
    if contexts == ["not-applicable"]:
        return text
    lines = text.splitlines(keepends=True)
    section = next((i for i, line in enumerate(lines) if line.strip() == "## context配分"), None)
    if section is None:
        raise ProvenanceError("対象Cを記録するにはMDに ## context配分 表が必要です")
    end = next(
        (i for i in range(section + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    header = next(
        (i for i in range(section + 1, end) if lines[i].lstrip().startswith("|") and " C " in lines[i]),
        None,
    )
    if header is None or header + 1 >= end:
        return text

    def cells(line: str) -> list[str]:
        return [part.strip() for part in line.strip().strip("|").split("|")]

    table_rows: dict[int, list[str]] = {
        header: cells(lines[header]),
        header + 1: cells(lines[header + 1]),
    }
    for index in range(header + 2, end):
        if lines[index].lstrip().startswith("|"):
            table_rows[index] = cells(lines[index])

    header_cells = table_rows[header]
    original_width = len(header_cells)
    for row in table_rows.values():
        while len(row) < original_width:
            row.append("")

    ai_col = next((i for i, value in enumerate(header_cells) if value == "AI実行"), None)
    if ai_col is None:
        model_col = next((i for i, value in enumerate(header_cells) if value == "実行モデル"), None)
        ai_col = model_col if model_col is not None else len(header_cells)
        for row in table_rows.values():
            row.insert(ai_col, "")
        table_rows[header][ai_col] = "AI実行"
        table_rows[header + 1][ai_col] = "---"
        if model_col is not None:
            model_col += 1

    model_col = next((i for i, value in enumerate(header_cells) if value == "実行モデル"), None)
    if execution_model is not None and model_col is None:
        model_col = ai_col + 1
        for row in table_rows.values():
            row.insert(model_col, "")
        table_rows[header][model_col] = "実行モデル"
        table_rows[header + 1][model_col] = "---"
    elif execution_model is not None and model_col != ai_col + 1:
        # A hand-edited table may already contain the model column elsewhere.
        # Move it next to AI実行 so the two provenance columns stay paired.
        assert model_col is not None
        if model_col < ai_col:
            ai_col -= 1
        for row in table_rows.values():
            value = row.pop(model_col)
            row.insert(ai_col + 1, value)
        model_col = ai_col + 1

    found: set[str] = set()
    for index, row in table_rows.items():
        if index in (header, header + 1):
            continue
        if not row:
            continue
        while len(row) <= ai_col:
            row.append("")
        context = row[0]
        if context not in contexts:
            continue
        found.add(context)
        refs = [part.strip() for part in row[ai_col].split(";") if part.strip()]
        is_new_execution = execution_id not in refs
        if is_new_execution:
            refs.append(execution_id)
        row[ai_col] = "; ".join(refs)
        if execution_model is not None and model_col is not None:
            model_refs = [part.strip() for part in row[model_col].split(";") if part.strip()]
            if is_new_execution:
                # AI実行 列に既存 ID があってモデル情報が無い行（列の導入前に記録された行）では、
                # 埋めずに追記すると 1 件目のモデルが 2 件目の ID のものとして誤読される。
                # 位置で対応づけて読めるよう、欠けている分を先に埋めてから追記する。
                while len(model_refs) < len(refs) - 1:
                    model_refs.append(_UNKNOWN_EXECUTION_MODEL)
                model_refs.append(execution_model)
            row[model_col] = "; ".join(model_refs)
    missing = sorted(set(contexts) - found)
    if missing:
        raise ProvenanceError(f"context配分に対象Cがありません: {', '.join(missing)}")
    newline = "\r\n" if lines[header].endswith("\r\n") else "\n"
    for index, row in table_rows.items():
        if index == header + 1:
            lines[index] = "|" + "|".join(row) + "|" + newline
        else:
            lines[index] = "| " + " | ".join(row) + " |" + newline
    return "".join(lines)


def _context_execution_refs(text: str) -> set[str]:
    """Return execution IDs written in the context配分 AI実行 column."""
    lines = text.splitlines()
    section = next((i for i, line in enumerate(lines) if line.strip() == "## context配分"), None)
    if section is None:
        return set()
    end = next((i for i in range(section + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    header = next(
        (i for i in range(section + 1, end) if lines[i].lstrip().startswith("|") and "AI実行" in lines[i]),
        None,
    )
    if header is None:
        return set()
    header_cells = [part.strip() for part in lines[header].strip().strip("|").split("|")]
    try:
        ai_col = header_cells.index("AI実行")
    except ValueError:
        return set()
    refs: set[str] = set()
    for line in lines[header + 2:end]:
        if not line.lstrip().startswith("|"):
            continue
        cells = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(cells) <= ai_col:
            continue
        refs.update(part.strip() for part in cells[ai_col].split(";") if part.strip())
    return refs


def initialize_document(
    path: Path,
    *,
    project_dir: Path,
    config: Config,
    metadata: AIMetadata,
    backfill: bool = False,
) -> dict:
    delegated = _delegated(config)
    if delegated is not None:
        return {**delegated, "path": str(path.resolve())}
    if not path.is_file():
        raise ProvenanceError(f"対象MDがありません: {path}")
    work_path = _relative_work_path(path, project_dir)
    data = read_frontmatter(path) or {}
    if data.get("work_id") and all(data.get(field) is not None for field in AUTHOR_FIELDS):
        return {
            "status": "existing",
            "changed": False,
            "path": str(path.resolve()),
            "work_id": str(data["work_id"]),
            "execution_refs": _frontmatter_refs(data),
        }
    work_id = str(data.get("work_id") or _new_id("WK"))
    execution_id = _new_id("AIX")
    started_at = _now()
    row = {
        "execution_id": execution_id,
        "project_id": _project_id(config, project_dir),
        "work_id": work_id,
        "work_path": work_path,
        "context_id": "not-applicable",
        "actor_key": metadata.actor_key,
        "role": "authoring",
        "agent": metadata.agent,
        "runtime": metadata.runtime,
        "provider": metadata.provider,
        "model_id": metadata.model_id,
        "model_display": metadata.model_display,
        "reasoning_profile": metadata.reasoning_profile,
        "model_source": metadata.model_source,
        "started_at": started_at,
        "ended_at": started_at,
        "result": "completed",
        "evidence_refs": "",
        "notes": "backfill-no-primary-evidence" if backfill else "",
    }
    refs = _frontmatter_refs(data)
    if execution_id not in refs:
        refs.append(execution_id)
    with _ledger_lock(config.provenance_ledger):
        previous_rows = _append_row(config.provenance_ledger, row)
        fields: dict[str, str | list[str]] = {
            "work_id": work_id,
            "ai_provenance_version": PROVENANCE_VERSION,
            "ai_author_agent": metadata.agent,
            "ai_author_runtime": metadata.runtime,
            "ai_author_provider": metadata.provider,
            "ai_author_model_id": metadata.model_id,
            "ai_author_model_display": metadata.model_display,
            "ai_author_reasoning": metadata.reasoning_profile,
            "ai_author_model_source": metadata.model_source,
            "ai_execution_refs": refs,
        }
        session_logs = _session_log_lines(config, metadata)
        if session_logs:
            fields[SESSION_LOG_FIELD] = session_logs
        try:
            _patch_frontmatter(path, fields)
        except Exception:
            _write_ledger(config.provenance_ledger, previous_rows)
            raise
    return {
        "status": "initialized",
        "changed": True,
        "path": str(path.resolve()),
        "work_id": work_id,
        "execution_id": execution_id,
        "ledger": str(config.provenance_ledger),
    }


def start_execution(
    path: Path,
    *,
    project_dir: Path,
    config: Config,
    contexts: list[str],
    role: str,
    metadata: AIMetadata,
    notes: str = "",
) -> dict:
    delegated = _delegated(config)
    if delegated is not None:
        return {**delegated, "path": str(path.resolve()), "contexts": contexts, "role": role}
    if role not in ROLES - {"authoring"}:
        raise ProvenanceError(f"role は implementation/review/verification のいずれかです: {role}")
    normalized = [value.strip() for value in contexts if value.strip()]
    if not normalized:
        raise ProvenanceError("context を1件以上指定してください")
    if "not-applicable" in normalized and len(normalized) != 1:
        raise ProvenanceError("not-applicable は他のcontextと併用できません")
    invalid = [value for value in normalized if value != "not-applicable" and not _CONTEXT_RE.match(value)]
    if invalid:
        raise ProvenanceError(f"不正なcontext IDです: {', '.join(invalid)}")
    if not path.is_file():
        raise ProvenanceError(f"対象MDがありません: {path}")
    data = read_frontmatter(path) or {}
    if not data.get("work_id"):
        initialize_document(
            path,
            project_dir=project_dir,
            config=config,
            metadata=AIMetadata.unknown(actor_key=config.provenance_actor_key),
            backfill=True,
        )
        data = read_frontmatter(path) or {}
    work_id = str(data["work_id"])
    execution_id = _new_id("AIX")
    row = {
        "execution_id": execution_id,
        "project_id": _project_id(config, project_dir),
        "work_id": work_id,
        "work_path": _relative_work_path(path, project_dir),
        "context_id": ";".join(normalized),
        "actor_key": metadata.actor_key,
        "role": role,
        "agent": metadata.agent,
        "runtime": metadata.runtime,
        "provider": metadata.provider,
        "model_id": metadata.model_id,
        "model_display": metadata.model_display,
        "reasoning_profile": metadata.reasoning_profile,
        "model_source": metadata.model_source,
        "started_at": _now(),
        "ended_at": "",
        "result": "started",
        "evidence_refs": "",
        "notes": _clean(notes, "notes") if notes else "",
    }
    refs = _frontmatter_refs(data)
    if execution_id not in refs:
        refs.append(execution_id)
    execution_model = _execution_model_cell(role, metadata)
    # Validate the context table before taking an ID into the ledger. This avoids
    # leaving an orphan execution when the requested C does not exist.
    original_text = path.open("r", encoding="utf-8", newline="").read()
    _append_context_execution(
        original_text,
        normalized,
        execution_id,
        execution_model=execution_model,
    )
    with _ledger_lock(config.provenance_ledger):
        previous_rows = _append_row(config.provenance_ledger, row)

        def transform(text: str) -> str:
            updated = _replace_or_insert(
                text,
                "ai_execution_refs",
                _format_value("ai_execution_refs", refs),
            )
            return _append_context_execution(
                updated,
                normalized,
                execution_id,
                execution_model=execution_model,
            )

        try:
            update_line(path, transform=transform)
        except Exception:
            _write_ledger(config.provenance_ledger, previous_rows)
            raise
    return {
        "status": "started",
        "changed": True,
        "execution_id": execution_id,
        "work_id": work_id,
        "contexts": normalized,
        "role": role,
        "ledger": str(config.provenance_ledger),
    }


def finish_execution(
    execution_id: str,
    *,
    config: Config,
    result: str,
    evidence_refs: str = "",
    notes: str | None = None,
) -> dict:
    delegated = _delegated(config)
    if delegated is not None:
        return {**delegated, "execution_id": execution_id, "result": result}
    if result not in RESULTS - {"started"}:
        raise ProvenanceError(f"result は completed/partial/failed/cancelled のいずれかです: {result}")
    with _ledger_lock(config.provenance_ledger):
        rows = _read_ledger(config.provenance_ledger)
        matches = [row for row in rows if row["execution_id"] == execution_id]
        if not matches:
            raise ProvenanceError(f"execution ID が台帳にありません: {execution_id}")
        row = matches[0]
        if row["result"] != "started":
            raise ProvenanceError(f"execution はすでに終了しています: {execution_id} ({row['result']})")
        row["result"] = result
        row["ended_at"] = _now()
        if evidence_refs:
            row["evidence_refs"] = _clean(evidence_refs, "evidence_refs")
        if notes is not None:
            row["notes"] = _clean(notes, "notes") if notes else ""
        _write_ledger(config.provenance_ledger, rows)
    return {
        "status": "finished",
        "changed": True,
        "execution_id": execution_id,
        "result": result,
        "ended_at": row["ended_at"],
        "ledger": str(config.provenance_ledger),
    }


def check_document(path: Path, *, project_dir: Path, config: Config) -> dict:
    if config.provenance_manager == "repo":
        return {
            "status": "delegated",
            "manager": "repo",
            "delegate_skill": config.provenance_delegate_skill,
            "path": str(path.resolve()),
            "valid": True,
            "errors": [],
            "warnings": ["repo固有validatorへ委譲してください"],
        }
    _delegated(config)
    errors: list[str] = []
    warnings: list[str] = []
    data = read_frontmatter(path) if path.is_file() else None
    if data is None:
        return {"status": "checked", "valid": False, "errors": ["frontmatterがありません"], "warnings": []}
    for field in ("work_id", "ai_provenance_version", *AUTHOR_FIELDS, "ai_execution_refs"):
        if field not in data:
            errors.append(f"frontmatterに {field} がありません")
    refs = _frontmatter_refs(data) if "ai_execution_refs" in data else []
    rows = _read_ledger(config.provenance_ledger)
    by_id = {row["execution_id"]: row for row in rows}
    for ref in refs:
        row = by_id.get(ref)
        if row is None:
            errors.append(f"台帳にexecutionがありません: {ref}")
        elif str(data.get("work_id", "")) != row["work_id"]:
            errors.append(f"work_idが一致しません: {ref}")
    author_rows = [by_id[ref] for ref in refs if ref in by_id and by_id[ref]["role"] == "authoring"]
    if not author_rows:
        errors.append("authoring executionがありません")
    elif data.get("ai_author_agent") != author_rows[0]["agent"]:
        errors.append("ai_author_agentとauthoring executionが一致しません")
    work_path = _relative_work_path(path, project_dir)
    for ref in refs:
        if ref in by_id and by_id[ref]["work_path"] != work_path:
            warnings.append(f"台帳のwork_pathが現在pathと異なります: {ref}")
    text = path.open("r", encoding="utf-8", newline="").read()
    table_refs = _context_execution_refs(text)
    for ref in table_refs - set(refs):
        errors.append(f"context配分のAI実行がfrontmatter参照にありません: {ref}")
    for row in by_id.values():
        if row["work_id"] != str(data.get("work_id", "")):
            continue
        contexts = [part for part in row["context_id"].split(";") if part]
        if row["role"] != "authoring" and contexts != ["not-applicable"]:
            if row["execution_id"] not in table_refs:
                errors.append(f"C executionがcontext配分にありません: {row['execution_id']}")
    return {
        "status": "checked",
        "path": str(path.resolve()),
        "work_id": data.get("work_id"),
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "execution_refs": refs,
        "ledger": str(config.provenance_ledger),
    }


def result_dict(value: object) -> dict:
    """Small public helper for callers that accept dataclass or dict results."""
    return asdict(value) if hasattr(value, "__dataclass_fields__") else dict(value)  # type: ignore[arg-type]
