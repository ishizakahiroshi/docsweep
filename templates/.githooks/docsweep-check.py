#!/usr/bin/env python3
"""docsweep pre-commit hook — frontmatter 不整合検知。

採用者が ``install-hooks.sh`` / ``install-hooks.ps1`` で ``.git/hooks/pre-commit`` に
配置することを想定。**docsweep 本体がインストールされていなくても動く**ようフォールバックを
内蔵する（docsweep を入れていないリポでも、frontmatter の値域違反はコミット時に止まる）。

検知対象:

- ``type:`` が plan / bugfix / pending / その他 docsweep が知らない値（許容）以外
- ``status:`` が許容値域外（planned / in-progress / watching / done / discarded / pending）
- ``review_status:`` が draft / review / published 以外
- ``related:`` で参照される .md が存在しない
- frontmatter の YAML パース失敗

非 OKF 採用ファイル（frontmatter なし）はスキップ（H1 ラベル運用は触らない）。
plan_* / bugfix_* / pending_* で始まる .md のみを対象にする。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ALLOWED_TYPES = {"plan", "bugfix", "pending"}
ALLOWED_STATUSES = {
    "planned", "in-progress", "watching", "done", "discarded", "pending",
}
ALLOWED_REVIEW_STATUSES = {"draft", "review", "published"}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _staged_md_files() -> list[Path]:
    """``git diff --cached --name-only --diff-filter=AM`` で対象 md を取得。"""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
            text=True, encoding="utf-8", errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    files: list[Path] = []
    for line in out.splitlines():
        line = line.strip()
        if not line.endswith(".md"):
            continue
        name = Path(line).name
        if not any(
            name.startswith(prefix) for prefix in ("plan_", "bugfix_", "pending_")
        ):
            continue
        p = Path(line)
        if p.is_file():
            files.append(p)
    return files


def _parse_yaml_minimal(text: str) -> dict | None:
    """yaml.safe_load を試し、無ければ最小 parser でフォールバック。

    フォールバックは ``key: value`` / ``key: [a, b]`` の 2 形式のみ扱う。
    """
    try:
        import yaml  # type: ignore[import-not-found]
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
        return None
    except ImportError:
        pass
    out: dict = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            items = [s.strip().strip("'\"") for s in inner.split(",") if s.strip()]
            out[key] = items
        elif raw == "":
            out[key] = None
        else:
            out[key] = raw.strip("'\"")
    return out


def _check_one(path: Path) -> list[str]:
    """1 ファイルを検査してエラー文字列のリストを返す（空なら OK）。"""
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [f"{path}: 読み取り失敗: {e}"]

    m = _FRONTMATTER_RE.match(text)
    if not m:
        # frontmatter 無し（旧来の H1 ラベル運用）は対象外。
        return []
    body = m.group(1)
    data = _parse_yaml_minimal(body)
    if data is None:
        return [f"{path}: frontmatter の YAML パースに失敗しました"]

    doc_type = data.get("type")
    if doc_type is not None and doc_type not in ALLOWED_TYPES:
        errors.append(
            f"{path}: type={doc_type!r} は許容外（{sorted(ALLOWED_TYPES)} のみ）"
        )

    status = data.get("status")
    if status is not None and status not in ALLOWED_STATUSES:
        errors.append(
            f"{path}: status={status!r} は許容外（{sorted(ALLOWED_STATUSES)} のみ）"
        )

    review = data.get("review_status")
    if review is not None and review not in ALLOWED_REVIEW_STATUSES:
        errors.append(
            f"{path}: review_status={review!r} は許容外"
            f"（{sorted(ALLOWED_REVIEW_STATUSES)} のみ）"
        )

    related = data.get("related") or []
    if not isinstance(related, list):
        errors.append(f"{path}: related は list 型である必要があります")
    else:
        base = path.parent
        for ref in related:
            if not ref:
                continue
            ref_s = str(ref).strip()
            # 絶対パス or 相対パス（path 隣接 or リポルート相対）両対応の探索。
            candidates = [
                base / ref_s,
                Path(ref_s),
                Path.cwd() / ref_s,
            ]
            if not any(c.is_file() for c in candidates):
                errors.append(
                    f"{path}: related に存在しない md があります: {ref_s!r}"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        targets = [Path(a) for a in args]
    else:
        targets = _staged_md_files()
    if not targets:
        return 0
    all_errors: list[str] = []
    for p in targets:
        all_errors.extend(_check_one(p))
    if not all_errors:
        return 0
    sys.stderr.write("docsweep-check: frontmatter 不整合を検出しました\n")
    for e in all_errors:
        sys.stderr.write(f"  - {e}\n")
    sys.stderr.write(
        "\n修正してから再度 git commit してください。"
        "（hook を一時的に外すには git commit --no-verify）\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
