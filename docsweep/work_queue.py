"""作業 queue の配置・Git 境界・書き込み前検査を共有する。"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    DEFAULT_PROJECT_MARKERS,
    Config,
    config_for_project,
    privacy_enforced,
    resolve_work_dir,
)
from .secrets_guard import enforce_secret_policy


class WorkQueueError(PermissionError):
    """作業 queue の配置または Git プライバシー境界に違反した。"""


@dataclass
class WorkQueueCheck:
    path: Path
    project_dir: Path
    policy: str
    ignored: bool | None = None
    tracked: bool | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        """JSON/UI 用。本文や credential 値は含めない。"""
        return {
            "path": self.path.as_posix(),
            "project_dir": self.project_dir.as_posix(),
            "policy": self.policy,
            "ignored": self.ignored,
            "tracked": self.tracked,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "ok": self.ok,
        }


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _under(path: Path, root: Path) -> bool:
    try:
        _absolute(path).relative_to(_absolute(root))
        return True
    except ValueError:
        return False


def _under_real(path: Path, root: Path) -> bool:
    """実体解決後に ``path`` が ``root`` 配下（または root 自体）か調べる。"""
    try:
        child = Path(os.path.normcase(os.path.realpath(os.fspath(path))))
        parent = Path(os.path.normcase(os.path.realpath(os.fspath(root))))
        child.relative_to(parent)
        return True
    except (OSError, ValueError):
        return False


def _same_lexical(path: Path, other: Path) -> bool:
    return os.path.normcase(os.fspath(_absolute(path))) == os.path.normcase(
        os.fspath(_absolute(other))
    )


def _target_realpath_allowed(
    *, project_root: Path, target_dir: Path, queue_root: Path
) -> bool:
    """書き込み target の実体境界を検査する。

    ``queue_root`` 自体は、private queue をリポジトリ外へ置くための junction / symlink
    として正規利用されるので許可する。一方、その配下からさらに別の実体へ抜ける
    symlink は許可しない。queue 外の明示 target は通常どおり project root の実体内だけ
    を許可する。
    """
    if _same_lexical(target_dir, queue_root):
        return True
    if _under(target_dir, queue_root):
        return _under_real(target_dir, queue_root)
    return _under_real(target_dir, project_root)


def _git_available(project_dir: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _git_ignored(project_dir: Path, path: Path) -> bool | None:
    if not _git_available(project_dir):
        return None
    rel = os.path.relpath(str(path), str(project_dir)).replace(os.sep, "/").rstrip("/")

    def _check(target: str) -> int | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(project_dir), "check-ignore", "--no-index", "-q", "--", target],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.returncode

    code = _check(rel)
    if code == 0:
        return True
    if code is None:
        return None
    # ディレクトリ限定パターン（`docs/local/` のように末尾 / 付き）は、対象がまだ
    # **存在しない**と一致しない。git は実在しないパスをディレクトリと判断できないため。
    # 作業 queue の初回作成時はまさにこの状態なので、`.gitignore` が正しく書けているのに
    # 「ignore されていません」で保存が丸ごと止まっていた（新規プロジェクトの 1 本目が作れない）。
    # 末尾 / 付きでもう一度問い合わせる。
    code_dir = _check(f"{rel}/")
    if code_dir == 0:
        return True
    if code_dir is None:
        return None
    if code == 1:
        return False
    return None


def _git_tracked(project_dir: Path, path: Path) -> bool | None:
    if not _git_available(project_dir):
        return None
    rel = os.path.relpath(str(path), str(project_dir)).replace(os.sep, "/").rstrip("/")
    patterns = [rel, f"{rel}/**"]
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir), "ls-files", "-z", "--", *patterns],
            capture_output=True,
            text=False,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout)


def find_project_dir(
    *,
    config: Config | None = None,
    project_dir: Path | None = None,
    project: str | None = None,
    cwd: Path | None = None,
) -> Path:
    """CLI / capture / inject が共有する軽量なプロジェクト境界推定。"""
    if project_dir is not None:
        return _absolute(Path(project_dir))
    if config is not None and config.project_dir is not None:
        return _absolute(config.project_dir)

    here = _absolute(cwd or Path.cwd())
    if project:
        candidates: list[Path] = []
        if config is not None:
            for root in config.roots:
                root_abs = _absolute(root)
                candidates.extend([root_abs, root_abs / project])
                if root_abs.is_dir():
                    candidates.extend(p for p in root_abs.iterdir() if p.is_dir())
        candidates.append(here / project)
        for candidate in candidates:
            if candidate.name == project and candidate.is_dir():
                return _absolute(candidate)

    markers = set(config.project_markers if config is not None else DEFAULT_PROJECT_MARKERS)
    probe = here
    for candidate in (probe, *probe.parents):
        if any((candidate / marker).exists() for marker in markers):
            return candidate
    return here


def resolve_work_target(
    config: Config,
    *,
    project_dir: Path | None = None,
    project: str | None = None,
    cwd: Path | None = None,
    explicit_dir: Path | None = None,
) -> tuple[Path, Path]:
    """``(project_root, queue_or_explicit_target)`` を返す。作成はしない。"""
    root = find_project_dir(config=config, project_dir=project_dir, project=project, cwd=cwd)
    effective = config_for_project(config, root)
    queue_root = resolve_work_dir(root, effective.work_dir)
    target = _absolute(explicit_dir) if explicit_dir is not None else queue_root
    if not _under(target, root):
        raise WorkQueueError(f"作業先はプロジェクト配下である必要があります: {target}")
    if (
        config.roots
        and config.project_dir is None
        and not any(_under(target, Path(r)) for r in config.roots)
    ):
        raise WorkQueueError(f"作業先はスキャン root 配下である必要があります: {target}")
    if not _target_realpath_allowed(
        project_root=root, target_dir=target, queue_root=queue_root
    ):
        raise WorkQueueError(
            "作業先の実体がプロジェクトまたは設定済み queue の範囲外です: "
            f"{target}"
        )
    return root, target


def check_work_queue(
    *,
    config: Config,
    project_dir: Path,
    target_dir: Path,
    content: str | None = None,
    allow_sensitive: bool = False,
) -> WorkQueueCheck:
    """mkdir / write より前に queue の境界と本文ポリシーを検査する。"""
    root = _absolute(project_dir)
    target = _absolute(target_dir)
    policy = (config.work_policy or "private").strip().lower()
    result = WorkQueueCheck(path=target, project_dir=root, policy=policy)
    try:
        effective = config_for_project(config, root)
        queue_root = resolve_work_dir(root, effective.work_dir)
    except (OSError, ValueError):
        queue_root = resolve_work_dir(root, config.work_dir)
    if policy not in {"private", "shared"}:
        result.errors.append("work_policy は private または shared で指定してください")
    if not _under(target, root):
        result.errors.append("作業 queue はプロジェクト配下である必要があります")
    elif not _target_realpath_allowed(
        project_root=root, target_dir=target, queue_root=queue_root
    ):
        result.errors.append(
            "作業 queue の実体がプロジェクトまたは設定済み queue の範囲外です"
        )
    if (
        config.roots
        and config.project_dir is None
        and not any(_under(target, Path(r)) for r in config.roots)
    ):
        result.errors.append("作業 queue はスキャン root 配下である必要があります")

    if policy == "private" and not result.errors:
        result.ignored = _git_ignored(root, target)
        result.tracked = _git_tracked(root, target)
        if result.tracked:
            result.errors.append("private work queue に tracked ファイルが含まれています")
        if result.ignored is False:
            result.errors.append("private work queue が Git ignore されていません")
        elif result.ignored is None:
            result.warnings.append("Git ignore 状態を確認できませんでした")

    if content is not None:
        # enforce_secret_policy は本文を例外・戻り値へ含めない。
        enforce_secret_policy(
            content,
            policy=config.secret_policy,
            allow_sensitive=allow_sensitive,
        )
    return result


def ensure_write_allowed(
    *,
    config: Config,
    project_dir: Path,
    target_dir: Path,
    content: str | None = None,
    allow_sensitive: bool = False,
) -> WorkQueueCheck:
    result = check_work_queue(
        config=config,
        project_dir=project_dir,
        target_dir=target_dir,
        content=content,
        allow_sensitive=allow_sensitive,
    )
    errors = list(result.errors)
    if not privacy_enforced(config):
        downgraded = [
            error for error in errors
            if "private work queue" in error or "private queue" in error
        ]
        errors = [error for error in errors if error not in downgraded]
        # 互換 fallback で error を落とすときは、黙って落とさず warning として残す。
        # 落としたことが見えないと「保護されている」と誤解したまま運用が続く。
        for error in downgraded:
            result.warnings.append(
                f"{error}（work_dir / work_policy が未設定のため警告に留めています。"
                "強制するには .docsweep.yaml で work_policy を明示してください）"
            )
    if errors:
        raise WorkQueueError("; ".join(errors) + f": {result.path}")
    return result
