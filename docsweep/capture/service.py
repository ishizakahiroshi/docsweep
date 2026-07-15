"""capture の orchestration: 抽出 + 保存。CLI / Web / MCP がすべてここを呼ぶ。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..config import Config
from .heuristics import extract_drafts_heuristic
from .llm import LLMRequest, get_llm
from .models import Draft


class CaptureScopeError(PermissionError):
    """target_dir が config.roots 配下でない、または suggested_filename が不正なとき発生。

    Web / MCP どちらの capture 経路も外部由来（AI / ユーザー貼り付け）の out_dir と
    suggested_filename を扱うため、書き込み境界を service 層で単一化する（層を上位に
    分散すると片方が抜けたときに任意ファイル書き込みへ発展する）。
    """


_SAFE_FILENAME_RE = re.compile(r"^[\w\-.]+\.md\Z")


def _sanitize_filename(name: str) -> str:
    """``suggested_filename`` を basename のみ・``.md`` のみへ正規化する。

    ``../evil.md`` / ``a/b.md`` / ``foo.txt`` / 空文字 / ``None`` はすべて拒否。
    Windows のディレクトリ区切り ``\\`` も拒否。
    """
    if not name or not isinstance(name, str):
        raise CaptureScopeError(f"suggested_filename が空または不正です: {name!r}")
    # パスセパレータを含む入力は明示拒否（Path.name で吸収せず「意図」を弾く）。
    if "/" in name or "\\" in name or name in (".", ".."):
        raise CaptureScopeError(f"suggested_filename にパスを含めることはできません: {name!r}")
    if not _SAFE_FILENAME_RE.match(name):
        raise CaptureScopeError(
            f"suggested_filename は英数字・ハイフン・アンダースコア・ドットのみで .md 拡張子必須: {name!r}"
        )
    return name


def _target_under_roots(target: Path, roots: list[Path]) -> bool:
    """``target`` が ``roots`` のいずれかの配下（realpath 解決後）であれば True。

    Windows の大小文字差は ``os.path.normcase`` で吸収する。
    """
    try:
        target_real = os.path.realpath(str(target))
    except OSError:
        return False
    tnorm = os.path.normcase(target_real)
    for root in roots:
        try:
            root_real = os.path.realpath(str(root))
        except OSError:
            continue
        rnorm = os.path.normcase(root_real)
        try:
            Path(tnorm).relative_to(Path(rnorm))
            return True
        except ValueError:
            continue
    return False


def extract_drafts(
    text: str,
    *,
    config: Config,
    project: str | None = None,
    max_drafts: int = 5,
    use_llm: bool = False,
) -> list[Draft]:
    """会話履歴 ``text`` から Draft 候補のリストを返す。

    Args:
        text: 会話履歴（クリップボード / ファイル / stdin / Web 貼付け）
        config: ロード済み Config（LLM 設定を参照）
        project: 配置先プロジェクト名（None なら cwd プロジェクト推定）
        max_drafts: 候補上限
        use_llm: True で LLM 経路（config.capture_llm_provider を使う）

    Returns:
        抽出された Draft のリスト（空でも可）。
    """
    if use_llm:
        provider = getattr(config, "capture_llm_provider", None) or "mock"
        client = get_llm(provider)
        request = LLMRequest(
            conversation=text, project_hint=project, max_drafts=max_drafts,
        )
        return client.extract(request)

    return extract_drafts_heuristic(text, project=project, max_drafts=max_drafts)


def save_drafts(
    drafts: list[Draft],
    *,
    config: Config,
    target_dir: Path,
    overwrite: bool = False,
) -> list[Path]:
    """採用された Draft を ``target_dir`` 配下に書き出す。

    Args:
        drafts: 採用された Draft のリスト
        config: ロード済み Config（owner / lang などを参照する余地）
        target_dir: 書き出し先（プロジェクトの ``docs/local/`` を想定）
        overwrite: 既存ファイルを上書きしてよいか

    Returns:
        書き出されたファイルのパスリスト。

    Raises:
        CaptureScopeError: ``target_dir`` が ``config.roots`` 配下でない、または
            いずれかの draft の ``suggested_filename`` が不正（パスセパレータ含む /
            ``.md`` 以外 / 空 / 記号混入）のとき。
    """
    # 書き込み境界: roots が設定されているなら target_dir は必ずその配下でなければならない。
    # roots 未設定の場合（config 無しでの単発呼び出し・テスト等）は cwd フォールバックに委ねる。
    if config.roots and not _target_under_roots(target_dir, config.roots):
        raise CaptureScopeError(
            f"target_dir はスキャンルート配下である必要があります: {target_dir}"
        )
    # basename・.md 限定の filename 検証は全 draft を先に走らせて、部分書き込みを避ける。
    safe_names = [_sanitize_filename(d.suggested_filename) for d in drafts]

    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for d, safe_name in zip(drafts, safe_names, strict=True):
        path = target_dir / safe_name
        if path.exists() and not overwrite:
            # 番号サフィックスで衝突回避
            stem = path.stem
            suffix = path.suffix
            n = 2
            while True:
                cand = path.with_name(f"{stem}_{n}{suffix}")
                if not cand.exists():
                    path = cand
                    break
                n += 1
        try:
            from ..secrets_guard import format_warnings, scan_secrets
            for w in format_warnings(scan_secrets(d.body)):
                import sys
                print(f"warn: {path.name}: {w}", file=sys.stderr)
        except Exception:
            pass
        path.write_text(d.body, encoding="utf-8")
        written.append(path)
    return written
