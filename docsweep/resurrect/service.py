"""resurrect の orchestration: archive 走査 + 類似度ペア検出。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import Config
from ..engine import scan_records
from ..models import FileRecord
from .embedding import EmbeddingUnavailable, encode
from .similarity import cosine_similarity, jaccard_similarity

# 「廃止確認済」マーカー: frontmatter に書き込み、再浮上を防ぐ。
RESURRECT_DISMISSED_KEY = "resurrect_dismissed"


@dataclass
class ResurrectCandidate:
    archive_path: str
    archive_title: str | None
    related_path: str  # 現役 plan / bugfix の path
    related_title: str | None
    similarity: float
    mode: str  # "embedding" / "jaccard"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResurrectResult:
    mode: str  # "embedding" / "jaccard"
    threshold: float
    candidates: list[ResurrectCandidate] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "threshold": self.threshold,
            "candidates": [c.to_dict() for c in self.candidates],
        }


def _extract_title(text: str) -> str | None:
    """md の H1 タイトル（ラベル除去後）を抜き出す。"""
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("#"):
            continue
        stripped = re.sub(r"^#+\s*", "", s)
        stripped = re.sub(r"^\[[^\]]+\]\s*", "", stripped)
        return stripped.strip() or None
    return None


def _extract_summary(text: str) -> str:
    """概要セクション先頭の意味行を返す（類似度用テキスト）。"""
    section_re = re.compile(r"^##\s*(?:概要|症状)\s*$.*?(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
    m = section_re.search(text)
    body = m.group(0) if m else text
    return body.strip()[:1000]


def _is_dismissed(text: str) -> bool:
    """frontmatter の resurrect_dismissed: true が立っているか。"""
    fm_re = re.compile(r"^---\s*$(.*?)^---\s*$", re.MULTILINE | re.DOTALL)
    m = fm_re.match(text)
    if not m:
        return False
    return bool(re.search(rf"^{RESURRECT_DISMISSED_KEY}\s*:\s*true\s*$", m.group(1), re.MULTILINE))


def _walk_archive(config: Config) -> list[tuple[Path, str]]:
    """archive 配下の md を列挙する (path, text)。本文を読み込んで返す。"""
    out: list[tuple[Path, str]] = []
    archive_dir_names: set[str] = set()
    for ad in (config.archive_dir, *(t.archive_dir for t in config.types)):
        if ad:
            seg = ad.strip("/").split("/")
            if seg and seg[-1]:
                archive_dir_names.add(seg[-1])

    seen: set[str] = set()
    for root in config.roots:
        root = Path(root).resolve()
        for ad_name in archive_dir_names:
            for md_path in root.rglob(f"{ad_name}/**/*.md"):
                key = md_path.resolve().as_posix()
                if key in seen:
                    continue
                seen.add(key)
                try:
                    text = md_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if _is_dismissed(text):
                    continue
                out.append((md_path, text))
    return out


def _build_active_corpus(records: list[FileRecord]) -> list[tuple[FileRecord, str]]:
    """現役 plan / bugfix の (record, text) を返す。"""
    out: list[tuple[FileRecord, str]] = []
    for r in records:
        if r.type not in ("plan", "bugfix"):
            continue
        if r.state in {"done", "discarded"}:
            continue
        # mtime fresh な「最近の plan」を優先するためここで絞る選択肢もあるが、
        # 本実装ではアクティブ全件を対象に取る。embedding コストは呼び出し側で制御。
        try:
            text = Path(r.path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.append((r, text))
    return out


def find_candidates(
    config: Config,
    *,
    threshold: float = 0.5,
    use_embedding: bool = True,
    top_k_per_archive: int = 1,
) -> ResurrectResult:
    """archive と現役の類似ペアを抽出する。

    Args:
        config: ロード済み Config
        threshold: 類似度の下限。これ以上のペアを候補化
        use_embedding: True で sentence-transformers、False / 未インストール時は Jaccard
        top_k_per_archive: 各 archive について上位 K 件の現役を候補化

    Returns:
        ``ResurrectResult``。embedding が無ければ ``mode="jaccard"`` で動作する。
    """
    archives = _walk_archive(config)
    active = _build_active_corpus(scan_records(config))
    if not archives or not active:
        return ResurrectResult(mode="jaccard", threshold=threshold)

    mode = "jaccard"
    use_emb = False
    archive_vecs: list[list[float]] = []
    active_vecs: list[list[float]] = []
    if use_embedding:
        try:
            archive_vecs = encode([_extract_summary(t) for _, t in archives])
            active_vecs = encode([_extract_summary(t) for _, t in active])
            mode = "embedding"
            use_emb = True
        except EmbeddingUnavailable:
            pass

    candidates: list[ResurrectCandidate] = []
    for i, (apath, atext) in enumerate(archives):
        a_title = _extract_title(atext)
        a_summary = _extract_summary(atext)
        # 全 active との類似度を計算 → top_k
        scored: list[tuple[float, FileRecord, str]] = []
        for j, (rec, rtext) in enumerate(active):
            if use_emb:
                sim = cosine_similarity(archive_vecs[i], active_vecs[j])
            else:
                sim = jaccard_similarity(a_summary, _extract_summary(rtext))
            scored.append((sim, rec, _extract_title(rtext) or ""))
        scored.sort(key=lambda x: -x[0])
        for sim, rec, rtitle in scored[:top_k_per_archive]:
            if sim < threshold:
                continue
            candidates.append(ResurrectCandidate(
                archive_path=apath.as_posix(),
                archive_title=a_title,
                related_path=rec.path,
                related_title=rtitle or rec.title,
                similarity=round(float(sim), 4),
                mode=mode,
            ))

    candidates.sort(key=lambda c: -c.similarity)
    return ResurrectResult(mode=mode, threshold=threshold, candidates=candidates)
