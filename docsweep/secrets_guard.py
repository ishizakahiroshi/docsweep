"""作業文書の秘密情報ガード。

書き込み前に高信頼の credential を拒否し、低信頼の候補は警告する。検出結果を
ログ・JSON・UI へ返すときは、値の一部を含む ``sample`` を決して持ち回らない。
"""

from __future__ import annotations

import re


class SensitiveContentError(PermissionError):
    """秘密情報ポリシーにより本文の保存・表示を拒否した。"""


_HIGH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("github_fine_grained", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("anthropic_sk", re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}")),
    ("openai_sk", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    (
        "generic_bearer",
        re.compile(
            r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{24,}"
        ),
    ),
    (
        "private_key_block",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
]

# 長さだけでは credential と断定できない設定値。high と同じく本文を返さず kind だけ出す。
_LOW_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "possible_secret_assignment",
        re.compile(
            r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,23}"
        ),
    ),
]


def scan_secrets(text: str) -> list[dict]:
    """検出ヒット一覧を返す。

    戻り値は ``kind`` と ``confidence`` のみで、秘密値やその prefix は含めない。
    同一本文の同一 kind は重複を抑え、最大 10 件に制限する。
    """
    hits: list[dict] = []
    if not text:
        return hits
    seen: set[tuple[str, str]] = set()
    for confidence, patterns in (("high", _HIGH_PATTERNS), ("low", _LOW_PATTERNS)):
        for kind, pat in patterns:
            for _match in pat.finditer(text):
                key = (kind, confidence)
                if key in seen:
                    continue
                seen.add(key)
                hits.append({"kind": kind, "confidence": confidence})
                if len(hits) >= 10:
                    return hits
    return hits


def high_confidence_hits(hits: list[dict]) -> list[dict]:
    return [h for h in hits if h.get("confidence") == "high"]


def format_warnings(hits: list[dict]) -> list[str]:
    """本文を含まない、人間向けの警告文を返す。"""
    return [
        f"possible secret detected ({h.get('kind', 'unknown')}, {h.get('confidence', 'unknown')} confidence)"
        for h in hits
    ]


def enforce_secret_policy(
    text: str,
    *,
    policy: str = "block",
    allow_sensitive: bool = False,
) -> list[dict]:
    """秘密情報ポリシーを適用し、警告対象を返す。

    ``block`` は高信頼ヒットだけを拒否する。低信頼ヒットは誤検知を避けて警告に留める。
    ``allow_sensitive`` は明示的な上書きであり、戻り値には依然として値を含めない。
    """
    hits = scan_secrets(text)
    normalized = (policy or "block").strip().lower()
    if normalized == "off":
        return []
    high = high_confidence_hits(hits)
    if normalized == "block" and high and not allow_sensitive:
        kinds = ", ".join(sorted({str(h.get("kind", "unknown")) for h in high}))
        raise SensitiveContentError(
            f"秘密情報らしき本文を保存できません（検出種別: {kinds}）。"
            "安全確認後に --allow-sensitive で明示的に許可できます。"
        )
    return hits
