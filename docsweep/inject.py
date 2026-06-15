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


@dataclass
class InjectResult:
    project: str
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    yaml_path: str | None = None


def _block_hash(inner: str) -> str:
    return hashlib.sha256(inner.strip().encode("utf-8")).hexdigest()[:16]


def generate_managed_block(sm: StateModel, lang: str = "ja", *, use_frontmatter: bool = False) -> str:
    """states から CLAUDE.md のラベル節（マーカー内の本文）を生成する。"""
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


def inject(
    project_dir: Path,
    *,
    preset: str | None = None,
    targets: tuple[str, ...] = DEFAULT_TARGETS,
    write_yaml: bool = True,
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

    inner = generate_managed_block(sm, p.lang, use_frontmatter=p.use_frontmatter)

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
        # CLAUDE.md は常に書く。AGENTS.md は存在する場合のみ更新（薄いポインタ運用を壊さない）。
        path = project_dir / t
        if t != "CLAUDE.md" and not path.is_file():
            continue
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
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        spans = _find_all_blocks(text)
        if not spans:
            continue
        # 手編集検出（先頭ブロック基準）。
        prev_hash = (entry.get("blocks") or {}).get(fname)
        if prev_hash and _block_hash(_inner_of(text, spans[0])) != prev_hash:
            result.warnings.append(f"{fname}: 手編集を検出。.bak を作成しました。")
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


def list_injected() -> list[dict]:
    manifest = load_manifest()
    return [
        {"project": Path(k).name, "path": k, "preset": v.get("preset"), "ts": v.get("ts")}
        for k, v in manifest.get("projects", {}).items()
    ]
