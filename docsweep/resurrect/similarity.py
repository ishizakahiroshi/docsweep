"""類似度計算。embedding 経路と Jaccard フォールバックの 2 系統。"""

from __future__ import annotations

import re

# 日本語混在の語境界を抑える簡易トークナイザ
_TOKEN_RE = re.compile(r"[\w一-鿿ぁ-ゟァ-ヿ]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    """text を語の集合にする（Jaccard 用）。"""
    if not text:
        return set()
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) >= 2}


def jaccard_similarity(a_text: str, b_text: str) -> float:
    """2 つのテキストの Jaccard 類似度（0..1）。"""
    a = _tokens(a_text)
    b = _tokens(b_text)
    if not a or not b:
        return 0.0
    intersect = len(a & b)
    union = len(a | b)
    return intersect / union if union > 0 else 0.0


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """単純 cosine（numpy 無依存）。"""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(x * y for x, y in zip(vec_a, vec_b, strict=False))
    na = sum(x * x for x in vec_a) ** 0.5
    nb = sum(y * y for y in vec_b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
