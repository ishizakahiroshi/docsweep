"""Claude/Codex のグローバル注入先解決と Codex override 警告。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .agent_claude import _agent_uses_central


def _codex_home() -> Path:
    """Codex のホーム。CODEX_HOME を尊重し、無ければ ~/.codex。"""
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def resolve_global_target(
    agent: str = "claude", target: str | Path | None = None
) -> Path:
    """明示 target または agent ごとの契約からグローバル注入先を解決する。"""
    if target:
        return Path(target).expanduser().resolve()
    if agent == "claude":
        return (Path.home() / ".claude" / "CLAUDE.md").resolve()
    if agent == "codex":
        return (_codex_home() / "AGENTS.md").resolve()
    raise ValueError(
        f"未知の agent: {agent}（claude / codex、または --global-target で明示）"
    )


def _warn_if_shadowed(path: Path, result: Any, agent: str = "codex") -> None:
    """Codex override が注入先を隠す場合に result.warnings へ通知する。"""
    if _agent_uses_central(agent):
        return
    if path.name != "AGENTS.override.md" and (
        path.parent / "AGENTS.override.md"
    ).is_file():
        result.warnings.append(
            f"同階層に AGENTS.override.md があります。Codex はこちらを優先し {path.name} を読みません。"
            " 導線を効かせるには override 側に取り込むか、--global-target で override を指定してください。"
        )
