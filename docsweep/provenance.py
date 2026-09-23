"""AI author/execution provenance for work Markdown.

The generic store is a private user-level CSV. Repositories that already own a
provenance schema can set ``provenance.manager: repo`` and docsweep will delegate
without writing a second ledger.
"""

from __future__ import annotations

import csv
import io
import os
import posixpath
import re
import sys
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from pathlib import Path

from .atomic import update_line, write_atomic
from .config import Config, load_config, privacy_enforced
from .doc_vocab import column, column_variants, heading_by_lang, heading_variants
from .i18n import t
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
# 作業 MD の見出しと表の列名（文書の中身なので、表示言語では変えない）。日本語・英語の
# どちらの文書も読み、足す列名はその表の言語で書く（語彙は docsweep/doc_vocab.py）。
_CONTEXT_HEADINGS: dict[str, str] = {
    f"## {name}": code for code, name in heading_by_lang("context").items()
}
_CONTEXT_SECTION = " / ".join(heading_variants("context"))
_AI_EXECUTION_COLUMNS = frozenset(column_variants("ai_execution"))
_EXECUTION_MODEL_COLUMNS = frozenset(column_variants("execution_model"))


def _find_context_section(lines: list[str]) -> tuple[int | None, str]:
    """``## context配分`` の行番号とその表の言語。無ければ (None, "ja")。"""
    for index, line in enumerate(lines):
        lang = _CONTEXT_HEADINGS.get(line.strip())
        if lang is not None:
            return index, lang
    return None, "ja"


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

    def is_unresolved(self) -> bool:
        """AI 情報が 1 つも取れず、既定値のまま記録されようとしているか。

        呼び出し側で ``metadata.agent == "unknown"`` のように文字列を直書きさせない。
        既定値の綴りが変わったときに、判定だけ古いまま残るのを避ける。
        """
        return self.agent == "unknown" and self.model_source == "unavailable"

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
        raise ProvenanceError(t("provenance.invalid_value", field=field))
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
        raise ProvenanceError(t("provenance.outside_project", path=resolved)) from exc


def _work_path_key(value: str) -> str:
    """台帳の work_path を比べるための正規化（区切り文字・``./``・Windows の大文字小文字の揺れを吸収）。"""
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if not text:
        return ""
    normalized = posixpath.normpath(text)
    return normalized.casefold() if os.name == "nt" else normalized


def _real_key(path: Path) -> str:
    """junction / symlink を解いた比較用のパス。ファイルが無くても、在る親までは解く。"""
    try:
        resolved = os.path.realpath(os.fspath(path))
    except (OSError, ValueError):
        resolved = os.path.abspath(os.fspath(path))
    return os.path.normcase(os.path.normpath(resolved))


def _moved_work_path(old_work_path: str, new_path: Path, project_dir: Path) -> str | None:
    """移した後の場所を、台帳と同じ書式（project 相対・``/`` 区切り・junction を解かない経路）で返す。

    移送先は実体側のパスで届く（``docs/local`` が junction なら repo の外）。旧 work_path の祖先を
    近い順に実体へ解き、移送先を含む最初の祖先の下へ付け替える。project の中で表せなければ None。
    """
    root = Path(os.path.abspath(os.path.normpath(os.fspath(project_dir))))
    lexical = Path(os.path.normpath(os.fspath(root / old_work_path)))
    target = Path(os.path.realpath(os.fspath(new_path)))
    for ancestor in lexical.parents:
        try:
            ancestor.relative_to(root)
        except ValueError:
            break
        try:
            inner = target.relative_to(os.path.realpath(os.fspath(ancestor)))
        except ValueError:
            continue
        return (ancestor / inner).relative_to(root).as_posix()
    return None


def document_work_id(path: Path) -> str:
    """文書の frontmatter の ``work_id``（無ければ空文字）。provenance を使っていない文書を先に外すのに使う。"""
    data = read_frontmatter(path) if Path(path).is_file() else None
    return str((data or {}).get("work_id") or "").strip()


