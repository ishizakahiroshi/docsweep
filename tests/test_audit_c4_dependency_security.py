"""監査 C4: dependency floor と audit workflow の契約。"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def test_jinja_security_floor_and_existing_dependency_floors_are_explicit():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"jinja2>=3.1.6"' in text
    assert '"starlette>=1.3.1"' in text
    assert '"python-multipart>=0.0.30"' in text


def test_installed_jinja_is_not_below_security_floor():
    try:
        installed = version("Jinja2")
    except PackageNotFoundError:
        pytest.skip("web extra is not installed in this environment")
    assert tuple(int(part) for part in installed.split(".")[:3]) >= (3, 1, 6)


def test_dependency_audit_workflow_is_scheduled_manual_and_read_only():
    path = ROOT / ".github" / "workflows" / "dependency-audit.yml"
    text = path.read_text(encoding="utf-8")

    assert "schedule:" in text
    assert "workflow_dispatch:" in text
    assert "permissions:\n  contents: read" in text
    assert "actions/checkout@v4" in text
    assert "actions/setup-python@v5" in text
    assert "ubuntu-24.04" in text
    assert "pip install -e \".[web,mcp,review]\"" in text
    assert "pip-audit --strict" in text
    assert "continue-on-error:" not in text
    assert "contents: write" not in text


def test_dependency_audit_fixtures_pin_clean_and_vulnerable_inputs():
    fixture_dir = ROOT / "tests" / "fixtures" / "dependency-audit"
    vulnerable = (fixture_dir / "vulnerable.txt").read_text(encoding="utf-8").strip()
    clean = (fixture_dir / "clean.txt").read_text(encoding="utf-8").strip()

    assert vulnerable == "Jinja2==3.1.2"
    assert clean == "Jinja2==3.1.6"
    workflow = (ROOT / ".github" / "workflows" / "dependency-audit.yml").read_text(
        encoding="utf-8"
    )
    assert "vulnerable.txt --no-deps" in workflow
    assert "clean.txt --no-deps" in workflow
