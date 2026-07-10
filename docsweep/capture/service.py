"""capture の orchestration: 抽出 + 保存。CLI / Web / MCP がすべてここを呼ぶ。"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from .heuristics import extract_drafts_heuristic
from .llm import LLMRequest, get_llm
from .models import Draft


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
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for d in drafts:
        path = target_dir / d.suggested_filename
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
