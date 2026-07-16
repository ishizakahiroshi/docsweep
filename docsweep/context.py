"""``docsweep context <file>`` — 1 ファイル + related 群を AI 用プロンプトにまとめて吐く。

`docsweep show <file>` の逆参照と同じ ``related.py`` を共有し、本文 / 親 plan の概要 / related
bugfix/pending の要約を 1 文字列に連結する。``--clipboard`` で OS クリップボードへ。

「親 plan」の判定: target が bugfix/pending のときは ``related: [plan_*.md]`` が最初に
見つかる plan、無ければ同名 topic の plan（``plan_<同 topic>.md``）にフォールバック。
target 自身が plan の場合は親 plan は無し（自分が頂点）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .engine import run_scan
from .models import FileRecord
from .related import backref_records, forward_records
from .services.frontmatter import read_frontmatter_text


def _read_text(path: str) -> str:
    try:
        return Path(path).open("r", encoding="utf-8", newline="").read()
    except (OSError, UnicodeDecodeError):
        return ""


def _strip_frontmatter(text: str) -> str:
    """OKF frontmatter を取り除いた本文を返す（context へは本文だけ載せる）。"""
    _data, body = read_frontmatter_text(text)
    return body


def _parent_plan(target: FileRecord, records: list[FileRecord]) -> FileRecord | None:
    """target が bugfix/pending のときに「親 plan」と推定できるレコードを返す。"""
    if target.type == "plan":
        return None
    # 1. forward refs の中から plan を探す
    for r in forward_records(target, records):
        if r.type == "plan":
            return r
    # 2. backref の中から plan を探す（plan 側が related に bugfix を書いているケース）
    for r in backref_records(target, records):
        if r.type == "plan":
            return r
    return None


@dataclass
class ContextBundle:
    target: FileRecord
    parent: FileRecord | None
    related_recs: list[FileRecord]
    backrefs: list[FileRecord]


def collect_context(target_path: str, config: Config) -> ContextBundle:
    """target_path に対する context bundle を組み立てる。"""
    result = run_scan(config)
    records = list(result.records)
    target = next((r for r in records if r.path == target_path), None)
    if target is None:
        # スキャン外の絶対パスでも、ファイル自体が存在すれば最低限のレコードを作る。
        path = Path(target_path)
        if not path.is_file():
            raise FileNotFoundError(target_path)
        raise ValueError(
            f"対象がスキャン範囲外です（--root で範囲を拡張してください）: {target_path}"
        )
    parent = _parent_plan(target, records)
    related_recs = forward_records(target, records)
    backrefs = backref_records(target, records)
    return ContextBundle(
        target=target, parent=parent, related_recs=related_recs, backrefs=backrefs,
    )


def _section_header(label: str, *, fmt: str) -> str:
    if fmt == "markdown":
        return f"\n\n## {label}\n\n"
    return f"\n\n--- {label} ---\n\n"


def render_context(bundle: ContextBundle, *, fmt: str = "markdown") -> str:
    """ContextBundle を 1 つのプロンプト文字列にレンダリングする。"""
    if fmt not in ("markdown", "plain"):
        raise ValueError(f"未知の format: {fmt}")
    out: list[str] = []
    t = bundle.target
    head = (
        f"# 対象: {Path(t.path).name}" if fmt == "markdown"
        else f"対象: {Path(t.path).name}"
    )
    out.append(head)
    out.append(f"パス: {t.path}")
    if t.title:
        out.append(f"タイトル: {t.title}")
    if t.state_label:
        out.append(f"状態: {t.state_label}")

    body = _strip_frontmatter(_read_text(t.path)).strip()
    out.append(_section_header("本文", fmt=fmt) + body)

    if bundle.parent is not None:
        p = bundle.parent
        p_body = _strip_frontmatter(_read_text(p.path)).strip()
        out.append(
            _section_header(f"親 plan: {Path(p.path).name}", fmt=fmt) + p_body
        )

    if bundle.related_recs:
        for r in bundle.related_recs:
            r_body = _strip_frontmatter(_read_text(r.path)).strip()
            out.append(
                _section_header(f"related: {Path(r.path).name}", fmt=fmt) + r_body
            )

    if bundle.backrefs:
        names = ", ".join(Path(r.path).name for r in bundle.backrefs)
        out.append(
            _section_header("逆参照（このファイルを related に挙げているファイル）", fmt=fmt)
            + names
        )

    return "".join(out)


def to_clipboard(text: str) -> bool:
    """OS クリップボードへ書き出す。成功で True / 失敗で False。

    依存を増やさず Windows / macOS / Linux で動く最小実装。
    Windows は ``clip.exe``、macOS は ``pbcopy``、Linux は ``xclip``/``wl-copy``。
    """
    import shutil
    import subprocess
    import sys

    candidates: list[list[str]] = []
    if sys.platform == "win32":
        candidates.append(["clip"])
    elif sys.platform == "darwin":
        candidates.append(["pbcopy"])
    else:
        candidates.append(["wl-copy"])
        candidates.append(["xclip", "-selection", "clipboard"])
    for cmd in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            proc = subprocess.run(
                cmd, input=text.encode("utf-8"), timeout=5, check=False,
            )
            if proc.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False
