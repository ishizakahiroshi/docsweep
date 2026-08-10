#!/usr/bin/env python3
"""docsweep pre-commit hook — frontmatter 不整合検知。

採用者が ``install-hooks.sh`` / ``install-hooks.ps1`` で ``.git/hooks/pre-commit`` に
配置することを想定。**docsweep 本体がインストールされていなくても動く**ようフォールバックを
内蔵する（docsweep を入れていないリポでも、frontmatter の値域違反はコミット時に止まる）。

検知対象:

- ``type:`` が空、または文字列ではない
- ``status:`` が OKF lifecycle（draft / stable / deprecated）でも旧 docsweep 値でもない
- ``docsweep_state:`` が docsweep の状態語彙外
- ``review_status:`` が draft / review / published 以外
- ``related:`` で参照される .md が存在しない
- frontmatter の YAML パース失敗
- private work_dir の staged file
- staged file 本文の高信頼 secret（値はエラー出力に含めない）

非 OKF 採用ファイル（frontmatter なし）はスキップ（H1 ラベル運用は触らない）。
plan_* / bugfix_* / pending_* で始まる .md のみを対象にする。
"""

from __future__ import annotations

import re
import subprocess
import sys
import os
from pathlib import Path


ALLOWED_LIFECYCLE_STATUSES = {"draft", "stable", "deprecated"}
LEGACY_STATUSES = {
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
    if not isinstance(doc_type, str) or not doc_type.strip():
        errors.append(
            f"{path}: type は空でない文字列が必要です"
        )

    status = data.get("status")
    if status is not None and status not in (ALLOWED_LIFECYCLE_STATUSES | LEGACY_STATUSES):
        errors.append(
            f"{path}: status={status!r} は OKF lifecycle または旧 docsweep 値ではありません"
        )

    state = data.get("docsweep_state")
    if state is not None and state not in LEGACY_STATUSES:
        errors.append(
            f"{path}: docsweep_state={state!r} は docsweep の状態語彙外です"
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


def _repo_root() -> Path | None:
    try:
        raw = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
            encoding="utf-8",
            errors="replace",
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return Path(raw).resolve() if raw else None


def _staged_paths() -> list[Path]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [Path(line.strip()) for line in out.splitlines() if line.strip()]


def _work_settings(root: Path) -> tuple[str, str, str]:
    path = root / ".docsweep.yaml"
    if not path.is_file():
        return "docs/local", "private", "block"
    try:
        data = _parse_yaml_minimal(path.read_text(encoding="utf-8", errors="replace")) or {}
    except OSError:
        return "docs/local", "private", "block"
    work_dir = str(data.get("work_dir") or "docs/local").strip()
    policy = str(data.get("work_policy") or "private").strip().lower()
    secret_policy = str(data.get("secret_policy") or "block").strip().lower()
    return work_dir, policy, secret_policy


_HIGH_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_pat", re.compile(r"(?:ghp_|github_pat_)[A-Za-z0-9_\-]{20,}")),
    ("anthropic_sk", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_sk", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "generic_bearer",
        re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{24,}"),
    ),
)


def _check_staged_privacy() -> tuple[list[str], list[str]]:
    """private queue / secret を staged diff で検査する。秘密値は出力しない。"""
    root = _repo_root()
    if root is None:
        return [], []
    staged = _staged_paths()
    if not staged:
        return [], []
    work_raw, work_policy, secret_policy = _work_settings(root)
    try:
        work = (root / work_raw).resolve() if not Path(work_raw).is_absolute() else Path(work_raw).resolve()
        work.relative_to(root)
    except ValueError:
        return [".docsweep.yaml: work_dir はプロジェクト相対で指定してください"], []
    except OSError:
        return [".docsweep.yaml: work_dir を解決できません"], []

    errors: list[str] = []
    warnings: list[str] = []
    for rel in staged:
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(work)
            in_work = True
        except ValueError:
            in_work = False
        if in_work and work_policy == "private":
            errors.append(f"{rel.as_posix()}: private work_dir のファイルを staged にできません")
        if secret_policy == "off" or os.environ.get("DOCSWEEP_ALLOW_SENSITIVE") == "1":
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        kinds = [kind for kind, pattern in _HIGH_SECRET_PATTERNS if pattern.search(text)]
        for kind in kinds:
            message = f"{rel.as_posix()}: staged 本文に高信頼 secret ({kind}) を検出"
            if secret_policy == "warn":
                warnings.append(message)
            else:
                errors.append(message)
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        targets = [Path(a) for a in args]
    else:
        targets = _staged_md_files()
    all_errors: list[str] = []
    all_warnings: list[str] = []
    if not args:
        privacy_errors, privacy_warnings = _check_staged_privacy()
        all_errors.extend(privacy_errors)
        all_warnings.extend(privacy_warnings)
    if all_warnings:
        sys.stderr.write("docsweep-check: warning（secret_policy=warn）\n")
        for warning in all_warnings:
            sys.stderr.write(f"  - {warning}\n")
    if not targets and not all_errors:
        return 0
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
