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
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import Config
from .engine import run_scan
from .scan import scan_root


# OKF（Zenn 記事 https://zenn.dev/knowledgesense/articles/14a874a9f423bb 準拠）と
# docsweep 固定 type 集合のマッピング。OKF 側の type 語彙は緩いので「最も近い」値を当てる。
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

# docsweep 内部 state key → OKF 互換 status 値。OKF は「draft / active / done / archived」
# あたりの粗い語彙を想定しており、docsweep の `[様子見]` のような細粒度は
# `active` に丸めるしかない（読み手側で OKF 寄せで処理できる粒度に落とす）。
OKF_STATUS_VOCABULARY: dict[str, str] = {
    "planned": "draft",
    "in-progress": "active",
    "watching": "active",
    "done": "done",
    "discarded": "discarded",
    "pending": "deferred",
}

# review_status の値域定義（draft / review / published）。OKF 仕様には review_status の明示が
# 無いので、docsweep が「OSS として宣言する許容値」をここで固定する。
OKF_REVIEW_STATUS_VOCABULARY: list[str] = ["draft", "review", "published"]


@dataclass
class ExportedFile:
    """manifest の files[] に並ぶ 1 件分。"""

    path: str  # zip 内の相対パス（POSIX）
    type: str | None
    status: str | None  # OKF 互換 status
    title: str | None
    tags: list[str] = field(default_factory=list)
    owner: str | None = None
    review_status: str | None = None
    related: list[str] = field(default_factory=list)
    last_reviewed: str | None = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "type": self.type,
            "status": self.status,
            "title": self.title,
            "tags": list(self.tags),
            "owner": self.owner,
            "review_status": self.review_status,
            "related": list(self.related),
            "last_reviewed": self.last_reviewed,
        }


@dataclass
class ExportResult:
    out_path: str
    file_count: int
    files: list[ExportedFile] = field(default_factory=list)
    generated_at: str = ""
    include_archive: bool = False

    def to_dict(self) -> dict:
        return {
            "out_path": self.out_path,
            "file_count": self.file_count,
            "generated_at": self.generated_at,
            "include_archive": self.include_archive,
            "files": [f.to_dict() for f in self.files],
        }


def _default_out_path() -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return Path.cwd() / f"docsweep-okf-{today}.zip"


def _build_manifest(
    files: list[ExportedFile], *, generated_at: str, include_archive: bool
) -> dict:
    """OKF manifest（zip に同梱する JSON）を組み立てる。"""
    return {
        "format": "okf",
        "okf_version": "0.1",
        "docsweep_version": __version__,
        "generated_at": generated_at,
        "include_archive": include_archive,
        "type_vocabulary": OKF_TYPE_VOCABULARY,
        "status_vocabulary": OKF_STATUS_VOCABULARY,
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


def _gather_archive_files(config: Config) -> list[tuple[str, str, str]]:
    """``include_archive=True`` 時に archive 配下の md を集める。

    通常スキャンは archive を ALWAYS_SKIP_DIRS で除外しているため、export だけは
    そこを越えて archive ディレクトリを直接舐める。戻り値は (zip_entry, abs_path, project)。
    """
    entries: list[tuple[str, str, str]] = []
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
                    entries.append((f"{project}/{rel}", str(md), project))
    return entries


def collect_export(
    config: Config,
    *,
    project: str | None = None,
    include_archive: bool = False,
) -> tuple[list[ExportedFile], list[tuple[str, str]]]:
    """エクスポート対象を列挙する（書き出しは別関数）。

    戻り値: (manifest 用エントリ列, (zip_entry, abs_path) 列)
    """
    result = run_scan(config)
    out_files: list[ExportedFile] = []
    pairs: list[tuple[str, str]] = []
    for doc in result.docs:
        rec = doc.record
        if project and rec.project != project:
            continue
        if rec.type is None:
            continue
        zip_entry = _zip_entry_path(rec.path, rec.project_root, rec.project)
        status = OKF_STATUS_VOCABULARY.get(rec.state or "", rec.state) if rec.state else None
        out_files.append(
            ExportedFile(
                path=zip_entry,
                type=rec.type,
                status=status,
                title=rec.title,
                tags=list(rec.tags),
                owner=rec.owner,
                review_status=rec.review_status,
                related=list(rec.related),
                last_reviewed=rec.last_reviewed,
            )
        )
        pairs.append((zip_entry, rec.path))

    if include_archive:
        for zip_entry, abs_path, proj in _gather_archive_files(config):
            if project and proj != project:
                continue
            # archive 配下は scan を通らないので、frontmatter 解析を軽くする（type だけは
            # ファイル名から推測。本文・H1 はバイト保全だけで足りる）。
            name = Path(abs_path).name
            doc_type = None
            for prefix in ("plan_", "bugfix_", "pending_"):
                if name.startswith(prefix):
                    doc_type = prefix.rstrip("_")
                    break
            out_files.append(
                ExportedFile(
                    path=f"_archive/{zip_entry}",
                    type=doc_type,
                    status="archived",
                    title=None,
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
) -> ExportResult:
    """``docsweep export --okf`` の本体。zip を実際に書き出す。"""
    out_path = Path(out) if out else _default_out_path()
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    files, pairs = collect_export(
        config, project=project, include_archive=include_archive
    )
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest = _build_manifest(
        files, generated_at=generated_at, include_archive=include_archive
    )

    # zip 内に重複エントリが入ると後勝ちで上書きされ実体が読めなくなるので、
    # 同じパスが 2 度来たら数字サフィックスでユニーク化する。
    seen: set[str] = set()
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "okf-manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        for zip_entry, abs_path in pairs:
            unique = zip_entry
            n = 1
            while unique in seen:
                stem = Path(zip_entry).stem
                suffix = Path(zip_entry).suffix
                parent = str(Path(zip_entry).parent).replace("\\", "/")
                unique = f"{parent}/{stem}__{n}{suffix}"
                n += 1
            seen.add(unique)
            try:
                with open(abs_path, "rb") as fp:
                    zf.writestr(unique, fp.read())
            except OSError:
                # 読めなかったファイルはスキップ（途中失敗で zip 全体を捨てない）。
                continue

    return ExportResult(
        out_path=out_path.as_posix(),
        file_count=len(files),
        files=files,
        generated_at=generated_at,
        include_archive=include_archive,
    )
