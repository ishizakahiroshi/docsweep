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
    # session-recap（振り返り）: docs/local に置く記録 md。archive_with_release で release 時に道連れ archive。
    TypeDef("recap", "recap_*.md", (), "", 180),
    # HTML 生成物（design-html / review-sheet skill から）。frontmatter を書けないので
    # 先頭に <!--docsweep-meta ... --> を置いてもらう運用。命名は plan_/bugfix_ と対称:
    # design_<topic>_YYYY-MM-DD.html / mockup_..._.html / review_..._.html / incident_..._.html
    TypeDef("design", "design_*.html", (), "", 180),
    TypeDef("mockup", "mockup_*.html", (), "", 180),
    TypeDef("review-sheet", "review_*.html", (), "", 180),
    TypeDef("incident", "incident_*.html", (), "", 60),
)


DEFAULT_DUE_OFFSET_DAYS: dict[str, int] = {
    "plan": 7,
    "pending": 14,
    "bugfix_watching": 7,
}


# C2: `docsweep stale` のしきい値（review_status 別の経過日数）。``.docsweep.yaml`` の
# ``stale_thresholds:`` ブロックで上書き可能。draft / review は前倒し検知、published は
# 「再レビューが必要になる日数」。
DEFAULT_STALE_THRESHOLDS: dict[str, int] = {
    "draft": 14,
    "review": 7,
    "published": 90,
}