def follow_move(src: Path, dst: Path, *, project_dir: Path) -> dict | None:
    """移した文書の台帳行の ``work_path`` を、移した後の場所へ付け替える。

    archive（sweep / promote / apply / release close / Web）と ``docsweep mv`` は
    ``archive.archive_file`` を、``docsweep undo`` は ``services.archive.undo_last_batch`` を通るので、
    そこから 1 件移すたびに呼ぶ。``src`` は移す前、``dst`` は移した後の場所（どちらも実体パスでよい）。

    付け替えるのは、移した文書の frontmatter の ``work_id`` を持ち、``work_path`` が移す前の場所を
    指している行だけ。台帳は全 project 共通なので、同じ project 相対パスを持つ別 project の行を
    巻き込まないよう work_id でも絞る。比較は junction を解いた実体パスで行い、書く値は
    :func:`_relative_work_path` と同じ書式（project 相対・``/`` 区切り）にする。

    文書に work_id が無い・provenance が無効・repo 管理・台帳が無い・該当行が無いときは何もしない
    （None）。設定は ``docsweep provenance`` と同じく、既定の global 設定と project の
    ``.docsweep.yaml`` から読む。
    """
    work_id = document_work_id(dst)
    if not work_id:
        return None
    config = load_config(project_dir=Path(project_dir))
    if config.provenance_manager == "repo" or not config.provenance_enabled:
        return None
    ledger = config.provenance_ledger
    if not ledger.is_file():
        return None
    root = Path(os.path.abspath(os.path.normpath(os.fspath(project_dir))))
    before = _real_key(src)
    updated: list[dict[str, str]] = []
    with _ledger_lock(ledger):
        rows = _read_ledger(ledger)
        for row in rows:
            if row["work_id"] != work_id or not row["work_path"]:
                continue
            if _real_key(root / row["work_path"]) != before:
                continue
            moved = _moved_work_path(row["work_path"], dst, root)
            if moved is None:
                raise ProvenanceError(t("provenance.outside_project", path=dst))
            if moved != row["work_path"]:
                updated.append(
                    {"execution_id": row["execution_id"], "from": row["work_path"], "to": moved}
                )
                row["work_path"] = moved
        if updated:
            _write_ledger(ledger, rows)
    if not updated:
        return None
    return {"ledger": str(ledger), "work_id": work_id, "updated": updated}


def follow_move_safely(
    src: Path,
    dst: Path,
    *,
    project_dir: Path | Callable[[], Path | None] | None,
) -> dict | None:
    """:func:`follow_move` を、移動を失敗させない形で呼ぶ（失敗は stderr へ警告 1 行）。

    ファイルの移動はもう済んでいるので巻き戻さない。付け替えられなかった行は
    ``docsweep provenance check --path <md> --fix-work-path`` で後から直せる。

    ``project_dir`` の特定に手間がかかる呼び出し側（undo。root 配下の走査が要る）は関数で渡す。
    関数は work_id を持つ文書（provenance を使っている文書）のときだけ呼ぶ。
    ``project_dir`` が分からない（None）ときは、provenance を使っている文書に限って警告する。
    """
    try:
        if callable(project_dir):
            if not document_work_id(dst):
                return None
            project_dir = project_dir()
        if project_dir is None:
            if document_work_id(dst):
                _warn_follow_failed(dst, t("provenance.follow_project_unknown"))
            return None
        return follow_move(src, dst, project_dir=project_dir)
    except Exception as exc:  # 台帳の不具合（lock・schema・書き込み・設定）で移動を失敗させない
        _warn_follow_failed(dst, exc)
        return None


def _warn_follow_failed(path: Path, error: object) -> None:
    message = t("provenance.follow_move_failed", path=path, error=error)
    print(t("common.warning", message=message), file=sys.stderr)


def _delegated(config: Config) -> dict | None:
    if config.provenance_manager == "repo":
        return {
            "status": "delegated",
            "manager": "repo",
            "delegate_skill": config.provenance_delegate_skill,
            "changed": False,
        }
    if not config.provenance_enabled or config.provenance_manager == "disabled":
        raise ProvenanceError(t("provenance.disabled"))
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
                raise ProvenanceError(t("provenance.lock_timeout", path=lock)) from None
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
            raise ProvenanceError(t("provenance.ledger_schema_mismatch", path=path))
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
        raise ProvenanceError(
            t("provenance.duplicate_execution", execution_id=row["execution_id"])
        )
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
    raise ProvenanceError(t("provenance.refs_not_list"))


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
    section, table_lang = _find_context_section(lines)
    if section is None:
        raise ProvenanceError(
            t("provenance.context_table_missing", heading=" / ".join(_CONTEXT_HEADINGS))
        )
    ai_name = column("ai_execution", table_lang)
    model_name = column("execution_model", table_lang)
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

    ai_col = next((i for i, value in enumerate(header_cells) if value in _AI_EXECUTION_COLUMNS), None)
    if ai_col is None:
        model_col = next(
            (i for i, value in enumerate(header_cells) if value in _EXECUTION_MODEL_COLUMNS), None
        )
        ai_col = model_col if model_col is not None else len(header_cells)
        for row in table_rows.values():
            row.insert(ai_col, "")
        table_rows[header][ai_col] = ai_name
        table_rows[header + 1][ai_col] = "---"
        if model_col is not None:
            model_col += 1

    model_col = next(
        (i for i, value in enumerate(header_cells) if value in _EXECUTION_MODEL_COLUMNS), None
    )
    if execution_model is not None and model_col is None:
        model_col = ai_col + 1
        for row in table_rows.values():
            row.insert(model_col, "")
        table_rows[header][model_col] = model_name
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
        raise ProvenanceError(
            t(
                "provenance.context_not_found",
                section=_CONTEXT_SECTION,
                contexts=", ".join(missing),
            )
        )
    newline = "\r\n" if lines[header].endswith("\r\n") else "\n"
    for index, row in table_rows.items():
        if index == header + 1:
            lines[index] = "|" + "|".join(row) + "|" + newline
        else:
            lines[index] = "| " + " | ".join(row) + " |" + newline
    return "".join(lines)


