"""C6 (wings): resurrect — archive を embedding 類似度で蘇生する。

最近の plan / bugfix と高類似度の archive を浮上候補として列挙し、
ユーザーが Y で frontmatter related に追記、N で「廃止確認済」マーカーを残す。

embedding は ``sentence-transformers`` opt-in。未インストール時は Jaccard 類似度
（tags / title 単語集合）にフォールバックする（精度は落ちるが配布簡素）。
"""

from .service import ResurrectCandidate, ResurrectResult, find_candidates

__all__ = ["ResurrectCandidate", "ResurrectResult", "find_candidates"]
