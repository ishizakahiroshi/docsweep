"""設定の層: ① CLI フラグ > ② プロジェクト .docsweep.yaml > ③ グローバル ~/.docsweep/config.yaml。

グローバルだけ書けば体感 1 層。.docsweep.yaml は置いた時だけ部分上書きで効く。
states / types は単一正本で、ここから検出・archive 可否・概要抽出・stale 判定を導出する。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path, PureWindowsPath

import yaml

from .states import StateModel, build_state_model

GLOBAL_CONFIG_PATH = Path.home() / ".docsweep" / "config.yaml"
PROJECT_CONFIG_NAME = ".docsweep.yaml"

# プロジェクト境界マーカー（最寄りの祖先がこれを持てばそこがプロジェクト）。
# 決め打ちのフォルダ構成に依存せず、開発者が既に定義済みの実体で判定する。
DEFAULT_PROJECT_MARKERS = [".git", ".docsweep.yaml", "package.json", "pyproject.toml"]

# AI が作業文書を置く queue。既定値は従来の docs/local/ を維持する。
DEFAULT_WORK_DIR = "docs/local"
WORK_POLICIES = frozenset({"private", "shared"})
SECRET_POLICIES = frozenset({"block", "warn", "off"})
PROVENANCE_MANAGERS = frozenset({"docsweep", "repo", "disabled"})


@dataclass(frozen=True)
class TypeDef:
    """ユーザー定義可能な種別（標準の作業 type と legacy type は内蔵）。"""

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
    # Legacy release records. New releases use plan_release-*; keep this type so historical
    # manual_release-* files remain scannable and archivable during migration.
    TypeDef("manual_release", "manual_release-*.md", (), "", 180),
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
    # 作業文書の配置先。相対値は project_dir 基準で解決する。
    work_dir: str = DEFAULT_WORK_DIR
    work_policy: str = "private"
    secret_policy: str = "block"
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
    # 例: ["D:/dev/github/public/*", "D:/dev/github/private/*"]
    # 未設定の場合は索引機能は ``roots`` をフォールバック走査する。
    search_paths: list[str] = field(default_factory=list)
    search_exclude: list[str] = field(default_factory=lambda: list(DEFAULT_SEARCH_EXCLUDE))
    # C2 (wings): capture で使う LLM provider 名。現状は "mock" のみ実装済。
    # 実 provider (openai / anthropic) は別 plan で対応。
    capture_llm_provider: str = "mock"
    capture_llm_model: str | None = None  # 将来用（モデル ID 指定）
    # AI execution provenance。既定は opt-in で、個人の global config から有効化する。
    # manager=repo は cpni のようにリポ固有台帳・validatorを正典にする明示的な委譲モード。
    provenance_enabled: bool = False
    provenance_manager: str = "disabled"
    provenance_ledger: Path = field(
        default_factory=lambda: GLOBAL_CONFIG_PATH.parent / "provenance" / "ai-executions.csv"
    )
    provenance_project_id: str | None = None
    provenance_actor_key: str | None = None
    provenance_delegate_skill: str | None = None
    # 由来トレース用（どのファイルから来たか）。
    sources: list[Path] = field(default_factory=list)
    # config 層の解決結果を write/archive 側が再利用するためのメタデータ。
    project_dir: Path | None = None
    archive_dir_explicit: bool = False
    work_dir_explicit: bool = False
    work_policy_explicit: bool = False
    loaded_from_config: bool = False

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


def archive_dir_for_project(project_dir: Path, config: Config) -> str:
    """archive 先を決める。

    明示された ``archive_dir`` は最優先で互換維持する。private work queue を明示した
    新規設定では未指定の archive を queue 内へ連動させ、private 文書が公開側の
    ``archive/`` へ抜ける経路を作らない。従来 config（work 設定なし）は ``archive/`` を維持する。
    """
    project_path = Path(project_dir)
    project_cfg = _load_yaml(project_path / PROJECT_CONFIG_NAME)
    explicit = project_cfg.get("archive_dir")
    if explicit:
        return str(explicit)
    if config.archive_dir_explicit:
        return config.archive_dir
    work_dir, work_policy, _secret_policy = project_work_settings(project_path, config)
    if work_policy == "private" and (
        config.work_dir_explicit
        or config.work_policy_explicit
        or "work_dir" in project_cfg
        or "work_policy" in project_cfg
    ):
        archive_root = work_dir.rstrip("/").rstrip("\\")
        return f"{archive_root}/archive"
    return config.archive_dir


def resolve_work_dir(project_dir: Path, work_dir: str | None = None) -> Path:
    """プロジェクト相対の作業 queue を絶対パスへ解決する。

    ``work_dir`` は意図しないプロジェクト外書き込みを防ぐため、絶対パスと ``..`` に
    よる脱出を受け付けない。ここではディレクトリを作成しない。
    """
    root = Path(os.path.abspath(os.path.normpath(os.fspath(project_dir))))
    raw = str(work_dir or DEFAULT_WORK_DIR).strip() or DEFAULT_WORK_DIR
    candidate = Path(os.path.expanduser(raw))
    # ``C:relative`` is not absolute according to pathlib on Windows, but it is
    # drive-qualified and would be resolved against the process drive rather
    # than the project. Treat all Windows drive/UNC forms as invalid here.
    windows_candidate = PureWindowsPath(raw)
    if candidate.is_absolute() or windows_candidate.is_absolute() or windows_candidate.drive:
        raise ValueError("work_dir はプロジェクト相対パスで指定してください")
    target = Path(os.path.abspath(os.path.normpath(os.fspath(root / candidate))))
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("work_dir がプロジェクト外を指しています") from exc
    return target


def relative_work_dir(project_dir: Path, work_dir: str | None = None) -> str:
    """inject / UI 表示用に、解決済み queue を POSIX 相対表記で返す。"""
    root = Path(os.path.abspath(os.path.normpath(os.fspath(project_dir))))
    return resolve_work_dir(root, work_dir).relative_to(root).as_posix()


def project_work_settings(project_dir: Path, config: Config) -> tuple[str, str, str]:
    """プロジェクト設定の work/secret 3 値だけを軽量に解決する。"""
    raw = _load_yaml(Path(project_dir) / PROJECT_CONFIG_NAME)
    work_dir = str(raw.get("work_dir") or config.work_dir or DEFAULT_WORK_DIR).strip()
    raw_work_policy = str(raw.get("work_policy") or config.work_policy or "private").strip().lower()
    raw_secret_policy = str(raw.get("secret_policy") or config.secret_policy or "block").strip().lower()
    return (
        work_dir or DEFAULT_WORK_DIR,
        raw_work_policy if raw_work_policy in WORK_POLICIES else "private",
        raw_secret_policy if raw_secret_policy in SECRET_POLICIES else "block",
    )


def config_for_project(config: Config, project_dir: Path) -> Config:
    """横断処理で選ばれた project の queue 設定を Config に反映する。"""
    work_dir, work_policy, secret_policy = project_work_settings(project_dir, config)
    project_cfg = _load_yaml(Path(project_dir) / PROJECT_CONFIG_NAME)
    return replace(
        config,
        work_dir=work_dir,
        work_policy=work_policy,
        secret_policy=secret_policy,
        project_dir=Path(project_dir).resolve(),
        work_dir_explicit=(config.work_dir_explicit or "work_dir" in project_cfg),
        work_policy_explicit=(config.work_policy_explicit or "work_policy" in project_cfg),
    )


def privacy_enforced(config: Config) -> bool:
    """private queue を保存前に強制する設定が明示されているか。"""
    # load_config の空設定は旧利用者との互換 fallback として警告運用にする。
    # 直接 Config を組み立てる service/API 利用者は明示的な安全既定を受ける。
    return bool(
        config.work_dir_explicit
        or config.work_policy_explicit
        or not config.loaded_from_config
    )


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

    raw_work_dir = merged.get("work_dir") or DEFAULT_WORK_DIR
    work_dir = str(raw_work_dir).strip() or DEFAULT_WORK_DIR
    raw_work_policy = str(merged.get("work_policy") or "private").strip().lower()
    work_policy = raw_work_policy if raw_work_policy in WORK_POLICIES else "private"
    raw_secret_policy = str(merged.get("secret_policy") or "block").strip().lower()
    secret_policy = raw_secret_policy if raw_secret_policy in SECRET_POLICIES else "block"

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

    # AI provenance は global で opt-in し、project が manager / ledger 等を部分上書きする。
    # ledger の相対パスは、値を書いた config ファイルのディレクトリを基準に解決する。
    provenance_enabled = False
    provenance_manager = "disabled"
    provenance_ledger = global_path.parent / "provenance" / "ai-executions.csv"
    provenance_project_id: str | None = None
    provenance_actor_key: str | None = None
    provenance_delegate_skill: str | None = None

    def _safe_bool_setting(value: object, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "1", "on"}:
                return True
            if normalized in {"false", "no", "0", "off"}:
                return False
        return default

    for layer, layer_base in (
        (g.get("provenance") or {}, global_path.parent),
        (project_cfg.get("provenance") or {}, project_dir or Path.cwd()),
    ):
        if not isinstance(layer, dict):
            continue
        if "enabled" in layer:
            provenance_enabled = _safe_bool_setting(layer.get("enabled"), provenance_enabled)
        if layer.get("manager"):
            candidate = str(layer["manager"]).strip().lower()
            if candidate in PROVENANCE_MANAGERS:
                provenance_manager = candidate
        if layer.get("ledger"):
            candidate_path = Path(os.path.expanduser(str(layer["ledger"])))
            provenance_ledger = (
                candidate_path if candidate_path.is_absolute()
                else (Path(layer_base) / candidate_path).resolve()
            )
        if "project_id" in layer:
            provenance_project_id = str(layer.get("project_id") or "").strip() or None
        if "actor_key" in layer:
            provenance_actor_key = str(layer.get("actor_key") or "").strip() or None
        if "delegate_skill" in layer:
            provenance_delegate_skill = str(layer.get("delegate_skill") or "").strip() or None
    if provenance_manager == "disabled":
        provenance_enabled = False

    return Config(
        roots=roots,
        profiles=profiles_resolved,
        archive_dir=merged.get("archive_dir") or "archive",
        work_dir=work_dir,
        work_policy=work_policy,
        secret_policy=secret_policy,
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
        provenance_enabled=provenance_enabled,
        provenance_manager=provenance_manager,
        provenance_ledger=provenance_ledger,
        provenance_project_id=provenance_project_id,
        provenance_actor_key=provenance_actor_key,
        provenance_delegate_skill=provenance_delegate_skill,
        sources=sources,
        project_dir=project_dir.resolve() if project_dir is not None else None,
        archive_dir_explicit=("archive_dir" in g or "archive_dir" in project_cfg),
        work_dir_explicit=("work_dir" in g or "work_dir" in project_cfg),
        work_policy_explicit=("work_policy" in g or "work_policy" in project_cfg),
        loaded_from_config=True,
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
