"""UX W2: day / intent / find --q / fix-conflict."""

from __future__ import annotations

from pathlib import Path

from docsweep.cli import main
from docsweep.config import load_config
from docsweep.day import day_close, day_open
from docsweep.find import FindFilters, find_records
from docsweep.intent import route_intent


def _cfg(root: Path):
    return load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_route_intent_yesterday():
    r = route_intent("昨日何やった？")
    assert r.command == "activity"
    assert "yesterday" in r.args


def test_route_intent_brief():
    r = route_intent("今日の 1 個教えて")
    assert r.command == "brief"


def test_day_open_close(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj"
    _write(
        proj / "plan_hello.md",
        "---\nstatus: planned\ndue: 2099-01-01\n---\n# [計画] hello\n\n## 概要\n\nunique_token_xyz\n",
    )
    cfg = _cfg(root)
    op = day_open(cfg)
    assert op.mode == "open"
    cl = day_close(cfg)
    assert cl.mode == "close"


def test_find_q_body(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj"
    _write(
        proj / "plan_a.md",
        "# [計画] a\n\n## 概要\n\nneedle_auth_token_here\n",
    )
    _write(
        proj / "plan_b.md",
        "# [計画] b\n\n## 概要\n\nother\n",
    )
    cfg = _cfg(root)
    hits = find_records(cfg, FindFilters(q="needle_auth_token"))
    assert len(hits) == 1
    assert hits[0].path.endswith("plan_a.md")


def test_cli_intent_and_day(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj"
    _write(proj / "plan_x.md", "# [計画] x\n\n## 概要\n\nhi\n")
    assert main(["intent", "doctorで診断", "--json"]) == 0
    assert main(["day", "open", "--root", str(root), "--json"]) == 0
