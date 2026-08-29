"""監査 2026-08-23 C1（security・書き込み境界）の回帰のうち、否定側の判定。

C1 子 plan の決定的回帰は「各 secret 形式が kind だけで block され、**似た通常文は
block しない**」を求めている。肯定側（各形式を検出すること）は
``tests/test_audit_v0_4_0.py`` が持つが、否定側はどこにも無かった。

実際、``openai_sk`` は先頭の境界表明が無いため
``task-management-and-scheduling-system`` を ``ta|sk-...`` として拾い、秘密を
1 文字も含まない作業文書の書き込みを拒否していた。検出漏れと違って**利用者から
見えるのは「保存できない」だけ**で、原因が秘密ガードだと分かりにくい。
"""

from __future__ import annotations

import pytest

from docsweep.secrets_guard import (
    SensitiveContentError,
    enforce_secret_policy,
    high_confidence_hits,
    scan_secrets,
)


def _kinds(text: str) -> set[str]:
    return {h["kind"] for h in high_confidence_hits(scan_secrets(text))}


# 秘密を含まない、作業 md に実際に出てくる形の文。
ORDINARY_TEXTS = [
    "task-management-and-scheduling-system",
    "risk-assessment-and-mitigation-plan を参照する",
    "branch: feature/task-runner-refactor-and-cleanup",
    "desk-layout-and-seating-arrangement",
    "password: see the shared vault entry",
    "Authorization: Bearer TOKEN_PLACEHOLDER",
    "aws_secret_access_key = <redacted>",
    "この文書は token と secret の扱いを説明する",
    "eyJ で始まる文字列は JWT かもしれない",
]


@pytest.mark.parametrize("text", ORDINARY_TEXTS)
def test_ordinary_prose_is_not_flagged_as_a_credential(text: str) -> None:
    assert _kinds(text) == set(), text


@pytest.mark.parametrize("text", ORDINARY_TEXTS)
def test_ordinary_prose_is_writable_under_the_block_policy(text: str) -> None:
    assert enforce_secret_policy(text, policy="block") == []


@pytest.mark.parametrize(
    "text",
    [
        "key: sk-proj-" + "A" * 30,
        "key: sk-svcacct-" + "B" * 30,
        "key: sk-" + "C" * 30,
        '"sk-proj-' + "D" * 30 + '"',
        "OPENAI_API_KEY=sk-proj-" + "E" * 30,
    ],
)
def test_real_openai_key_shapes_still_block(text: str) -> None:
    assert "openai_sk" in _kinds(text)
    with pytest.raises(SensitiveContentError):
        enforce_secret_policy(text, policy="block")
