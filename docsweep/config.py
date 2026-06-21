"""設定の層: ① CLI フラグ > ② プロジェクト .docsweep.yaml > ③ グローバル ~/.docsweep/config.yaml。

グローバルだけ書けば体感 1 層。.docsweep.yaml は置いた時だけ部分上書きで効く。
states / types は単一正本で、ここから検出・archive 可否・概要抽出・stale 判定を導出する。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .states import StateModel, build_state_model

GLOBAL_CONFIG_PATH = Path.home() / ".docsweep" / "config.yaml"
PROJECT_CONFIG_NAME = ".docsweep.yaml"

# プロジェクト境界マーカー（最寄りの祖先がこれを持てばそこがプロジェクト）。
# 決め打ちのフォルダ構成に依存せず、開発者が既に定義済みの実体で判定する。
DEFAULT_PROJECT_MARKERS = [".git", ".docsweep.yaml", "package.json", "pyproject.toml"]


@dataclass(frozen=True)
class TypeDef:
    """ユーザー定義可能な種別（plan/bugfix/pending は内蔵デフォルト）。"""

    name: str
    pattern: str  # ファイル名グロブ（例 "plan_*.md"）
    sections: tuple[str, ...]  # 必須セクション見出し（"## " は除いた本文）
    summary_section: str  # 概要抽出に使うセクション名
    stale_days: int
    archive_dir: str | None = None  # type 別 archive 先（None なら全体設定を使う）


DEFAULT_TYPES: tuple[TypeDef, ...] = (
    TypeDef("plan", "plan_*.md", ("概要",), "概要", 90),
    TypeDef("bugfix", "bugfix_*.md", ("症状", "根本原因", "修正内容", "変更ファイル", "検証", "備忘"), "症状", 30),
    TypeDef("pending", "pending_*.md", ("概要", "保留理由", "着手条件"), "概要", 180),
)


@dataclass
class Config:
    roots: list[Path] = field(default_factory=list)
    profiles: dict[str, list[Path]] = field(default_factory=dict)
    archive_dir: str = "archive"
    ignore: list[str] = field(default_factory=list)
    use_gitignore: bool = True
    types: list[TypeDef] = field(default_factory=lambda: list(DEFAULT_TYPES))
    state_model: StateModel = field(default_factory=StateModel)
    project_markers: list[str] = field(default_factory=lambda: list(DEFAULT_PROJECT_MARKERS))
    lang: str = "ja"
    # 由来トレース用（どのファイルから来たか）。
    sources: list[Path] = field(default_factory=list)

    def type_by_name(self, name: str) -> TypeDef | None:
        return next((t for t in self.types if t.name == name), None)

    def match_type(self, filename: str) -> TypeDef | None:
        """ファイル名から type を判定（最初にマッチした定義）。"""
        from fnmatch import fnmatch

        for t in self.types:
            if fnmatch(filename, t.pattern):
                return t
        return None


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} はマップ形式である必要があります")
    return data


def _parse_types(raw: list | None) -> list[TypeDef] | None:
    if not raw:
        return None
    out: list[TypeDef] = []
    for r in raw:
        out.append(
            TypeDef(
                name=r["name"],
                pattern=r["pattern"],
                sections=tuple(r.get("sections") or ()),
                summary_section=r.get("summary_section") or "概要",
                stale_days=int(r.get("stale_days", 90)),
                archive_dir=r.get("archive_dir"),
            )
        )
    return out


def _merge(base: dict, override: dict) -> dict:
    """浅いマージ（部分上書き）。値が None のキーは無視して base を継承する。"""
    out = dict(base)
    for k, v in override.items():
        if v is None:
            continue
        out[k] = v
    return out


def _resolve_roots(values: list[str] | None, base_dir: Path) -> list[Path]:
    roots: list[Path] = []
    for v in values or []:
        p = Path(os.path.expanduser(str(v)))
        if not p.is_absolute():
            p = (base_dir / p).resolve()
        roots.append(p)
    return roots


def load_config(
    *,
    project_dir: Path | None = None,
    explicit_roots: list[str] | None = None,
    profile: str | None = None,
    global_path: Path | None = None,
) -> Config:
    """3 層をマージして Config を返す。

    explicit_roots（位置引数）が来たら config 不要の単発スキャンとして最優先で使う。
    """
    global_path = global_path or GLOBAL_CONFIG_PATH
    sources: list[Path] = []

    g = _load_yaml(global_path)
    if g:
        sources.append(global_path)

    project_cfg: dict = {}
    project_config_path: Path | None = None
    if project_dir is not None:
        project_config_path = project_dir / PROJECT_CONFIG_NAME
        project_cfg = _load_yaml(project_config_path)
        if project_cfg:
            sources.append(project_config_path)

    merged = _merge(g, project_cfg)

    # roots の決定（優先順位: 位置引数 > profile > roots）。
    # 相対パスは「それを定義した config のあるディレクトリ」基準で解決する。プロジェクト
    # .docsweep.yaml の相対値は project_dir、グローバルの相対値は ~/.docsweep/ 基準。
    base_dir = project_dir or Path.cwd()
    g_profiles = g.get("profiles") or {}
    p_profiles = project_cfg.get("profiles") or {}
    if explicit_roots:
        roots = _resolve_roots(explicit_roots, base_dir)
    elif profile:
        if profile in p_profiles:
            roots = _resolve_roots(p_profiles[profile], base_dir)
        elif profile in g_profiles:
            roots = _resolve_roots(g_profiles[profile], global_path.parent)
        else:
            raise ValueError(f"プロファイル '{profile}' が config に見つかりません")
    elif project_cfg.get("roots"):
        roots = _resolve_roots(project_cfg.get("roots"), base_dir)
    else:
        roots = _resolve_roots(g.get("roots"), global_path.parent)

    profiles_resolved = {
        name: _resolve_roots(vals, global_path.parent) for name, vals in g_profiles.items()
    }
    profiles_resolved.update(
        {name: _resolve_roots(vals, base_dir) for name, vals in p_profiles.items()}
    )

    types = _parse_types(merged.get("types")) or list(DEFAULT_TYPES)
    state_model = build_state_model(merged.get("states"))

    return Config(
        roots=roots,
        profiles=profiles_resolved,
        archive_dir=merged.get("archive_dir") or "archive",
        ignore=list(merged.get("ignore") or []),
        use_gitignore=bool(merged.get("use_gitignore", True)),
        types=types,
        state_model=state_model,
        project_markers=list(merged.get("project_markers") or DEFAULT_PROJECT_MARKERS),
        lang=merged.get("lang") or "ja",
        sources=sources,
    )
