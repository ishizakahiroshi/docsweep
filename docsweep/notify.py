"""``docsweep notify`` — ローカル OS 通知（UX W4 / P53）。クラウド push なし。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any

from .config import Config
from .engine import scan_records
from .models import Flag


@dataclass
class NotifyResult:
    sent: bool
    title: str
    body: str
    backend: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_overdue_message(config: Config) -> tuple[str, str]:
    records = scan_records(config)
    overdue = [r for r in records if Flag.OVERDUE_TODO.value in (r.flags or [])]
    n = len(overdue)
    title = "docsweep"
    if n == 0:
        body = "やり忘れは 0 件です"
    else:
        sample = overdue[0].title or Path_name(overdue[0].path)
        body = f"やり忘れ {n} 件（例: {sample}）"
    return title, body


def Path_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def send_os_notification(title: str, body: str) -> tuple[bool, str, str]:
    """(ok, backend, detail)."""
    if sys.platform == "win32":
        # PowerShell BalloonTip（依存ゼロ）
        ps = (
            f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            f"ContentType = WindowsRuntime] > $null; "
            f"$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            f"[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            f"$text = $template.GetElementsByTagName('text'); "
            f"$text.Item(0).AppendChild($template.CreateTextNode({_ps_quote(title)})) | Out-Null; "
            f"$text.Item(1).AppendChild($template.CreateTextNode({_ps_quote(body)})) | Out-Null; "
            f"$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
            f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('docsweep')"
            f".Show($toast)"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, timeout=15, check=False,
            )
            if proc.returncode == 0:
                return True, "windows-toast", ""
            return False, "windows-toast", (proc.stderr or b"").decode("utf-8", errors="ignore")[:200]
        except (OSError, subprocess.SubprocessError) as e:
            return False, "windows-toast", str(e)

    if sys.platform == "darwin":
        script = f'display notification {_ps_quote(body)} with title {_ps_quote(title)}'
        # osascript uses AppleScript quotes differently
        script = f'display notification "{_escape_as(body)}" with title "{_escape_as(title)}"'
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=10, check=False,
            )
            return proc.returncode == 0, "osascript", ""
        except (OSError, subprocess.SubprocessError) as e:
            return False, "osascript", str(e)

    # Linux
    if shutil.which("notify-send"):
        try:
            proc = subprocess.run(
                ["notify-send", title, body],
                capture_output=True, timeout=10, check=False,
            )
            return proc.returncode == 0, "notify-send", ""
        except (OSError, subprocess.SubprocessError) as e:
            return False, "notify-send", str(e)
    return False, "none", "no notification backend"


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _escape_as(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def notify_overdue(config: Config, *, dry_run: bool = False) -> NotifyResult:
    title, body = build_overdue_message(config)
    if dry_run:
        return NotifyResult(sent=False, title=title, body=body, backend="dry-run", detail="not sent")
    ok, backend, detail = send_os_notification(title, body)
    return NotifyResult(sent=ok, title=title, body=body, backend=backend, detail=detail)
