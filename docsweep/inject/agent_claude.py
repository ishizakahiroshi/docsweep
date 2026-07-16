"""Claude 固有のグローバル guidance 分岐。"""

from __future__ import annotations


def _agent_uses_central(agent: str | None) -> bool:
    """中央 guidance.md を @import で参照する agent か。"""
    return agent == "claude"