# C1（wings）: SQLite 索引が走査するプロジェクトルートのグロブパターン群。
# ``projects.search_paths`` 未設定なら従来通り ``roots`` を使うフォールバック動作。
DEFAULT_SEARCH_EXCLUDE: tuple[str, ...] = (
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.git/**",
    "**/archive-vault/**",
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
    # 期日（due）まわりの設定。.docsweep.yaml の ``due:`` ブロックから上書き可。
    # 既定: postpone_warn=3 / postpone_alert=5（services/due.py の warning しきい値）。
    # default_offset_days は ``docsweep new`` のテンプレ生成と AI ショートカット用初期値。
    due_warn_threshold: int = 3
    due_alert_threshold: int = 5
    due_default_offset_days: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_DUE_OFFSET_DAYS)
    )
    # C2 で追加: 任意の tag 語彙宣言（補完候補に使う・宣言外は warn する未来拡張用）。
    known_tags: list[str] = field(default_factory=list)
    # C2 で追加: `docsweep stale` の review_status 別しきい値（日数）。
    stale_thresholds: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_STALE_THRESHOLDS)
    )
    # C2 で追加: `docsweep config user.name` / `user.email` のユーザー設定。
    # ``~/.docsweep/config.yaml`` の ``user:`` ブロックから読み、Web UI と CLI で共有する。
    user_name: str | None = None
    user_email: str | None = None
    # C1 (wings): SQLite 索引が再帰走査するルート群のグロブパターン。
    # 例: ["C:/dev/github/public/*", "C:/dev/github/private/*"]
    # 未設定の場合は索引機能は ``roots`` をフォールバック走査する。
    search_paths: list[str] = field(default_factory=list)
    search_exclude: list[str] = field(default_factory=lambda: list(DEFAULT_SEARCH_EXCLUDE))
    # C2 (wings): capture で使う LLM provider 名。現状は "mock" のみ実装済。
    # 実 provider (openai / anthropic) は別 plan で対応。
    capture_llm_provider: str = "mock"
    capture_llm_model: str | None = None  # 将来用（モデル ID 指定）
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


def project_archive_dir(project_dir: Path) -> str | None:
    """プロジェクト直下の .docsweep.yaml から archive_dir だけを読む（無ければ None）。

    sweep / promote は複数プロジェクトを横断するため、起動時に読んだ単一 config では
    各プロジェクトの archive 先の意図を反映できない。移送直前に対象プロジェクト自身の
    設定を参照するための軽量フック（roots 等の他キーはここでは解決しない）。
    壊れた YAML は黙って既定へフォールバックせず例外を伝播させる
    （意図しない場所へのファイル移送を防ぐ）。
    """
    cfg = _load_yaml(project_dir / PROJECT_CONFIG_NAME)
    v = cfg.get("archive_dir")
    return str(v) if v else None


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

    # ``due:`` ブロックは shallow merge ではなくキー単位の deep merge で重ねる。
    # 優先順位: プロジェクト > グローバル > 内蔵 DEFAULT。
    # 例: グローバルで plan=7、プロジェクトで pending=3 だけ設定したい時、
    # 両者の値が共存し片方が片方を巻き添えで消さない（「プロジェクトの方が強い」を
    # 「プロジェクトが書いたキーだけ強い」として正確に表現する）。
    g_due = g.get("due") or {}
    p_due = project_cfg.get("due") or {}
    # 直下の default_offset_days / stale_thresholds が try/except で保護しているのと同じく、
    # ユーザー YAML に文字列や None が入っても load_config を落とさない。落とすと
    # doctor / brief / scan など全コマンドが起動不能になる。
    def _safe_int(value: object, default: int) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    due_warn = _safe_int(
        p_due.get("postpone_warn_threshold", g_due.get("postpone_warn_threshold", 3)),
        3,
    )
    due_alert = _safe_int(
        p_due.get("postpone_alert_threshold", g_due.get("postpone_alert_threshold", 5)),
        5,
    )
    offsets: dict[str, int] = dict(DEFAULT_DUE_OFFSET_DAYS)
    for layer in (g_due.get("default_offset_days"), p_due.get("default_offset_days")):
        if isinstance(layer, dict):
            for k, v in layer.items():
                try:
                    offsets[str(k)] = int(v)
                except (TypeError, ValueError):
                    # 不正な値は前段（global → DEFAULT）の値を温存（嘘の日付を量産しない方針）。
                    pass

    # C2: `known_tags` / `stale_thresholds` も deep merge（project 上書き優先）。
    known_tags_set: list[str] = []
    for layer in (g.get("known_tags"), project_cfg.get("known_tags")):
        if isinstance(layer, list):
            for t in layer:
                s = str(t).strip()
                if s and s not in known_tags_set:
                    known_tags_set.append(s)

    stale_thresholds: dict[str, int] = dict(DEFAULT_STALE_THRESHOLDS)
    for layer in (g.get("stale_thresholds"), project_cfg.get("stale_thresholds")):
        if isinstance(layer, dict):
            for k, v in layer.items():
                try:
                    stale_thresholds[str(k)] = int(v)
                except (TypeError, ValueError):
                    pass

    # C1 (wings): ``projects:`` ブロックは ~/.docsweep/config.yaml の所属。
    # ``search_paths`` (グロブ文字列のリスト) と ``exclude`` (除外グロブ) を読み込む。
    # プロジェクト側で上書きするケースは稀（プロジェクト自身が含まれてしまうため）だが
    # 一応 deep merge する（project が強い）。
    g_proj = g.get("projects") or {}
    p_proj = project_cfg.get("projects") or {}
    search_paths: list[str] = []
    for layer in (g_proj.get("search_paths"), p_proj.get("search_paths")):
        if isinstance(layer, list):
            search_paths = [str(p) for p in layer if p]
    # exclude は積み重ね（DEFAULT に追記する形）。明示空配列が来たらクリアする。
    search_exclude: list[str] = list(DEFAULT_SEARCH_EXCLUDE)
    for layer in (g_proj.get("exclude"), p_proj.get("exclude")):
        if isinstance(layer, list):
            for pat in layer:
                s = str(pat).strip()
                if s and s not in search_exclude:
                    search_exclude.append(s)

    # C2 (wings): ``llm:`` ブロックで capture の LLM provider を指定する。
    # 例: llm: { provider: mock, model: null }。実 provider 追加は別 plan で対応。
    g_llm = g.get("llm") or {}
    p_llm = project_cfg.get("llm") or {}
    capture_llm_provider = "mock"
    capture_llm_model: str | None = None
    for layer in (g_llm, p_llm):
        if not isinstance(layer, dict):
            continue
        if layer.get("provider"):
            capture_llm_provider = str(layer["provider"]).strip() or "mock"
        if layer.get("model"):
            capture_llm_model = str(layer["model"]).strip() or None

    # C2: ``user:`` ブロックは ~/.docsweep/config.yaml にだけ書く想定だが、プロジェクト側で
    # 上書きしたいケースも想定して両方マージする（project が強い）。
    g_user = g.get("user") or {}
    p_user = project_cfg.get("user") or {}
    user_name = None
    user_email = None
    for layer in (g_user, p_user):
        if not isinstance(layer, dict):
            continue
        if layer.get("name"):
            user_name = str(layer["name"]).strip() or None
        if layer.get("email"):
            user_email = str(layer["email"]).strip() or None

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
        due_warn_threshold=due_warn,
        due_alert_threshold=due_alert,
        due_default_offset_days=offsets,
        known_tags=known_tags_set,
        stale_thresholds=stale_thresholds,
        user_name=user_name,
        user_email=user_email,
        search_paths=search_paths,
        search_exclude=search_exclude,
        capture_llm_provider=capture_llm_provider,
        capture_llm_model=capture_llm_model,
        sources=sources,
    )


# ------------------------------------------------------------------
# C2: `docsweep config` CLI / Web UI 共通の user 設定読み書き
# 保存先は ~/.docsweep/config.yaml。``user:`` ブロックだけを単独で更新し、他のキーは温存する。
# ------------------------------------------------------------------


# `docsweep config` で扱える key の許可リスト（typo 防止）。
# ネスト記法 ``user.name`` / ``user.email`` のフラット表現で受ける。
SETTABLE_KEYS: frozenset[str] = frozenset({"user.name", "user.email"})


def get_user_setting(key: str, *, global_path: Path | None = None) -> str | None:
    """``user.name`` / ``user.email`` を ~/.docsweep/config.yaml から読む。

    プロジェクト側の上書きは load_config 経由で見る（こちらはグローバル単体読み出し用）。
    """
    if key not in SETTABLE_KEYS:
        raise ValueError(f"未知の設定キー: {key}（許可: {sorted(SETTABLE_KEYS)}）")
    data = _load_yaml(global_path or GLOBAL_CONFIG_PATH)
    section, name = key.split(".", 1)
    sec = data.get(section) if isinstance(data, dict) else None
    if not isinstance(sec, dict):
        return None
    v = sec.get(name)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def set_user_setting(
    key: str, value: str | None, *, global_path: Path | None = None
) -> Path:
    """``user.name`` / ``user.email`` を ~/.docsweep/config.yaml に書く。

    既存の他キーは温存（YAML 全体を読み込み → ``user:`` セクションだけ書き換え → 全部書き戻す）。
    ``value=None`` でキー削除。書き込み先のパスを返す。
    """
    if key not in SETTABLE_KEYS:
        raise ValueError(f"未知の設定キー: {key}（許可: {sorted(SETTABLE_KEYS)}）")
    path = global_path or GLOBAL_CONFIG_PATH
    data = _load_yaml(path) if path.exists() else {}
    section, name = key.split(".", 1)
    sec = data.get(section) if isinstance(data, dict) else None
    if not isinstance(sec, dict):
        sec = {}
    if value is None or value == "":
        sec.pop(name, None)
    else:
        sec[name] = str(value).strip()
    if sec:
        data[section] = sec
    else:
        data.pop(section, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
    return path


def list_settings(*, global_path: Path | None = None) -> dict[str, str | None]:
    """``--list`` 用の現在値スナップショット（None = 未設定）。"""
    return {k: get_user_setting(k, global_path=global_path) for k in sorted(SETTABLE_KEYS)}
