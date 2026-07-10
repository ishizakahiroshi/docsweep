"""シナリオ別コピペコマンド集（UX W4 / P68）。"""

from __future__ import annotations

SCENARIOS: dict[str, list[dict[str, str]]] = {
    "morning": [
        {"cmd": "docsweep day open", "why": "朝の儀式: 今日の1個 + overdue"},
        {"cmd": "docsweep brief", "why": "today_pick を断定"},
        {"cmd": "docsweep serve", "why": "看板で捌く"},
    ],
    "release": [
        {"cmd": "docsweep review-week --json", "why": "週次サマリ"},
        {"cmd": "docsweep promote --dry-run", "why": "様子見の昇格候補"},
        {"cmd": "docsweep sweep --dry-run", "why": "完了/廃止の移送確認"},
        {"cmd": "docsweep undo", "why": "誤移送を戻す"},
    ],
    "onboard": [
        {"cmd": "docsweep init --yes", "why": "config 作成"},
        {"cmd": "docsweep index-sync", "why": "索引同期"},
        {"cmd": "docsweep doctor", "why": "環境確認"},
        {"cmd": "docsweep inject --global", "why": "AI 導線"},
        {"cmd": "docsweep brief", "why": "価値到達"},
    ],
    "ai": [
        {"cmd": "docsweep intent \"昨日何やった\"", "why": "意図→コマンド"},
        {"cmd": "docsweep context <file> --clipboard", "why": "AI に渡す"},
        {"cmd": "docsweep triage --head 1", "why": "1件処理ループ"},
        {"cmd": "python -m docsweep mcp", "why": "MCP 起動"},
    ],
    "hygiene": [
        {"cmd": "docsweep project list", "why": "除外状態"},
        {"cmd": "docsweep fix-conflict --list", "why": "H1/FM 食い違い"},
        {"cmd": "docsweep find --q \"認証\"", "why": "本文検索"},
        {"cmd": "docsweep notify --dry-run", "why": "overdue 通知プレビュー"},
    ],
}


def list_scenarios() -> list[str]:
    return sorted(SCENARIOS.keys())


def get_scenario(name: str) -> list[dict[str, str]] | None:
    return SCENARIOS.get(name)


def render_cookbook(name: str | None = None) -> str:
    if name:
        items = SCENARIOS.get(name)
        if not items:
            return f"unknown scenario: {name} (known: {', '.join(list_scenarios())})"
        lines = [f"# cookbook: {name}", ""]
        for it in items:
            lines.append(f"$ {it['cmd']}")
            lines.append(f"  # {it['why']}")
            lines.append("")
        return "\n".join(lines)
    lines = ["# docsweep cookbook", ""]
    for key in list_scenarios():
        lines.append(f"## {key}")
        for it in SCENARIOS[key]:
            lines.append(f"  {it['cmd']}  — {it['why']}")
        lines.append("")
    return "\n".join(lines)