def _context_execution_refs(text: str) -> set[str]:
    """Return execution IDs written in the context配分 AI実行 column (either language)."""
    lines = text.splitlines()
    section, _lang = _find_context_section(lines)
    if section is None:
        return set()
    end = next((i for i in range(section + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    header = next(
        (
            i for i in range(section + 1, end)
            if lines[i].lstrip().startswith("|")
            and any(name in lines[i] for name in _AI_EXECUTION_COLUMNS)
        ),
        None,
    )
    if header is None:
        return set()
    header_cells = [part.strip() for part in lines[header].strip().strip("|").split("|")]
    ai_col = next(
        (i for i, value in enumerate(header_cells) if value in _AI_EXECUTION_COLUMNS), None
    )
    if ai_col is None:
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


_CORRECTION_NOTE = "ai-metadata-corrected"


def _update_authoring_metadata(
    path: Path, *, config: Config, data: dict, metadata: AIMetadata
) -> dict:
    """既存 work の authoring 行と frontmatter の AI metadata **だけ** を実値へ直す。

    ``docsweep new`` が provenance 有効のまま ``--ai-*`` 無しで走ると、作成 AI が
    全項目 ``unknown`` で残る。あとから frontmatter だけ直すと ``check_document`` が
    ``valid=false`` になり、台帳 CSV を手で編集して辻褄を合わせることになる。
    手編集が常態化すると台帳自体が信用できなくなるので、両方を 1 手で直す口を用意する。

    ``execution_id`` / ``work_id`` / ``started_at`` / ``role`` / ``work_path`` は
    変えない。台帳は append-only の思想なので、訂正したことは ``notes`` 列へ残す
    （列は増やさない）。
    """
    work_id = str(data["work_id"])
    with _ledger_lock(config.provenance_ledger):
        rows = _read_ledger(config.provenance_ledger)
        targets = [
            row for row in rows
            if row.get("work_id") == work_id and row.get("role") == "authoring"
        ]
        if not targets:
            raise ProvenanceError(t("provenance.authoring_row_missing", work_id=work_id))
        previous = [dict(row) for row in rows]
        changed_ledger = False
        for row in targets:
            updates = {
                "actor_key": metadata.actor_key,
                "agent": metadata.agent,
                "runtime": metadata.runtime,
                "provider": metadata.provider,
                "model_id": metadata.model_id,
                "model_display": metadata.model_display,
                "reasoning_profile": metadata.reasoning_profile,
                "model_source": metadata.model_source,
            }
            if all(row.get(key) == value for key, value in updates.items()):
                continue
            row.update(updates)
            notes = (row.get("notes") or "").strip()
            if _CORRECTION_NOTE not in notes:
                row["notes"] = f"{notes};{_CORRECTION_NOTE}".strip(";")
            changed_ledger = True
        if changed_ledger:
            _write_ledger(config.provenance_ledger, rows)
        fields: dict[str, str | list[str]] = {
            "ai_provenance_version": PROVENANCE_VERSION,
            "ai_author_agent": metadata.agent,
            "ai_author_runtime": metadata.runtime,
            "ai_author_provider": metadata.provider,
            "ai_author_model_id": metadata.model_id,
            "ai_author_model_display": metadata.model_display,
            "ai_author_reasoning": metadata.reasoning_profile,
            "ai_author_model_source": metadata.model_source,
        }
        try:
            _patch_frontmatter(path, fields)
        except Exception:
            if changed_ledger:
                _write_ledger(config.provenance_ledger, previous)
            raise
    return {
        "status": "updated",
        "changed": True,
        "path": str(path.resolve()),
        "work_id": work_id,
        "execution_refs": _frontmatter_refs(data),
        "updated_executions": [row["execution_id"] for row in targets],
        "ledger": str(config.provenance_ledger),
    }


def initialize_document(
    path: Path,
    *,
    project_dir: Path,
    config: Config,
    metadata: AIMetadata,
    backfill: bool = False,
    update: bool = False,
) -> dict:
    delegated = _delegated(config)
    if delegated is not None:
        return {**delegated, "path": str(path.resolve())}
    if not path.is_file():
        raise ProvenanceError(t("provenance.document_missing", path=path))
    work_path = _relative_work_path(path, project_dir)
    data = read_frontmatter(path) or {}
    if data.get("work_id") and all(data.get(field) is not None for field in AUTHOR_FIELDS):
        if update:
            return _update_authoring_metadata(path, config=config, data=data, metadata=metadata)
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


def _merged_session_logs(config: Config, metadata: AIMetadata, data: dict) -> list[str]:
    """既存の ``ai_session_logs`` へ、今のセッションの transcript を重複なく足す。

    足すものが無ければ空リストを返し、フィールドには触らない（``unknown`` のような
    偽の値を置かない。パスは実在するかどうかが意味を持つ値なので）。
    """
    current = _session_log_lines(config, metadata)
    if not current:
        return []
    existing: list[str] = []
    raw = data.get(SESSION_LOG_FIELD)
    if isinstance(raw, str):
        existing = [raw.strip()] if raw.strip() else []
    elif isinstance(raw, (list, tuple)):
        existing = [str(item).strip() for item in raw if str(item).strip()]
    merged = list(existing)
    seen = {os.path.normcase(value) for value in existing}
    added = False
    for value in current:
        key = os.path.normcase(value)
        if key in seen:
            continue
        seen.add(key)
        merged.append(value)
        added = True
    return merged if added else []


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
        raise ProvenanceError(t("provenance.invalid_role", role=role))
    normalized = [value.strip() for value in contexts if value.strip()]
    if not normalized:
        raise ProvenanceError(t("provenance.context_required"))
    if "not-applicable" in normalized and len(normalized) != 1:
        raise ProvenanceError(t("provenance.not_applicable_exclusive"))
    invalid = [value for value in normalized if value != "not-applicable" and not _CONTEXT_RE.match(value)]
    if invalid:
        raise ProvenanceError(t("provenance.invalid_context", contexts=", ".join(invalid)))
    if not path.is_file():
        raise ProvenanceError(t("provenance.document_missing", path=path))
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
    # plan は複数セッションにまたがる。作成時の 1 本だけでは C2 以降の実行を追えないので、
    # 実行を開始したセッションの transcript も足す。``initialize_document`` は work_id を
    # 持つ md をスキップするため、追記はここでしかできない。
    session_logs = _merged_session_logs(config, metadata, data)
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
            if session_logs:
                updated = _replace_or_insert(
                    updated,
                    SESSION_LOG_FIELD,
                    _format_value(SESSION_LOG_FIELD, session_logs),
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
        raise ProvenanceError(t("provenance.invalid_result", result=result))
    with _ledger_lock(config.provenance_ledger):
        rows = _read_ledger(config.provenance_ledger)
        matches = [row for row in rows if row["execution_id"] == execution_id]
        if not matches:
            raise ProvenanceError(t("provenance.execution_not_found", execution_id=execution_id))
        row = matches[0]
        if row["result"] != "started":
            raise ProvenanceError(
                t(
                    "provenance.execution_already_finished",
                    execution_id=execution_id,
                    result=row["result"],
                )
            )
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


def _repair_work_paths(
    path: Path, *, project_dir: Path, config: Config, data: dict
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """``check --fix-work-path``: 文書の work_id と ai_execution_refs の両方に一致する台帳行の
    ``work_path`` を、今の場所へ直す（移動に台帳が追従しなかった版で移した文書の後始末）。

    記録された場所に今も別のファイルがある行は直さない（複製か別の文書で、どちらが正しいか
    機械には決められない）。返り値は (直した行, 直さなかった行)。
    """
    work_id = str(data.get("work_id") or "").strip()
    refs = set(_frontmatter_refs(data)) if "ai_execution_refs" in data else set()
    ledger = config.provenance_ledger
    if not work_id or not refs or not ledger.is_file():
        return [], []
    current = _relative_work_path(path, project_dir)
    current_key = _real_key(path)
    root = Path(os.path.abspath(os.path.normpath(os.fspath(project_dir))))
    fixed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    with _ledger_lock(ledger):
        rows = _read_ledger(ledger)
        for row in rows:
            if row["execution_id"] not in refs or row["work_id"] != work_id:
                continue
            if _work_path_key(row["work_path"]) == _work_path_key(current):
                continue
            recorded = root / row["work_path"] if row["work_path"] else None
            if recorded is not None and recorded.is_file() and _real_key(recorded) != current_key:
                skipped.append(
                    {
                        "execution_id": row["execution_id"],
                        "work_path": row["work_path"],
                        "reason": "recorded_path_exists",
                    }
                )
                continue
            fixed.append({"execution_id": row["execution_id"], "from": row["work_path"], "to": current})
            row["work_path"] = current
        if fixed:
            _write_ledger(ledger, rows)
    return fixed, skipped


def check_document(
    path: Path, *, project_dir: Path, config: Config, fix_work_path: bool = False
) -> dict:
    """文書・台帳・context配分 の整合を検査する。

    ``fix_work_path=True`` では検査の前に :func:`_repair_work_paths` で台帳の work_path を今の場所へ
    直し、直した行を ``work_path_fixed``、直さなかった行を ``work_path_skipped`` に入れて返す。
    """
    if config.provenance_manager == "repo":
        return {
            "status": "delegated",
            "manager": "repo",
            "delegate_skill": config.provenance_delegate_skill,
            "path": str(path.resolve()),
            "valid": True,
            "errors": [],
            "warnings": [t("provenance.delegate_to_repo")],
        }
    _delegated(config)
    errors: list[str] = []
    warnings: list[str] = []
    data = read_frontmatter(path) if path.is_file() else None
    if data is None:
        return {
            "status": "checked",
            "valid": False,
            "errors": [t("provenance.frontmatter_missing")],
            "warnings": [],
        }
    fixed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    if fix_work_path:
        fixed, skipped = _repair_work_paths(path, project_dir=project_dir, config=config, data=data)
    for field in ("work_id", "ai_provenance_version", *AUTHOR_FIELDS, "ai_execution_refs"):
        if field not in data:
            errors.append(t("provenance.frontmatter_field_missing", field=field))
    refs = _frontmatter_refs(data) if "ai_execution_refs" in data else []
    rows = _read_ledger(config.provenance_ledger)
    by_id = {row["execution_id"]: row for row in rows}
    for ref in refs:
        row = by_id.get(ref)
        if row is None:
            errors.append(t("provenance.ledger_execution_missing", ref=ref))
        elif str(data.get("work_id", "")) != row["work_id"]:
            errors.append(t("provenance.work_id_mismatch", ref=ref))
    author_rows = [by_id[ref] for ref in refs if ref in by_id and by_id[ref]["role"] == "authoring"]
    if not author_rows:
        errors.append(t("provenance.authoring_missing"))
    elif data.get("ai_author_agent") != author_rows[0]["agent"]:
        errors.append(t("provenance.author_agent_mismatch"))
    work_path = _relative_work_path(path, project_dir)
    for ref in refs:
        if ref in by_id and _work_path_key(by_id[ref]["work_path"]) != _work_path_key(work_path):
            warnings.append(t("provenance.work_path_changed", ref=ref))
    text = path.open("r", encoding="utf-8", newline="").read()
    table_refs = _context_execution_refs(text)
    for ref in table_refs - set(refs):
        errors.append(
            t(
                "provenance.context_ref_not_in_frontmatter",
                section=_CONTEXT_SECTION,
                column=" / ".join(column_variants("ai_execution")),
                ref=ref,
            )
        )
    for row in by_id.values():
        if row["work_id"] != str(data.get("work_id", "")):
            continue
        contexts = [part for part in row["context_id"].split(";") if part]
        if row["role"] != "authoring" and contexts != ["not-applicable"]:
            if row["execution_id"] not in table_refs:
                errors.append(
                    t(
                        "provenance.execution_not_in_context",
                        section=_CONTEXT_SECTION,
                        execution_id=row["execution_id"],
                    )
                )
    result = {
        "status": "checked",
        "path": str(path.resolve()),
        "work_id": data.get("work_id"),
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "execution_refs": refs,
        "ledger": str(config.provenance_ledger),
    }
    if fix_work_path:
        result["changed"] = bool(fixed)
        result["work_path_fixed"] = fixed
        result["work_path_skipped"] = skipped
    return result


def result_dict(value: object) -> dict:
    """Small public helper for callers that accept dataclass or dict results."""
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return dict(value)  # type: ignore[call-overload]
