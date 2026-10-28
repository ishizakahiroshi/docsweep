"""CHANGELOG.md から、指定した版の節を GitHub Release の本文として取り出す。

使い方: ``python scripts/release_notes.py v0.8.0 [CHANGELOG.md]``

``## [0.8.0] - 2026-10-28`` の次の行から、次の ``## [`` の手前までを標準出力に出す。
版の節が無いとき・中身が空のときは終了コード 1（Release を本文なしで作らないため）。
publish.yml の ``gh release create --notes-file`` が使う。標準ライブラリだけで動く。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def section(changelog: str, tag: str) -> str | None:
    version = tag[1:] if tag.startswith("v") else tag
    head = re.compile(r"^## \[" + re.escape(version) + r"\](?:\s|$)")
    lines = changelog.splitlines()
    start = next((i for i, line in enumerate(lines) if head.match(line)), None)
    if start is None:
        return None
    body: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## ["):
            break
        body.append(line)
    text = "\n".join(body).strip()
    return text or None


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print("usage: release_notes.py <tag> [CHANGELOG.md]", file=sys.stderr)
        return 2
    path = Path(argv[2]) if len(argv) == 3 else Path("CHANGELOG.md")
    notes = section(path.read_text(encoding="utf-8"), argv[1])
    if notes is None:
        print(f"no CHANGELOG section for {argv[1]}", file=sys.stderr)
        return 1
    sys.stdout.write(notes + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
