"""sentence-transformers ラッパー（opt-in）。

未インストール時は ``EmbeddingUnavailable`` を投げる。CLI 側でこれを捕まえて
「Jaccard モードで動作する」と案内する。
"""

from __future__ import annotations

from typing import Sequence


class EmbeddingUnavailable(RuntimeError):
    """sentence-transformers extras 未インストール。"""


_MODEL = None
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def get_model():
    """シングルトン的にモデルをロードする。"""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise EmbeddingUnavailable(
            "sentence-transformers が未インストールです: pip install 'docsweep[resurrect]'"
        ) from e
    _MODEL = SentenceTransformer(_MODEL_NAME)
    return _MODEL


def encode(texts: Sequence[str]) -> list[list[float]]:
    """テキスト群を embedding ベクトルに変換する（list[list[float]] で返す）。

    本 plan では caching を入れない（初回 reindex で全件回す想定。embedding を
    DB の embedding カラムに保存するのは別 plan で対応）。
    """
    model = get_model()
    arr = model.encode(list(texts), show_progress_bar=False)
    return [list(map(float, vec)) for vec in arr]
