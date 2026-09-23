"""注入（inject）と取り消し（eject）— C7。

各プロジェクトへ書き込むもの:
1. .docsweep.yaml（ツールが読む設定。states/preset）
2. CLAUDE.md / AGENTS.md の管理ブロック（AI が読む運用ルール文。マーカー内だけ書換）

- マーカー内だけ書き換え、外側のユーザー手書きは温存。再注入でユーザー編集を壊さない（冪等）。
- 手編集検出: 管理ブロック内が前回注入時と変わっていたら警告＋.bak バックアップしてから処理。
- マニフェスト ~/.docsweep/injected.json にどのプロジェクトへ注入したかを記録。
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path


from ..atomic import write_atomic
from ..config import DEFAULT_DUE_OFFSET_DAYS, GLOBAL_CONFIG_PATH, load_config, relative_work_dir
from ..doc_vocab import heading as doc_heading
from ..i18n import current_lang, t, texts
from ..presets import Preset, get_preset
from ..states import StateModel
from .agent_claude import _agent_uses_central
from .agent_codex import _warn_if_shadowed, resolve_global_target
from .blocks import (
    _block_hash,
    _find_all_blocks,
    _inner_of,
    _private_backup,
    _strip_managed_blocks,
    _wrap,
)
from .manifest import load_manifest, save_manifest

DEFAULT_TARGETS = ("CLAUDE.md", "AGENTS.md")

# グローバル注入をサポートする AI ツール。注入先パスは固定せず、各ツールの契約に従って動的解決する
# （Claude=単一ファイル / Codex=CODEX_HOME 相対＋override 優先）。未対応ツールは --global-target で明示。
SUPPORTED_GLOBAL_AGENTS = ("claude", "codex")

# docsweep が所有する中央導線ファイル。実体はここ 1 つに集約し、各ツールには最小フックだけ書く
# （Claude=@import 1 行で取り込み / Codex 等=@import 非対応のため本文をブロック展開）。
GUIDANCE_PATH = Path.home() / ".docsweep" / "guidance.md"
GUIDANCE_IMPORT = "~/.docsweep/guidance.md"  # Claude の @import 行（先頭 ~ は Claude が展開する）

# グローバル導線ブロック（generate_guidance_block の出力）の改訂版。文言を変えたら手で bump する。
# 注入時にマニフェストへ記録し UI が「どの版が入っているか」を表示する。
GUIDANCE_VERSION = "13"


def _shell_command(parts: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


def docsweep_command(*args: str) -> str:
    """PATH に依存しない docsweep 起動コマンドを返す。

    通常の Python パッケージ実行では現在の Python 実行ファイルから ``-m docsweep`` を呼ぶ。
    PyInstaller 等の単体バイナリでは、そのバイナリ自体を絶対パスで呼ぶ。
    """
    exe = str(Path(sys.executable).resolve())
    if getattr(sys, "frozen", False):
        return _shell_command([exe, *args])
    return _shell_command([exe, "-m", "docsweep", *args])


@dataclass
class InjectResult:
    project: str
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    yaml_path: str | None = None


# 注入する本文の文言は docsweep/i18n/locales/<言語>/inject.json（キーは inject.*）に置く。
# 1 行の文言は文字列、複数行の段落はリスト（1 要素 = 出力の 1 行）で持ち、段落の間の空行・
# 表の区切り行・YAML の骨組みだけをコードが足す。言語は常に呼び出し側が渡す文書の言語
# （``lang``）で引き、表示言語には切り替えない。
def _lines(key: str, lang: str, **params: object) -> list[str]:
    """複数行の段落を ``lang`` で引き、各行のプレースホルダを埋める。

    :func:`t` と同じく各行を必ず ``str.format`` に通す（文字としての波括弧は JSON 側で
    ``{{`` ``}}``）。段落によって使わないプレースホルダがあってもよい（余分な引数は無視される）。
    """
    return [line.format(**params) for line in texts(key, lang=lang)]


def _managed_note(eject_cmd: str, lang: str = "ja") -> str:
    """人間が「docsweep が付けた」と一目で分かる注記。最小フック（@import 1 行等）にも必ず添える。"""
    return t("inject.note.managed", lang=lang, eject_cmd=eject_cmd)


def _hook_inner(body: str, eject_cmd: str, lang: str = "ja") -> str:
    """注記付きの最小フック本文（マーカーで包む前の inner）を作る。"""
    return _managed_note(eject_cmd, lang) + "\n" + body


def write_guidance_file(lang: str = "ja", *, dry_run: bool = False) -> Path:
    """docsweep 所有の中央導線ファイルを生成・再生成する（直接編集禁止の注記付き）。"""
    header = t(
        "inject.guidance_file.header",
        lang=lang,
        inject_cmd=docsweep_command("inject", "--global"),
        eject_cmd=docsweep_command("eject", "--global"),
    )
    content = header + "\n\n" + generate_guidance_block(lang) + "\n"
    if not dry_run:
        write_atomic(GUIDANCE_PATH, content, encoding="utf-8")
    return GUIDANCE_PATH


def generate_label_block(sm: StateModel, lang: str = "ja", *, use_frontmatter: bool = False) -> str:
    """states から CLAUDE.md のラベル節（プロジェクト固有・状態モデル）を生成する。

    `.docsweep.yaml` の states から導出するためプロジェクトごとに変わる。グローバルには置かない。
    """

    def _label(key: str) -> str:
        # 状態モデルに無い状態は、既定の語（terms.json の state.label.<key>）で書く。
        state = sm.by_key(key)
        return state.label(lang) if state else t(f"state.label.{key}", lang=lang)

    lines: list[str] = [
        t("inject.label.heading", lang=lang),
        "",
        *_lines("inject.label.intro", lang, eject_cmd=docsweep_command("eject")),
        "",
        t("inject.label.table_columns", lang=lang),
        "|---|---|---|",
    ]
    for st in sm.states:
        mark = "✓" if st.archive else "✗"
        note = t("inject.label.watching_note", lang=lang) if st.key == "watching" else ""
        lines.append(f"| {st.key} | `[{st.label(lang)}]` | {mark}{note} |")
    lines += [
        "",
        *_lines(
            "inject.label.rules",
            lang,
            watching_label=_label("watching"),
            done_label=_label("done"),
            discarded_label=_label("discarded"),
        ),
    ]
    if use_frontmatter:
        lines.append(t("inject.label.frontmatter_note", lang=lang))
    return "\n".join(lines)


def generate_due_block(lang: str = "ja") -> str:
    """対応期日（``due:``）ルール節を生成する（プロジェクト非依存・導線と一緒に配る）。

    文言は「プロジェクト .docsweep.yaml > グローバル ~/.docsweep/config.yaml > 内蔵 DEFAULT」の
    重なりを説明しており固有情報を含まないため、グローバル注入・プロジェクト注入のどちらでも同文で効く。
    """
    return "\n".join([
        t("inject.due.heading", lang=lang),
        "",
        *_lines(
            "inject.due.body",
            lang,
            new_cmd=docsweep_command("new", "<type>", "<topic>"),
            new_due_cmd=docsweep_command("new", "<type>", "<topic>", "--due", "YYYY-MM-DD"),
            relabel_cmd=docsweep_command(
                "apply", "--action", "relabel", "--to", "watching", "--watching-days", "N"
            ),
            promote_preview_cmd=docsweep_command("promote", "--due-expired", "--dry-run"),
            promote_cmd=docsweep_command("promote", "--due-expired"),
            # 状態モデルを受け取らない共通節なので、既定の語（terms.json）で書く。
            watching_label=t("state.label.watching", lang=lang),
        ),
    ])


def _guidance_owner() -> str:
    """導線へ埋め込む owner の実効値（解決できなければ空文字）。"""
    try:
        from ..services.frontmatter import default_doc_owner

        return default_doc_owner()
    except Exception:  # noqa: BLE001 - 導線生成を owner の解決失敗で止めない
        return ""


def generate_okf_block(
    lang: str = "ja",
    *,
    work_dir: str | None = None,
    work_policy: str | None = None,
    secret_policy: str | None = None,
) -> str:
    """新規 md 作成ルール（OKF frontmatter の注入経路）節を生成する（プロジェクト非依存）。

    OKF frontmatter を注入する経路は ``docsweep new``（templates_gen）だけなので、
    AI が Write 等で手書きすると ``due:`` だけの最小 frontmatter になり OKF が欠落する
    （2026-07-04 に many-ai-cli で実発生）。導線側で「原則 new を使う / 手書き時は
    OKF 一式を必ず入れる」を宣言して穴を塞ぐ。many-ai-cli 等の特定ツールには依存しない。

    ``owner`` は解決済みの値を導線へ埋め込む。「空で書け」とだけ言うと、埋める場面に
    なったときの値が AI の判断になり、リポジトリごとに別表記へ分岐する（同一人物に
    対する 4 表記の並存を実測）。書くべき値そのものを見せれば判断が要らない。
    """
    new_cmd = docsweep_command("new", "<type>", "<topic>")
    migrate_cmd = docsweep_command("migrate-frontmatter", "--apply")
    owner_value = _guidance_owner()
    owner_line = (
        t("inject.okf.owner_set", lang=lang, owner_value=owner_value, new_cmd=new_cmd)
        if owner_value
        else t("inject.okf.owner_unset", lang=lang)
    )
    queue_lines = [
        (
            t("inject.okf.queue_path", lang=lang, work_dir=work_dir)
            if work_dir
            else t("inject.okf.queue_unset", lang=lang)
        ),
        (
            t(
                "inject.okf.queue_policies",
                lang=lang,
                work_policy=work_policy,
                secret_policy=secret_policy,
            )
            if work_dir and (work_policy or secret_policy)
            else t("inject.okf.queue_resolve", lang=lang)
        ),
        t("inject.okf.queue_capture", lang=lang),
    ]
    return "\n".join([
        t("inject.okf.heading", lang=lang),
        "",
        *_lines("inject.okf.intro", lang, new_cmd=new_cmd, owner_value=owner_value),
        owner_line,
        *_lines("inject.okf.other_docs", lang, migrate_cmd=migrate_cmd),
        "",
        *queue_lines,
        t("inject.okf.project_dir", lang=lang),
    ])


def generate_provenance_block(lang: str = "ja") -> str:
    """AI作成者とC単位の実行者を記録する共通導線を生成する。

    global guidance は特定repoの ``provenance.manager`` を固定できないため、AIに実効設定を
    判定させる。repo管理ではdelegateへ委譲し、docsweep管理でだけ汎用台帳を更新することで
    二重記録を防ぐ。global skillは任意の補助であり、CLIだけでも同じ境界を守れる文面にする。
    """
    start_cmd = docsweep_command(
        "provenance",
        "start",
        "--path",
        "<work-md>",
        "--context",
        "<C>",
        "--role",
        "<role>",
        "--json",
    )
    finish_cmd = docsweep_command(
        "provenance",
        "finish",
        "--execution",
        "<AIX-ID>",
        "--result",
        "<result>",
        "--json",
    )
    check_cmd = docsweep_command("provenance", "check", "--path", "<work-md>", "--json")
    return "\n".join([
        t("inject.provenance.heading", lang=lang),
        "",
        *_lines(
            "inject.provenance.body",
            lang,
            start_cmd=start_cmd,
            finish_cmd=finish_cmd,
            check_cmd=check_cmd,
        ),
    ])


def generate_delegation_block(lang: str = "ja") -> str:
    """委譲 plan の C 詳細書式を案内する最小ブロックを生成する。"""
    new_cmd = docsweep_command("new", "plan", "<topic>", "--delegate")
    return "\n".join([
        t("inject.delegation.heading", lang=lang),
        "",
        *_lines(
            "inject.delegation.body",
            lang,
            new_cmd=new_cmd,
            c_details_heading=doc_heading("c_details", lang),
        ),
    ])


def generate_template_sections_block(lang: str = "ja") -> str:
    """プロジェクト固有の本文節を設定・記入する導線を生成する。"""
    new_cmd = docsweep_command("new", "<type>", "<topic>")
    return "\n".join([
        t("inject.template_sections.heading", lang=lang),
        "",
        *_lines("inject.template_sections.intro", lang),
        "",
        "```yaml",
        "template_sections:",
        "  plan:",
        f"    - heading: {t('inject.template_sections.example_heading', lang=lang)}",
        "      body: |",
        f"        {t('inject.template_sections.example_body', lang=lang)}",
        "```",
        *_lines(
            "inject.template_sections.rules",
            lang,
            new_cmd=new_cmd,
            context_heading=doc_heading("context", lang),
            # 英語の文は日本語の文書で使う見出しも併記する（日本語の文では使わない）。
            context_heading_ja=doc_heading("context", "ja"),
        ),
    ])


def generate_guidance_block(
    lang: str = "ja",
    *,
    work_dir: str | None = None,
    work_policy: str | None = None,
    secret_policy: str | None = None,
) -> str:
    """セッション開始・closeout・provenance・due ルール（プロジェクト非依存・グローバル注入可）。

    文言は常に同じなので、グローバル（~/.claude/CLAUDE.md 等）に一度入れれば全プロジェクトで効く。
    closeout / due ルールもプロジェクト非依存のためここに同梱する。
    グローバルに寄せたくない場合はプロジェクト inject（include_guidance=True 既定）で同じ内容が入る。
    """
    brief_cmd = docsweep_command("brief")
    cross_cmd = docsweep_command("cross")
    closeout_cmd = docsweep_command("closeout-check", "--path", "<parent-plan>", "--json")
    return "\n".join([
        t("inject.guidance.heading", lang=lang),
        "",
        *_lines("inject.guidance.session_start", lang, brief_cmd=brief_cmd),
        "",
        *_lines("inject.guidance.continue", lang, cross_cmd=cross_cmd),
        "",
        t("inject.guidance.routes_heading", lang=lang),
        "",
        *_lines("inject.guidance.routes", lang),
        "",
        t("inject.guidance.closeout_heading", lang=lang),
        "",
        *_lines("inject.guidance.closeout", lang, closeout_cmd=closeout_cmd),
        "",
        generate_okf_block(
            lang,
            work_dir=work_dir,
            work_policy=work_policy,
            secret_policy=secret_policy,
        ),
        "",
        generate_provenance_block(lang),
        "",
        generate_delegation_block(lang),
        "",
        generate_template_sections_block(lang),
        "",
        generate_due_block(lang),
    ])


def generate_managed_block(
    sm: StateModel,
    lang: str = "ja",
    *,
    use_frontmatter: bool = False,
    include_guidance: bool = True,
    work_dir: str | None = None,
    work_policy: str | None = None,
    secret_policy: str | None = None,
) -> str:
    """プロジェクト用の管理ブロック本文（ラベル節＋任意で導線）。

    導線をグローバルへ寄せている場合は ``include_guidance=False`` でラベル節だけにできる（二重化回避）。
    """
    block = generate_label_block(sm, lang, use_frontmatter=use_frontmatter)
    if include_guidance:
        block = block + "\n\n" + generate_guidance_block(
            lang,
            work_dir=work_dir,
            work_policy=work_policy,
            secret_policy=secret_policy,
        )
    return block


# プロジェクトの各ターゲットへ書く inner（CLAUDE.md=正本 / AGENTS.md 等=ポインタ）。inject と preview で共有。
def _pointer_body(lang: str) -> str:
    return t("inject.pointer.body", lang=lang)


def _project_inners(
    sm: StateModel,
    lang: str,
    *,
    use_frontmatter: bool,
    include_guidance: bool,
    work_dir: str | None = None,
    work_policy: str | None = None,
    secret_policy: str | None = None,
) -> tuple[str, str]:
    claude_inner = generate_managed_block(
        sm,
        lang,
        use_frontmatter=use_frontmatter,
        include_guidance=include_guidance,
        work_dir=work_dir,
        work_policy=work_policy,
        secret_policy=secret_policy,
    )
    pointer_inner = _hook_inner(_pointer_body(lang), docsweep_command("eject"), lang)
    return claude_inner, pointer_inner


def _global_inner(agent: str, lang: str) -> str:
    """グローバル先へ書く最小フック inner。claude=@import 1 行 / その他=本文インライン。inject と preview で共有。"""
    if agent == "claude":
        return _hook_inner(f"@{GUIDANCE_IMPORT}", docsweep_command("eject", "--global"), lang)
    return _hook_inner(
        generate_guidance_block(lang),
        docsweep_command("eject", "--global", "--agent", agent),
        lang,
    )


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_managed_file(
    path: Path, inner: str, manifest_entry: dict, result: InjectResult, *, dry_run: bool
) -> None:
    raw_block = _wrap(inner)
    rel = path.name
    prev_hash = (manifest_entry.get("blocks") or {}).get(rel)

    if path.is_file():
        # ``Path.read_text`` / ``write_text`` は Windows で改行を暗黙変換する。
        # managed block 外をバイト上も保持するため、既存改行を読み取り時に維持し、
        # 新しい block だけ同じ改行へ合わせる。
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                text = handle.read()
        except UnicodeDecodeError as exc:
            result.warnings.append(t("inject.write_not_utf8", name=rel, reason=exc.reason))
            return
        newline = "\r\n" if "\r\n" in text else "\n"
        new_block = raw_block.replace("\n", newline)
        spans = _find_all_blocks(text)
        if spans:
            current_inner = _inner_of(text, spans[0])
            current_hash = _block_hash(current_inner.replace("\r\n", "\n").replace("\r", "\n"))
            expected_hash = _block_hash(inner)
            # 手編集検出: 前回注入と現在の（先頭）ブロックが食い違うなら private backup を取る。
            if prev_hash and current_hash != prev_hash:
                result.warnings.append(t("inject.block_hand_edited", name=rel))
                if not dry_run:
                    _private_backup(path, path.read_bytes())
            if len(spans) > 1:
                result.warnings.append(t("inject.blocks_merged", name=rel))
            if len(spans) == 1 and current_hash == expected_hash:
                result.skipped.append(rel)  # 冪等: 変化なし
                (manifest_entry.setdefault("blocks", {}))[rel] = _block_hash(inner)
                return
            # 余剰ブロックを末尾側から除去（先頭オフセットを保つ）→ 先頭を新ブロックへ置換。
            new_text = text
            for sp in reversed(spans[1:]):
                new_text = new_text[:sp[0]] + new_text[sp[1]:]
            new_text = new_text[:spans[0][0]] + new_block + new_text[spans[0][1]:]
        else:
            sep = "" if text.endswith(newline * 2) else (newline if text.endswith(newline) else newline * 2)
            new_text = text + sep + new_block + newline
    else:
        newline = os.linesep
        new_text = raw_block.replace("\n", newline) + newline

    if not dry_run:
        write_atomic(path, new_text, encoding="utf-8")
    result.written.append(rel)
    (manifest_entry.setdefault("blocks", {}))[rel] = _block_hash(inner)


def inject(
    project_dir: Path,
    *,
    preset: str | None = None,
    targets: tuple[str, ...] = DEFAULT_TARGETS,
    write_yaml: bool = True,
    include_guidance: bool = True,
    lang: str | None = None,
    dry_run: bool = False,
) -> InjectResult:
    project_dir = project_dir.resolve()
    p: Preset = get_preset(preset)
    effective = load_config(project_dir=project_dir)
    # 注入文の言語: 明示の --lang > そのプロジェクトの文書の言語（設定の lang、無ければ
    # 表示言語）。preset の既定言語（claude-jp は ja）に任せると、英語の利用者にも
    # 日本語のルールが入る（states のラベル辞書は両言語を持つ）。
    lang = lang or effective.document_lang()
    if lang != p.lang:
        p = replace(p, lang=lang)
    sm = p.states
    result = InjectResult(project=project_dir.name)
    effective_work_dir = relative_work_dir(project_dir, effective.work_dir)

    manifest = load_manifest()
    key = project_dir.as_posix()
    entry = manifest["projects"].get(key, {"preset": p.name, "blocks": {}, "ts": _now()})
    entry["preset"] = p.name
    entry["preset_version"] = p.version

    # CLAUDE.md = 正本（ラベル節＋導線）。AGENTS.md 等は複製せず CLAUDE.md を指すポインタにする
    # （single source of truth。Codex は AGENTS.md のポインタを読んで CLAUDE.md を参照する）。
    claude_inner, pointer_inner = _project_inners(
        sm,
        p.lang,
        use_frontmatter=p.use_frontmatter,
        include_guidance=include_guidance,
        work_dir=effective_work_dir,
        work_policy=effective.work_policy,
        secret_policy=effective.secret_policy,
    )

    if write_yaml:
        yaml_path = project_dir / ".docsweep.yaml"
        if not yaml_path.exists():
            content = _render_yaml(
                p,
                work_dir=effective_work_dir,
                work_policy=effective.work_policy,
                secret_policy=effective.secret_policy,
            )
            if not dry_run:
                write_atomic(yaml_path, content, encoding="utf-8")
            result.yaml_path = yaml_path.as_posix()
        else:
            result.skipped.append(t("inject.yaml_kept"))

    for target_name in targets:
        # CLAUDE.md は常に正本を書く。AGENTS.md は存在する場合のみ、CLAUDE.md を指すポインタを書く。
        path = project_dir / target_name
        if target_name != "CLAUDE.md" and not path.is_file():
            continue
        inner = claude_inner if target_name == "CLAUDE.md" else pointer_inner
        _write_managed_file(path, inner, entry, result, dry_run=dry_run)

    entry["ts"] = _now()
    manifest["projects"][key] = entry
    if not dry_run:
        save_manifest(manifest)
    return result


def _ensure_global_config_scaffold(lang: str = "ja", *, dry_run: bool = False) -> bool:
    """``~/.docsweep/config.yaml`` が無ければ due ひな型付きで作る。既存は触らない。

    既存ユーザー設定を上書きしないことを最優先（破壊回避）。新規に作る時のみ、
    全プロジェクト共通の既定として ``due:`` ブロックの書き方が一目で分かる scaffold を置く。
    返り値 True = 新規作成した。False = 既存があったので何もしなかった。
    """
    if GLOBAL_CONFIG_PATH.is_file():
        return False
    header = "\n".join(_lines("inject.global_config.header", lang)) + "\n\n"
    work_scaffold = (
        t("inject.global_config.work_queue", lang=lang) + "\n"
        "# work_dir: docs/local\n"
        "# work_policy: private\n"
        "# secret_policy: block\n\n"
    )
    body = header + work_scaffold + _due_scaffold(scope="global", lang=lang)
    if not dry_run:
        write_atomic(GLOBAL_CONFIG_PATH, body, encoding="utf-8")
    return True


def _due_scaffold(*, scope: str, lang: str = "ja") -> str:
    """``due:`` ブロックのコメントアウト済みひな型を返す（プロジェクト / グローバル共通）。

    既定値そのままを例示するので、コメントを外しただけでは挙動が変わらない（嘘の上書きが起きない）。
    値を編集して初めて反映される。プロジェクト > グローバル > 内蔵 DEFAULT の順で key 単位に重なる。
    """
    intro_key = "inject.due_scaffold.project" if scope == "project" else "inject.due_scaffold.global"
    intro = "\n".join(_lines(intro_key, lang)) + "\n"
    lines = [intro, "# due:", "#   default_offset_days:"]
    for k, v in DEFAULT_DUE_OFFSET_DAYS.items():
        lines.append(f"#     {k}: {v}")
    return "\n".join(lines) + "\n"


def _render_yaml(
    p: Preset,
    *,
    work_dir: str = "docs/local",
    work_policy: str = "private",
    secret_policy: str = "block",
) -> str:
    states_block = []
    for st in p.states.states:
        labels = ", ".join(f"{k}: {v}" for k, v in st.labels.items())
        states_block.append(
            f"  - key: {st.key}\n"
            f"    labels: {{ {labels} }}\n"
            f"    archive: {str(st.archive).lower()}\n"
            f"    auto_move: {str(st.auto_move).lower()}"
        )
    # 設定ファイルの説明コメントもプリセット（＝文書）の言語で書く。
    header = t("inject.yaml.header", lang=p.lang, preset=p.name)
    work_queue_comment = t("inject.yaml.work_queue", lang=p.lang)
    return (
        f"{header}\n"
        f"# {p.description}\n"
        f"lang: {p.lang}\n"
        f"preset: {p.name}\n"
        f"states:\n" + "\n".join(states_block) + "\n\n"
        f"{work_queue_comment}\n"
        f"work_dir: {work_dir}\n"
        f"work_policy: {work_policy}\n"
        f"secret_policy: {secret_policy}\n\n"
        + _due_scaffold(scope="project", lang=p.lang)
    )


@dataclass
class EjectResult:
    project: str
    removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    purged_yaml: bool = False


def eject(project_dir: Path, *, purge: bool = False, dry_run: bool = False) -> EjectResult:
    """管理ブロックだけ削除。ユーザー手書きは温存。--purge で .docsweep.yaml も削除。"""
    project_dir = project_dir.resolve()
    result = EjectResult(project=project_dir.name)
    manifest = load_manifest()
    key = project_dir.as_posix()
    entry = manifest["projects"].get(key, {"blocks": {}})
    # UTF-8 として読めなかったファイル名。警告の文言（表示言語で変わる）では判定しない。
    decode_failures: list[str] = []

    for fname in list((entry.get("blocks") or {}).keys()) or list(DEFAULT_TARGETS):
        path = project_dir / fname
        prev_hash = (entry.get("blocks") or {}).get(fname)
        if _strip_managed_blocks(
            path, prev_hash, result, dry_run=dry_run, decode_failures=decode_failures
        ):
            result.removed.append(fname)

    if purge:
        yaml_path = project_dir / ".docsweep.yaml"
        if decode_failures:
            result.warnings.append(t("inject.purge_not_utf8"))
        elif yaml_path.is_file():
            if not dry_run:
                yaml_path.unlink()
            result.purged_yaml = True

    if not dry_run:
        manifest["projects"].pop(key, None)
        save_manifest(manifest)
    return result


def inject_global(
    *,
    agent: str = "claude",
    target: str | Path | None = None,
    lang: str | None = None,
    dry_run: bool = False,
) -> InjectResult:
    """導線（triage を読む行動ルール）のみを AI ツールのグローバル設定へ注入する。

    ラベル節（状態モデル）はプロジェクト固有なのでグローバルには書かない。一度入れれば全プロジェクトで効く。
    ``lang`` を省くと表示言語（明示した global の lang を含む）で書く。
    """
    lang = lang or current_lang()
    path = resolve_global_target(agent, target)
    result = InjectResult(project=f"global:{agent}")
    _warn_if_shadowed(path, result, agent)

    # 中央 guidance.md は @import で参照する agent（claude）のときだけ生成する。
    # Codex 等は導線本文をインライン展開し中央ファイルを参照しないので、作ると誰も読まない
    # 孤児になる（eject 側の保持判定とも整合: guidance.md は Claude が居る時だけ保持）。
    if _agent_uses_central(agent):
        write_guidance_file(lang, dry_run=dry_run)
    # docsweep 自身のグローバル設定 (~/.docsweep/config.yaml) のひな型を「未存在のときだけ」作る。
    # 既存ユーザー設定には触らない。due.default_offset_days を全プロジェクト共通の既定として
    # ここに書いてもらえるよう、コメントアウト済みのブロックを提示する。
    if _ensure_global_config_scaffold(lang, dry_run=dry_run):
        # 画面に出す報告なので表示言語で出す（lang は書き込む本文の言語）
        result.warnings.append(t("inject.global_config_scaffolded"))
    inner = _global_inner(agent, lang)

    manifest = load_manifest()
    key = path.as_posix()
    entry = manifest["projects"].get(key, {"scope": "global", "agent": agent, "blocks": {}, "ts": _now()})
    entry["scope"] = "global"
    entry["agent"] = agent
    entry["guidance_version"] = GUIDANCE_VERSION

    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
    _write_managed_file(path, inner, entry, result, dry_run=dry_run)

    entry["ts"] = _now()
    manifest["projects"][key] = entry
    if not dry_run:
        save_manifest(manifest)
    return result


def eject_global(
    *, agent: str = "claude", target: str | Path | None = None, dry_run: bool = False
) -> EjectResult:
    """グローバルへ注入した導線ブロックを剥がす（project eject と除去ロジックを共有）。"""
    path = resolve_global_target(agent, target)
    result = EjectResult(project=f"global:{agent}")
    manifest = load_manifest()
    key = path.as_posix()
    entry = manifest["projects"].get(key, {"blocks": {}})
    prev_hash = (entry.get("blocks") or {}).get(path.name)
    if _strip_managed_blocks(path, prev_hash, result, dry_run=dry_run):
        result.removed.append(path.name)
    if not dry_run:
        manifest["projects"].pop(key, None)
        # 中央 guidance.md を @import 参照する global（=claude）が他に残っていなければ撤去する。
        # Codex はインライン展開で guidance.md を参照しないので、残っていても保持理由にならない。
        still_referenced = any(
            v.get("scope") == "global" and _agent_uses_central(v.get("agent"))
            for v in manifest["projects"].values()
        )
        if not still_referenced and GUIDANCE_PATH.is_file():
            GUIDANCE_PATH.unlink()
            result.removed.append(GUIDANCE_PATH.name)
        save_manifest(manifest)
    return result


def list_injected() -> list[dict]:
    manifest = load_manifest()
    items = []
    for k, v in manifest.get("projects", {}).items():
        scope = v.get("scope", "project")
        # scope=project は preset_version、scope=global は guidance_version を共通の "version" として返す
        # （UI 側は scope を見ずに version 列を描画できる）。古いマニフェストで欠けていれば None。
        version = v.get("preset_version") if scope == "project" else v.get("guidance_version")
        items.append({
            "project": Path(k).name,
            "path": k,
            "preset": v.get("preset"),
            "scope": scope,
            "agent": v.get("agent"),
            "ts": v.get("ts"),
            "version": version,
        })
    return items


def preview_inject(
    project_dir: Path, *, preset: str | None = None, include_guidance: bool = True, lang: str | None = None
) -> dict:
    """プロジェクト inject で「何が書かれるか」を返す（書き込みはしない・UI の dry-run プレビュー用）。"""
    project_dir = Path(project_dir).resolve()
    p: Preset = get_preset(preset)
    effective = load_config(project_dir=project_dir)
    claude_inner, pointer_inner = _project_inners(
        p.states,
        lang or effective.document_lang(),
        use_frontmatter=p.use_frontmatter,
        include_guidance=include_guidance,
        work_dir=relative_work_dir(project_dir, effective.work_dir),
        work_policy=effective.work_policy,
        secret_policy=effective.secret_policy,
    )
    blocks = [{"file": "CLAUDE.md", "text": _wrap(claude_inner)}]
    if (project_dir / "AGENTS.md").is_file():
        blocks.append({"file": "AGENTS.md", "text": _wrap(pointer_inner)})
    return {
        "scope": "project",
        "project": project_dir.name,
        "path": project_dir.as_posix(),
        "blocks": blocks,
        "yaml_exists": (project_dir / ".docsweep.yaml").is_file(),
    }


def preview_global(
    *, agent: str = "claude", target: str | Path | None = None, lang: str | None = None
) -> dict:
    """グローバル inject で「何が書かれるか」を返す（書き込みはしない・UI の dry-run プレビュー用）。"""
    lang = lang or current_lang()
    path = resolve_global_target(agent, target)
    probe = InjectResult(project=f"global:{agent}")
    _warn_if_shadowed(path, probe, agent)
    # 中央ファイルは @import 参照する agent（claude）でのみ生成・参照される。Codex 等は
    # 導線をインライン展開するので、プレビューでも中央ファイルの行を見せない（誤誘導を防ぐ）。
    uses_central = _agent_uses_central(agent)
    return {
        "scope": "global",
        "agent": agent,
        "path": path.as_posix(),
        "blocks": [{"file": path.name, "text": _wrap(_global_inner(agent, lang))}],
        "guidance_path": GUIDANCE_PATH.as_posix() if uses_central else None,
        "guidance": generate_guidance_block(lang) if uses_central else None,
        "warnings": probe.warnings,
    }
