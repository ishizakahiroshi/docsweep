"""シナリオ別コピペコマンド集（UX W4 / P68）。"""

from __future__ import annotations

from dataclasses import dataclass

from .i18n import t


@dataclass(frozen=True)
class Step:
    """1 行分のコマンドと説明。説明（と言語で例が変わるコマンド）は辞書キーで持つ。"""

    cmd: str
    why_key: str
    # 例の引数が言語で変わるコマンド（intent の自然文・find の検索語）は辞書キーで持つ。
    cmd_key: str | None = None

    def render(self) -> dict[str, str]:
        return {
            "cmd": t(self.cmd_key) if self.cmd_key else self.cmd,
            "why": t(self.why_key),
        }


SCENARIOS: dict[str, list[Step]] = {
    "morning": [
        Step("docsweep day open", "cookbook.morning.day_open"),
        Step("docsweep brief", "cookbook.morning.brief"),
        Step("docsweep serve", "cookbook.morning.serve"),
    ],
    "release": [
        Step("docsweep review-week --json", "cookbook.release.review_week"),
        Step("docsweep promote --due-expired --dry-run", "cookbook.release.promote"),
        Step("docsweep sweep --dry-run", "cookbook.release.sweep"),
        Step("docsweep undo", "cookbook.release.undo"),
    ],
    "closeout": [
        Step(
            "python -m docsweep closeout-check --path docs/local/plan_<parent>.md --to watching --json",
            "cookbook.closeout.check",
        ),
        Step(
            "python -m docsweep apply --root . --path docs/local/<child>.md --action relabel --to watching",
            "cookbook.closeout.child",
        ),
        Step(
            "python -m docsweep apply --root . --path docs/local/plan_<parent>.md --action relabel --to watching",
            "cookbook.closeout.parent",
        ),
        Step("python -m docsweep sweep --dry-run --json", "cookbook.closeout.sweep"),
    ],
    "onboard": [
        Step("docsweep init --yes", "cookbook.onboard.init"),
        Step("docsweep index-sync", "cookbook.onboard.index_sync"),
        Step("docsweep doctor", "cookbook.onboard.doctor"),
        Step("docsweep inject --global", "cookbook.onboard.inject"),
        Step("docsweep brief", "cookbook.onboard.brief"),
    ],
    "ai": [
        Step("docsweep intent", "cookbook.ai.intent", cmd_key="cookbook.cmd.intent_example"),
        Step("docsweep context <file> --clipboard", "cookbook.ai.context"),
        Step("docsweep triage --head 1", "cookbook.ai.triage"),
        Step("python -m docsweep mcp", "cookbook.ai.mcp"),
    ],
    "hygiene": [
        Step("docsweep project list", "cookbook.hygiene.project_list"),
        Step("docsweep fix-conflict --list", "cookbook.hygiene.fix_conflict"),
        Step("docsweep find --q", "cookbook.hygiene.find", cmd_key="cookbook.cmd.find_example"),
        Step("docsweep notify --dry-run", "cookbook.hygiene.notify"),
    ],
}


def list_scenarios() -> list[str]:
    return sorted(SCENARIOS.keys())


def get_scenario(name: str) -> list[dict[str, str]] | None:
    steps = SCENARIOS.get(name)
    if steps is None:
        return None
    return [step.render() for step in steps]


def render_cookbook(name: str | None = None) -> str:
    if name:
        items = get_scenario(name)
        if not items:
            return t("cookbook.unknown_scenario", name=name, known=", ".join(list_scenarios()))
        lines = [f"# cookbook: {name}", ""]
        for it in items:
            lines.append(f"$ {it['cmd']}")
            lines.append(f"  # {it['why']}")
            lines.append("")
        return "\n".join(lines)
    lines = ["# docsweep cookbook", ""]
    for key in list_scenarios():
        lines.append(f"## {key}")
        for it in get_scenario(key) or []:
            lines.append(f"  {it['cmd']}  - {it['why']}")
        lines.append("")
    return "\n".join(lines)
