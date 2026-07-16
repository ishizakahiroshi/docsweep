"""md 書き込み時の簡易 secrets 警告（UX W4 / P60）。

API key っぽい文字列を検出して警告する。拒否はしない（習慣ガード）。
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("github_fine_grained", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("openai_sk", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("anthropic_sk", re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}")),
    ("generic_bearer", re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{24,}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]


def scan_secrets(text: str) -> list[dict]:
    """検出ヒット一覧 ``[{kind, sample}]``。sample は先頭 8 文字のみ。"""
    hits: list[dict] = []
    if not text:
        return hits
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(0)
            sample = raw[:8] + ("…" if len(raw) > 8 else "")
            hits.append({"kind": kind, "sample": sample})
            if len(hits) >= 10:
                return hits
    return hits


def format_warnings(hits: list[dict]) -> list[str]:
    return [f"possible secret ({h['kind']}): {h['sample']}" for h in hits]
