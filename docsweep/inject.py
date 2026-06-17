"""注入（inject）と取り消し（eject）— C7。

各プロジェクトへ書き込むもの:
1. .docsweep.yaml（ツールが読む設定。states/preset）
2. CLAUDE.md / AGENTS.md の管理ブロック（AI が読む運用ルール文。マーカー内だけ書換）

- マーカー内だけ書き換え、外側のユーザー手書きは温存。再注入でユーザー編集を壊さない（冪等）。
- 手編集検出: 管理ブロック内が前回注入時と変わっていたら警告＋.bak バックアップしてから処理。
- マニフェスト ~/.docsweep/injected.json にどのプロジェクトへ注入したかを記録。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .presets import Preset, get_preset
from .states import StateModel

MARK_START = "<!-- docsweep:managed:start -->"
MARK_END = "<!-- docsweep:managed:end -->"
MANIFEST_PATH = Path.home() / ".docsweep" / "injected.json"
DEFAULT_TARGETS = ("CLAUDE.md", "AGENTS.md")

# グローバル注入をサポートする AI ツール。注入先パスは固定せず、各ツールの契約に従って動的解決する
# （Claude=単一ファイル / Codex=CODEX_HOME 相対＋override 優先）。未対応ツールは --global-target で明示。
SUPPORTED_GLOBAL_AGENTS = ("claude", "codex")

# docsweep が所有する中央導線ファイル。実体はここ 1 つに集約し、各ツールには最小フックだけ書く
# （Claude=@import 1 行で取り込み / Codex 等=@import 非対応のため本文をブロック展開）。
GUIDANCE_PATH = Path.home() / ".docsweep" / "guidance.md"
GUIDANCE_IMPORT = "~/.docsweep/guidance.md"  # Claude の @import 行（先頭 ~ は Claude が展開する）


def _codex_home() -> Path:
    """Codex のホーム。CODEX_HOME を尊重し、無ければ ~/.codex（公式仕様準拠）。"""
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def resolve_global_target(agent: str = "claude", target: str | Path | None = None) -> Path:
    """グローバル注入先の絶対パスを解決する（明示 target 優先、無ければ agent ごとの契約で決定）。

    - claude: ``~/.claude/CLAUDE.md``（単一・常に読まれる）
    - codex : ``$CODEX_HOME/AGENTS.md``（既定 ``~/.codex/AGENTS.md``）。
      なお Codex は同階層に ``AGENTS.override.md`` があるとそちらを優先し AGENTS.md を無視するため、
      注入時に override の有無を検査して警告する（``_warn_if_shadowed``）。
    """
    if target:
        return Path(target).expanduser().resolve()
    if agent == "claude":
        return (Path.home() / ".claude" / "CLAUDE.md").resolve()
    if agent == "codex":
        return (_codex_home() / "AGENTS.md").resolve()
    raise ValueError(f"未知の agent: {agent}（claude / codex、または --global-target で明示）")


def _agent_uses_central(agent: str | None) -> bool:
    """中央 guidance.md を @import で参照する agent か（Claude のみ。Codex 等はインライン展開で参照しない）。"""
    return agent == "claude"


def _warn_if_shadowed(path: Path, result: InjectResult | EjectResult, agent: str = "codex") -> None:
    """Codex 系で同階層に AGENTS.override.md があると、注入先が読まれない旨を警告する。

    Claude は override の概念が無いので対象外。Codex は override を最優先し AGENTS.md/フォールバック
    （TEAM_GUIDE.md 等）を無視するため、注入先名を問わず override の存在で警告する。
    """
    if _agent_uses_central(agent):  # claude は対象外
        return
    if path.name != "AGENTS.override.md" and (path.parent / "AGENTS.override.md").is_file():
        result.warnings.append(
            f"同階層に AGENTS.override.md があります。Codex はこちらを優先し {path.name} を読みません。"
            " 導線を効かせるには override 側に取り込むか、--global-target で override を指定してください。"
        )


@dataclass
class InjectResult:
    project: str
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    yaml_path: str | None = None


def _block_hash(inner: str) -> str:
    return hashlib.sha256(inner.strip().encode("utf-8")).hexdigest()[:16]


def _managed_note(eject_cmd: str) -> str:
    """人間が「docsweep が付けた」と一目で分かる注記。最小フック（@import 1 行等）にも必ず添える。"""
    return (
        f"<!-- ⚠ この内容は docsweep inject が自動追加・管理します。"
        f"手で編集せず `{eject_cmd}` で削除してください。 -->"
    )


def _hook_inner(body: str, eject_cmd: str) -> str:
    """注記付きの最小フック本文（マーカーで包む前の inner）を作る。"""
    return _managed_note(eject_cmd) + "\n" + body


def write_guidance_file(lang: str = "ja", *, dry_run: bool = False) -> Path:
    """docsweep 所有の中央導線ファイルを生成・再生成する（直接編集禁止の注記付き）。"""
    content = (
        "<!-- このファイルは docsweep が所有・自動生成します。直接編集しないでください"
        "（`docsweep inject --global` で再生成 / `docsweep eject --global` で参照解除）。 -->\n\n"
        + generate_guidance_block(lang)
        + "\n"
    )
    if not dry_run:
        GUIDANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        GUIDANCE_PATH.write_text(content, encoding="utf-8")
    return GUIDANCE_PATH


def generate_label_block(sm: StateModel, lang: str = "ja", *, use_frontmatter: bool = False) -> str:
    """states から CLAUDE.md のラベル節（プロジェクト固有・状態モデル）を生成する。

    `.docsweep.yaml` の states から導出するためプロジェクトごとに変わる。グローバルには置かない。
    """
    lines: list[str] = [
        "## AI 作業ドキュメントのステータスラベル（docsweep 管理）",
        "",
        "このブロックは `docsweep inject` が生成・同期します。手で編集しないでください",
        "（編集は docsweep eject で剥がしてから）。docsweep は H1 先頭のラベルを読み取り、",
        "完了/廃止を archive へ移送し、陳腐化を要判断フラグで可視化します。",
        "",
        "| 内部状態 | ラベル | 自動 archive |",
        "|---|---|---|",
    ]
    for st in sm.states:
        mark = "✓" if st.archive else "✗"
        note = "" if st.archive else ""
        if st.key == "watching":
            note = "（寝かせ中＝守る）"
        lines.append(f"| {st.key} | `[{st.label(lang)}]` | {mark}{note} |")
    lines += [
        "",
        "- `[" + (sm.by_key('watching').label(lang) if sm.by_key('watching') else '様子見') + "]` は再発確認のため寝かせている状態。**docsweep は自動移送しない**。",
        "- 終端は手の意思決定: 片付いたら `[完了]`、陳腐化は `[廃止]`（削除ではなく archive へ隔離）。",
    ]
    if use_frontmatter:
        lines += [
            "- front matter に `status: <内部状態>` を併記してもよい（検出は frontmatter > H1 > filename）。",
        ]
    return "\n".join(lines)


def generate_guidance_block(lang: str = "ja") -> str:
    """セッション開始時に triage を読む導線（プロジェクト非依存・グローバル注入可）。

    文言は常に同じなので、グローバル（~/.claude/CLAUDE.md 等）に一度入れれば全プロジェクトで効く。
    """
    return "\n".join([
        "## docsweep — セッション開始時の残作業確認（必須）",
        "",
        "作業を始める前に、まず `docsweep triage`（MCP 接続時は `triage` ツール）を実行し、",
        "返ってきた残作業（要判断＋保留・古い順）を確認してから着手すること。",
        "ファイル名や場所を思い出せなくても、これで「次にやるべき作業」が先頭に出る。",
        "ユーザーが「続きやって」と言ったら、triage の先頭 item の `path` と残りを対象にする。",
    ])


def generate_managed_block(
    sm: StateModel, lang: str = "ja", *, use_frontmatter: bool = False, include_guidance: bool = True
) -> str:
    """プロジェクト用の管理ブロック本文（ラベル節＋任意で導線）。

    導線をグローバルへ寄せている場合は ``include_guidance=False`` でラベル節だけにできる（二重化回避）。
    """
    block = generate_label_block(sm, lang, use_frontmatter=use_frontmatter)
    if include_guidance:
        block = block + "\n\n" + generate_guidance_block(lang)
    return block


# プロジェクトの各ターゲットへ書く inner（CLAUDE.md=正本 / AGENTS.md 等=ポインタ）。inject と preview で共有。
_POINTER_BODY = "docsweep の運用ルール（ステータスラベル・残作業導線）は CLAUDE.md の docsweep 管理ブロックを参照してください。"


def _project_inners(sm: StateModel, lang: str, *, use_frontmatter: bool, include_guidance: bool) -> tuple[str, str]:
    claude_inner = generate_managed_block(
        sm, lang, use_frontmatter=use_frontmatter, include_guidance=include_guidance
    )
    pointer_inner = _hook_inner(_POINTER_BODY, "docsweep eject")
    return claude_inner, pointer_inner


def _global_inner(agent: str, lang: str) -> str:
    """グローバル先へ書く最小フック inner。claude=@import 1 行 / その他=本文インライン。inject と preview で共有。"""
    if agent == "claude":
        return _hook_inner(f"@{GUIDANCE_IMPORT}", "docsweep eject --global")
    return _hook_inner(generate_guidance_block(lang), f"docsweep eject --global --agent {agent}")


def _wrap(inner: str) -> str:
    return f"{MARK_START}\n{inner.rstrip()}\n{MARK_END}"


def _find_block(text: str) -> tuple[int, int] | None:
    spans = _find_all_blocks(text)
    return spans[0] if spans else None


def _find_all_blocks(text: str) -> list[tuple[int, int]]:
    """管理ブロック（START..END）を全て列挙する。

    END を START の後ろから探すことで、ユーザー本文に END マーカー文字列が紛れていても
    誤判定しない。複数ブロックがあっても 2 個目以降を取りこぼさない。
    """
    spans: list[tuple[int, int]] = []
    i = 0
    while True:
        s = text.find(MARK_START, i)
        if s == -1:
            break
        e = text.find(MARK_END, s + len(MARK_START))
        if e == -1:
            break
        end = e + len(MARK_END)
        spans.append((s, end))
        i = end
    return spans


def _inner_of(text: str, span: tuple[int, int]) -> str:
    seg = text[span[0]:span[1]]
    return seg[len(MARK_START):-len(MARK_END)].strip()


def load_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        return {"projects": {}}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"projects": {}}


def save_manifest(data: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_managed_file(
    path: Path, inner: str, manifest_entry: dict, result: InjectResult, *, dry_run: bool
) -> None:
    new_block = _wrap(inner)
    rel = path.name
    prev_hash = (manifest_entry.get("blocks") or {}).get(rel)

    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
        spans = _find_all_blocks(text)
        if spans:
            current_inner = _inner_of(text, spans[0])
            # 手編集検出: 前回注入と現在の（先頭）ブロックが食い違うなら .bak を取る。
            if prev_hash and _block_hash(current_inner) != prev_hash:
                result.warnings.append(f"{rel}: 管理ブロックが手編集されています。.bak を作成しました。")
                if not dry_run:
                    path.with_suffix(path.suffix + ".bak").write_text(text, encoding="utf-8")
            if len(spans) > 1:
                result.warnings.append(f"{rel}: 管理ブロックが複数あります。1 つに統合しました。")
            if len(spans) == 1 and _block_hash(current_inner) == _block_hash(inner):
                result.skipped.append(rel)  # 冪等: 変化なし
                (manifest_entry.setdefault("blocks", {}))[rel] = _block_hash(inner)
                return
            # 余剰ブロックを末尾側から除去（先頭オフセットを保つ）→ 先頭を新ブロックへ置換。
            new_text = text
            for sp in reversed(spans[1:]):
                new_text = new_text[:sp[0]] + new_text[sp[1]:]
            new_text = new_text[:spans[0][0]] + new_block + new_text[spans[0][1]:]
        else:
            sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
            new_text = text + sep + new_block + "\n"
    else:
        new_text = new_block + "\n"

    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    result.written.append(rel)
    (manifest_entry.setdefault("blocks", {}))[rel] = _block_hash(inner)


def _strip_managed_blocks(
    path: Path, prev_hash: str | None, result: "EjectResult", *, dry_run: bool
) -> bool:
    """ファイルから全管理ブロックを除去する。手編集は .bak 退避。除去したら True。

    project / global の eject で共有（除去ロジックを 1 か所に集約しドリフトを防ぐ）。
    """
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    spans = _find_all_blocks(text)
    if not spans:
        return False
    # 手編集検出（先頭ブロック基準）。
    if prev_hash and _block_hash(_inner_of(text, spans[0])) != prev_hash:
        result.warnings.append(f"{path.name}: 手編集を検出。.bak を作成しました。")
        if not dry_run:
            path.with_suffix(path.suffix + ".bak").write_text(text, encoding="utf-8")
    # 全ブロックと直前の余分な空行を末尾側から除去（オフセットずれ防止）。
    new_text = text
    for sp in reversed(spans):
        before = new_text[:sp[0]].rstrip("\n")
        after = new_text[sp[1]:].lstrip("\n")
        new_text = before + ("\n\n" if before and after else "") + after
    new_text = new_text.rstrip("\n")
    new_text = new_text + "\n" if new_text else ""
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return True


def inject(
    project_dir: Path,
    *,
    preset: str | None = None,
    targets: tuple[str, ...] = DEFAULT_TARGETS,
    write_yaml: bool = True,
    include_guidance: bool = True,
    dry_run: bool = False,
) -> InjectResult:
    project_dir = project_dir.resolve()
    p: Preset = get_preset(preset)
    sm = p.states
    result = InjectResult(project=project_dir.name)

    manifest = load_manifest()
    key = project_dir.as_posix()
    entry = manifest["projects"].get(key, {"preset": p.name, "blocks": {}, "ts": _now()})
    entry["preset"] = p.name

    # CLAUDE.md = 正本（ラベル節＋導線）。AGENTS.md 等は複製せず CLAUDE.md を指すポインタにする
    # （single source of truth。Codex は AGENTS.md のポインタを読んで CLAUDE.md を参照する）。
    claude_inner, pointer_inner = _project_inners(
        sm, p.lang, use_frontmatter=p.use_frontmatter, include_guidance=include_guidance
    )

    if write_yaml:
        yaml_path = project_dir / ".docsweep.yaml"
        if not yaml_path.exists():
            content = _render_yaml(p)
            if not dry_run:
                yaml_path.write_text(content, encoding="utf-8")
            result.yaml_path = yaml_path.as_posix()
        else:
            result.skipped.append(".docsweep.yaml (既存・温存)")

    for t in targets:
        # CLAUDE.md は常に正本を書く。AGENTS.md は存在する場合のみ、CLAUDE.md を指すポインタを書く。
        path = project_dir / t
        if t != "CLAUDE.md" and not path.is_file():
            continue
        inner = claude_inner if t == "CLAUDE.md" else pointer_inner
        _write_managed_file(path, inner, entry, result, dry_run=dry_run)

    entry["ts"] = _now()
    manifest["projects"][key] = entry
    if not dry_run:
        save_manifest(manifest)
    return result


def _render_yaml(p: Preset) -> str:
    states_block = []
    for st in p.states.states:
        labels = ", ".join(f"{k}: {v}" for k, v in st.labels.items())
        states_block.append(
            f"  - key: {st.key}\n"
            f"    labels: {{ {labels} }}\n"
            f"    archive: {str(st.archive).lower()}\n"
            f"    auto_move: {str(st.auto_move).lower()}"
        )
    return (
        f"# docsweep 設定（preset: {p.name}）\n"
        f"# {p.description}\n"
        f"lang: {p.lang}\n"
        f"preset: {p.name}\n"
        f"states:\n" + "\n".join(states_block) + "\n"
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

    for fname in list((entry.get("blocks") or {}).keys()) or list(DEFAULT_TARGETS):
        path = project_dir / fname
        prev_hash = (entry.get("blocks") or {}).get(fname)
        if _strip_managed_blocks(path, prev_hash, result, dry_run=dry_run):
            result.removed.append(fname)

    if purge:
        yaml_path = project_dir / ".docsweep.yaml"
        if yaml_path.is_file():
            if not dry_run:
                yaml_path.unlink()
            result.purged_yaml = True

    if not dry_run:
        manifest["projects"].pop(key, None)
        save_manifest(manifest)
    return result


def inject_global(
    *, agent: str = "claude", target: str | Path | None = None, lang: str = "ja", dry_run: bool = False
) -> InjectResult:
    """導線（triage を読む行動ルール）のみを AI ツールのグローバル設定へ注入する。

    ラベル節（状態モデル）はプロジェクト固有なのでグローバルには書かない。一度入れれば全プロジェクトで効く。
    """
    path = resolve_global_target(agent, target)
    result = InjectResult(project=f"global:{agent}")
    _warn_if_shadowed(path, result, agent)

    # 実体は docsweep 所有の中央ファイルに集約。各ツールには最小フックだけを注記付きで書く。
    write_guidance_file(lang, dry_run=dry_run)
    inner = _global_inner(agent, lang)

    manifest = load_manifest()
    key = path.as_posix()
    entry = manifest["projects"].get(key, {"scope": "global", "agent": agent, "blocks": {}, "ts": _now()})
    entry["scope"] = "global"
    entry["agent"] = agent

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
    return [
        {
            "project": Path(k).name,
            "path": k,
            "preset": v.get("preset"),
            "scope": v.get("scope", "project"),
            "agent": v.get("agent"),
            "ts": v.get("ts"),
        }
        for k, v in manifest.get("projects", {}).items()
    ]


def preview_inject(project_dir: Path, *, preset: str | None = None, include_guidance: bool = True) -> dict:
    """プロジェクト inject で「何が書かれるか」を返す（書き込みはしない・UI の dry-run プレビュー用）。"""
    project_dir = Path(project_dir).resolve()
    p: Preset = get_preset(preset)
    claude_inner, pointer_inner = _project_inners(
        p.states, p.lang, use_frontmatter=p.use_frontmatter, include_guidance=include_guidance
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


def preview_global(*, agent: str = "claude", target: str | Path | None = None, lang: str = "ja") -> dict:
    """グローバル inject で「何が書かれるか」を返す（書き込みはしない・UI の dry-run プレビュー用）。"""
    path = resolve_global_target(agent, target)
    probe = InjectResult(project=f"global:{agent}")
    _warn_if_shadowed(path, probe, agent)
    return {
        "scope": "global",
        "agent": agent,
        "path": path.as_posix(),
        "blocks": [{"file": path.name, "text": _wrap(_global_inner(agent, lang))}],
        "guidance_path": GUIDANCE_PATH.as_posix(),
        "guidance": generate_guidance_block(lang),
        "warnings": probe.warnings,
    }
