"""``docsweep export --okf`` — OKF（Open Knowledge Format）準拠の zip / tarball 出力。

スキャン範囲内の plan / bugfix / pending を frontmatter ごとそのまま取り出し、
``okf-manifest.json`` を同梱して 1 つの zip にまとめる。docsweep を使わなくなっても
md は OKF 互換語彙で読めることを実演するためのコマンド。

不変条件:

- ファイル本文は触らない（frontmatter / H1 / 本文をバイトレベルで温存）
- ``--include-archive`` 指定時のみ ``archive/`` 配下も含める（既定は除外）
- manifest は OKF 語彙との対応表 + 生成日時 + docsweep バージョンを含む
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from . import __version__
from .config import Config, config_for_project, project_work_settings, privacy_enforced, resolve_work_dir
from .engine import run_scan
from .okf import OkfProfile, bundled_okf_profile, load_okf_profile
from .scan import scan_root
from .services.frontmatter import read_frontmatter_text


# docsweep 固定 type 集合の説明。OKF v0.2 は type を登録制にしていないため、
# これは docsweep 固有の補助情報であり、Bundle の type を制限する schema ではない。
OKF_TYPE_VOCABULARY: dict[str, dict[str, str]] = {
    "plan": {
        "okf_equivalent": "plan",
        "description": "計画 / 調査メモ / 検討メモ（着手前〜進行中の作業）",
    },
    "bugfix": {
        "okf_equivalent": "incident",
        "description": "障害対応の事後記録（症状 / 根本原因 / 修正内容）",
    },
    "pending": {
        "okf_equivalent": "deferred",
        "description": "保留 / 将来対応（着手条件待ち）",
    },
}

# docsweep 内部 state key → 選択された OKF profile の lifecycle status 値。
# 公開互換の定数として残すが、値の正本は同梱 profile JSON に置く。
OKF_STATUS_VOCABULARY: dict[str, str] = dict(bundled_okf_profile().docsweep_status_map)

# review_status の値域定義（draft / review / published）。OKF 仕様には review_status の明示が
# 無いので、docsweep が「OSS として宣言する許容値」をここで固定する。
OKF_REVIEW_STATUS_VOCABULARY: list[str] = ["draft", "review", "published"]


@dataclass
class ExportedFile:
    """manifest の files[] に並ぶ 1 件分。"""

    path: str  # zip 内の相対パス（POSIX）
    type: str | None
    status: str | None  # OKF lifecycle status
    title: str | None
    docsweep_state: str | None = None
    tags: list[str] = field(default_factory=list)
    owner: str | None = None
    review_status: str | None = None
    related: list[str] = field(default_factory=list)
    docsweep_parent: str | None = None
    last_reviewed: str | None = None
    normalized: bool = False

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "type": self.type,
            "status": self.status,
            "docsweep_state": self.docsweep_state,
            "title": self.title,
            "tags": list(self.tags),
            "owner": self.owner,
            "review_status": self.review_status,
            "related": list(self.related),
            "docsweep_parent": self.docsweep_parent,
            "last_reviewed": self.last_reviewed,
            "normalized": self.normalized,
        }


@dataclass
class ExportResult:
    out_path: str
    file_count: int
    files: list[ExportedFile] = field(default_factory=list)
    generated_at: str = ""
    include_archive: bool = False
    profile: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "out_path": self.out_path,
            "file_count": self.file_count,
            "generated_at": self.generated_at,
            "include_archive": self.include_archive,
            "profile": self.profile,
            "files": [f.to_dict() for f in self.files],
        }


def _default_out_path() -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return Path.cwd() / f"docsweep-okf-{today}.zip"


def _build_manifest(
    files: list[ExportedFile],
    *,
    generated_at: str,
    include_archive: bool,
    profile: OkfProfile,
) -> dict:
    """OKF manifest（zip に同梱する JSON）を組み立てる。"""
    return {
        "format": "okf",
        "okf_version": profile.spec_version,
        "okf_profile": profile.to_dict(),
        "docsweep_version": __version__,
        "generated_at": generated_at,
        "include_archive": include_archive,
        "type_vocabulary": OKF_TYPE_VOCABULARY,
        "status_vocabulary": dict(profile.docsweep_status_map),
        "review_status_vocabulary": OKF_REVIEW_STATUS_VOCABULARY,
        "file_count": len(files),
        "files": [f.to_dict() for f in files],
    }


def _zip_entry_path(record_path: str, project_root: str, project: str) -> str:
    """zip 内のエントリ名を ``<project>/<project からの相対パス>`` で揃える。"""
    src = Path(record_path)
    base = Path(project_root)
    try:
        rel = src.relative_to(base).as_posix()
    except ValueError:
        rel = src.name
    return f"{project}/{rel}"


def _gather_archive_files(config: Config) -> list[tuple[str, str, str, str]]:
    """``include_archive=True`` 時に archive 配下の md を集める。

    通常スキャンは archive を ALWAYS_SKIP_DIRS で除外しているため、export だけは
    そこを越えて archive ディレクトリを直接舐める。戻り値は
    (zip_entry, abs_path, project, project_root)。
    """
    entries: list[tuple[str, str, str, str]] = []
    archive_names = {
        (config.archive_dir or "archive").split("/")[-1],
    }
    for t in config.types:
        if t.archive_dir:
            archive_names.add(t.archive_dir.split("/")[-1])
    for root in config.roots:
        root = root.resolve()
        if not root.is_dir():
            continue
        for adir_name in archive_names:
            for ad in root.rglob(adir_name):
                if not ad.is_dir():
                    continue
                # archive の親をプロジェクト境界とみなす（その親フォルダ名を project に）。
                project = ad.parent.name
                project_root = ad.parent
                for md in ad.rglob("*.md"):
                    try:
                        rel = md.relative_to(project_root).as_posix()
                    except ValueError:
                        rel = md.name
                    entries.append((f"{project}/{rel}", str(md), project, str(project_root)))
    return entries


_EXPORT_FRONTMATTER_RE = re.compile(
    r"^---[ \t]*\r?\n(.*?)(?:\r?\n)?---[ \t]*(?:\r?\n|$)", re.DOTALL
)


def _yaml_scalar(value: str) -> str:
    value = str(value)
    if re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _minimal_export_frontmatter(
    *,
    doc_type: str,
    state: str | None,
    profile: OkfProfile,
    status: str | None = None,
    newline: str = "\n",
) -> str:
    lifecycle = status if status and profile.is_lifecycle_status(status) else profile.status_for_state(state)
    lines = ["---", f"type: {_yaml_scalar(doc_type)}", f"status: {_yaml_scalar(lifecycle)}"]
    if state:
        lines.append(f"docsweep_state: {_yaml_scalar(state)}")
    return newline.join(lines) + newline + "---" + newline


def _replace_or_insert_fm_line(inner: str, key: str, line: str) -> str:
    pattern = re.compile(
        rf"^(?P<indent>[ \t]*){re.escape(key)}[ \t]*:.*(?:\r?\n|$)", re.MULTILINE
    )
    match = pattern.search(inner)
    if match:
        newline = "\r\n" if "\r\n" in match.group(0) else "\n"
        return inner[: match.start()] + line + newline + inner[match.end() :]
    newline = "\r\n" if "\r\n" in inner else "\n"
    return line + newline + inner


def _normalize_export_text(
    text: str,
    *,
    doc_type: str,
    state: str | None,
    profile: OkfProfile,
) -> tuple[str, bool]:
    """Make a copy conformant for the Bundle without touching the source file."""
    match = _EXPORT_FRONTMATTER_RE.match(text)
    newline = "\r\n" if "\r\n" in text else "\n"
    if match is None:
        return (
            _minimal_export_frontmatter(
                doc_type=doc_type, state=state, profile=profile, newline=newline
            )
            + text,
            True,
        )
    raw_inner = match.group(1)
    newline = "\r\n" if "\r\n" in text else "\n"
    inner = raw_inner
    if inner and not inner.endswith(("\n", "\r")):
        inner += newline
    try:
        data = yaml.safe_load(raw_inner)
    except yaml.YAMLError as exc:
        raise ValueError("frontmatter の YAML を解析できません") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter の root は mapping である必要があります")

    changed = False
    raw_type = data.get("type")
    if not isinstance(raw_type, str) or not raw_type.strip():
        inner = _replace_or_insert_fm_line(inner, "type", f"type: {_yaml_scalar(doc_type)}")
        changed = True

    raw_status = data.get("status")
    lifecycle = str(raw_status).strip() if raw_status is not None else ""
    if not profile.is_lifecycle_status(lifecycle):
        # Legacy docsweep status values and malformed status values become an
        # OKF lifecycle value in the exported copy.  The work state is retained
        # separately below.
        lifecycle = profile.status_for_state(state)
        inner = _replace_or_insert_fm_line(inner, "status", f"status: {_yaml_scalar(lifecycle)}")
        changed = True
    elif lifecycle != lifecycle.lower():
        inner = _replace_or_insert_fm_line(
            inner, "status", f"status: {_yaml_scalar(lifecycle.lower())}"
        )
        changed = True
    if state and ("docsweep_state" not in data or not str(data.get("docsweep_state") or "").strip()):
        inner = _replace_or_insert_fm_line(inner, "docsweep_state", f"docsweep_state: {_yaml_scalar(state)}")
        changed = True
    if not changed:
        return text, False
    return "---" + newline + inner + "---" + newline + text[match.end() :], True


def collect_export(
    config: Config,
    *,
    project: str | None = None,
    include_archive: bool = False,
    profile: OkfProfile | None = None,
    allow_sensitive: bool = False,
) -> tuple[list[ExportedFile], list[tuple[str, str]]]:
    """エクスポート対象を列挙する（書き出しは別関数）。

    戻り値: (manifest 用エントリ列, (zip_entry, abs_path) 列)
    """
    profile = profile or load_okf_profile()
    result = run_scan(config)
    out_files: list[ExportedFile] = []
    pairs: list[tuple[str, str]] = []

    def is_private_path(path: Path, project_root: Path) -> bool:
        effective = config_for_project(config, project_root)
        work_dir, work_policy, _secret_policy = project_work_settings(project_root, effective)
        if work_policy != "private" or not privacy_enforced(effective):
            return False
        try:
            queue = resolve_work_dir(project_root, work_dir)
            path.resolve().relative_to(queue.resolve())
            return True
        except (OSError, ValueError):
            return False

    for doc in result.docs:
        rec = doc.record
        if project and rec.project != project:
            continue
        if rec.type is None:
            continue
        if rec.sensitive and not allow_sensitive:
            continue
        if is_private_path(Path(rec.path), Path(rec.project_root)) and not allow_sensitive:
            continue
        zip_entry = _zip_entry_path(rec.path, rec.project_root, rec.project)
        state = rec.docsweep_state or rec.state
        status = rec.okf_status if rec.okf_status and profile.is_lifecycle_status(rec.okf_status) else profile.status_for_state(state)
        out_files.append(
            ExportedFile(
                path=zip_entry,
                type=rec.type,
                status=status,
                title=rec.title,
                docsweep_state=state,
                tags=list(rec.tags),
                owner=rec.owner,
                review_status=rec.review_status,
                related=list(rec.related),
                docsweep_parent=rec.docsweep_parent,
                last_reviewed=rec.last_reviewed,
            )
        )
        pairs.append((zip_entry, rec.path))

    if include_archive:
        for zip_entry, abs_path, proj, project_root in _gather_archive_files(config):
            if project and proj != project:
                continue
            if not allow_sensitive:
                archive_path = Path(abs_path)
                try:
                    if is_private_path(archive_path, Path(project_root)):
                        continue
                except OSError:
                    continue
                try:
                    from .secrets_guard import high_confidence_hits, scan_secrets
                    if high_confidence_hits(
                        scan_secrets(archive_path.read_text(encoding="utf-8", errors="replace"))
                    ):
                        continue
                except OSError:
                    continue
            # archive 配下は scan を通らないので、type はファイル名から推測する。
            name = Path(abs_path).name
            doc_type = None
            for prefix in ("plan_", "bugfix_", "pending_"):
                if name.startswith(prefix):
                    doc_type = prefix.rstrip("_")
                    break
            out_files.append(
                ExportedFile(
                    path=f"_archive/{zip_entry}",
                    type=doc_type or "Reference",
                    status="deprecated",
                    title=None,
                    docsweep_state="done",
                )
            )
            pairs.append((f"_archive/{zip_entry}", abs_path))
    return out_files, pairs


def run_export(
    config: Config,
    *,
    out: Path | None = None,
    project: str | None = None,
    include_archive: bool = False,
    okf_version: str = "0.2",
    okf_profile: str | Path | None = None,
    okf_profile_sha256: str | None = None,
    allow_sensitive: bool = False,
) -> ExportResult:
    """``docsweep export --okf`` の本体。zip を実際に書き出す。"""
    out_path = Path(out) if out else _default_out_path()
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    profile = load_okf_profile(
        okf_version,
        source=okf_profile,
        sha256=okf_profile_sha256,
        cache_dir=Path.home() / ".docsweep" / "okf",
    )
    files, pairs = collect_export(
        config,
        project=project,
        include_archive=include_archive,
        profile=profile,
        allow_sensitive=allow_sensitive,
    )

    # Normalize the copy before building the manifest so its ``normalized``
    # flags describe the actual Bundle, not the source files.  The source tree
    # is never written.  UTF-8 is part of the Markdown interchange contract;
    # an undecodable concept is an explicit export error rather than a silent
    # non-conformant entry.
    export_payloads: list[tuple[str, bytes]] = []
    for index, (zip_entry, abs_path) in enumerate(pairs):
        try:
            raw = Path(abs_path).read_bytes()
        except OSError:
            # Keep the historical best-effort behavior for files that vanish
            # during a scan/export race.
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"export 対象が UTF-8 ではありません: {abs_path}") from exc
        entry = files[index]
        try:
            normalized, changed = _normalize_export_text(
                text,
                doc_type=entry.type or "Reference",
                state=entry.docsweep_state,
                profile=profile,
            )
        except ValueError as exc:
            raise ValueError(f"{abs_path}: {exc}") from exc
        entry.normalized = changed
        export_payloads.append((zip_entry, normalized.encode("utf-8")))

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest = _build_manifest(
        files,
        generated_at=generated_at,
        include_archive=include_archive,
        profile=profile,
    )

    # zip 内に重複エントリが入ると後勝ちで上書きされ実体が読めなくなるので、
    # 同じパスが 2 度来たら数字サフィックスでユニーク化する。
    seen: set[str] = set()
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "okf-manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        index_lines = [
            "---",
            f"okf_version: {json.dumps(profile.spec_version, ensure_ascii=False)}",
            "---",
            "",
            "# docsweep export",
            "",
            "## Concepts",
            "",
        ]
        for entry in files:
            index_lines.append(f"* [{entry.path}]({entry.path})")
        zf.writestr("index.md", "\n".join(index_lines) + "\n")
        for zip_entry, payload in export_payloads:
            unique = zip_entry
            n = 1
            while unique in seen:
                stem = Path(zip_entry).stem
                suffix = Path(zip_entry).suffix
                parent = str(Path(zip_entry).parent).replace("\\", "/")
                unique = f"{parent}/{stem}__{n}{suffix}"
                n += 1
            seen.add(unique)
            zf.writestr(unique, payload)

    return ExportResult(
        out_path=out_path.as_posix(),
        file_count=len(files),
        files=files,
        generated_at=generated_at,
        include_archive=include_archive,
        profile=profile.to_dict(),
    )
