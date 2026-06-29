"""``docsweep claim <file>`` / ``--unclaim`` — frontmatter の owner を現ユーザーで上書き。

owner 解決順は ``services.frontmatter.current_owner``（config.user.name → git → OS ログイン）に
集約。claim 時には ``claimed_at: <today>`` も併せて付与する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .atomic import update_line
from .detect import _FRONTMATTER_RE
from .services.frontmatter import (
    FrontmatterValidationError,
    current_owner,
    update_frontmatter_field,
)


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


@dataclass
class ClaimResult:
    path: str
    owner: str | None  # unclaim 時は None
    claimed_at: str | None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "owner": self.owner,
            "claimed_at": self.claimed_at,
        }


_CLAIMED_AT_KEY = "claimed_at"


def _set_claimed_at(path: Path, value: str | None) -> None:
    """frontmatter に ``claimed_at: YYYY-MM-DD`` を追加/削除する（services 経由ではなく直書き）。

    ``services.frontmatter.ALLOWED_FIELDS`` に ``claimed_at`` を後付けで足したくないので、
    こちらは ``atomic.update_line`` で薄く行操作する。frontmatter が無いファイルは新設する。
    """
    text = path.read_text(encoding="utf-8", newline="")
    fm = _FRONTMATTER_RE.match(text)
    line = f"{_CLAIMED_AT_KEY}: {value}" if value else f"{_CLAIMED_AT_KEY}: "

    import re

    field_re = re.compile(
        rf"^[ \t]*{re.escape(_CLAIMED_AT_KEY)}[ \t]*:[ \t]*(?P<v>.*)$", re.MULTILINE
    )

    def _xform(t: str) -> str:
        m = _FRONTMATTER_RE.match(t)
        if not m:
            if value is None:
                return t
            return f"---\n{line}\n---\n{t}"
        inner = m.group(1)
        lm = field_re.search(inner)
        if lm is None:
            if value is None:
                return t
            sep = "" if inner.endswith("\n") or not inner else "\n"
            new_inner = f"{inner}{sep}{line}\n"
        else:
            if value is None:
                # 行ごと削除
                start = lm.start()
                end = lm.end()
                # 末尾改行も一緒に外す
                if end < len(inner) and inner[end] == "\n":
                    end += 1
                new_inner = inner[:start] + inner[end:]
            else:
                new_inner = inner[: lm.start()] + line + inner[lm.end():]
        head = "---\n"
        tail = "\n---\n" if new_inner and not new_inner.endswith("\n") else "---\n"
        return head + new_inner + tail + t[m.end():]

    update_line(path, transform=_xform)


def claim(path: Path, *, unclaim: bool = False, cwd: Path | None = None) -> ClaimResult:
    """frontmatter の owner（と claimed_at）を現ユーザー名で更新する。

    ``unclaim=True`` で owner を空にし、``claimed_at`` 行を削除する。
    """
    if not path.is_file():
        raise FileNotFoundError(str(path))
    if unclaim:
        update_frontmatter_field(path, "owner", "")
        _set_claimed_at(path, None)
        return ClaimResult(path=path.resolve().as_posix(), owner=None, claimed_at=None)

    owner = current_owner(cwd=cwd)
    today = _today()
    try:
        update_frontmatter_field(path, "owner", owner)
    except FrontmatterValidationError:
        # owner 値が含み文字（: # 等）で弾かれた場合は何もしない（typo を増やさない）。
        raise
    _set_claimed_at(path, today)
    return ClaimResult(
        path=path.resolve().as_posix(), owner=owner, claimed_at=today
    )
