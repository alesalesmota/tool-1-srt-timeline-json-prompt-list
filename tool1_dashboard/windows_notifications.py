from __future__ import annotations

import base64
import os
import subprocess
import threading
from typing import Literal


NotificationLevel = Literal["info", "warning", "error"]

_TITLE_LIMIT = 63
_MESSAGE_LIMIT = 220
_DISPLAY_MS = 5000


class WindowsNotificationManager:
    """Best-effort Windows desktop notifications without extra Python deps."""

    def __init__(self, *, enabled: bool | None = None) -> None:
        if enabled is None:
            raw = str(os.environ.get("TOOL1_WINDOWS_NOTIFICATIONS", "1")).strip().lower()
            enabled = raw not in {"0", "false", "no", "off"}
        self.enabled = bool(enabled)

    def notify(self, *, title: str, message: str, level: NotificationLevel = "info") -> None:
        if not self.enabled or os.name != "nt":
            return
        safe_title = self._clean_text(title, limit=_TITLE_LIMIT)
        safe_message = self._clean_text(message, limit=_MESSAGE_LIMIT)
        if not safe_title or not safe_message:
            return
        thread = threading.Thread(
            target=self._show_balloon_tip,
            args=(safe_title, safe_message, level),
            daemon=True,
        )
        thread.start()

    @staticmethod
    def _clean_text(value: str, *, limit: int) -> str:
        text = " ".join(str(value or "").split()).strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip(" ,.;:") + "…"

    @staticmethod
    def _show_balloon_tip(title: str, message: str, level: NotificationLevel) -> None:
        icon_name = {
            "info": "Information",
            "warning": "Warning",
            "error": "Error",
        }.get(level, "Information")
        tooltip_icon = {
            "info": "Info",
            "warning": "Warning",
            "error": "Error",
        }.get(level, "Info")
        script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::{icon_name}
$notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::{tooltip_icon}
$notify.BalloonTipTitle = '{WindowsNotificationManager._escape_powershell(title)}'
$notify.BalloonTipText = '{WindowsNotificationManager._escape_powershell(message)}'
$notify.Visible = $true
$notify.ShowBalloonTip({_DISPLAY_MS})
$end = [DateTime]::UtcNow.AddMilliseconds({_DISPLAY_MS + 1500})
while ([DateTime]::UtcNow -lt $end) {{
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}}
$notify.Dispose()
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    encoded,
                ],
                check=False,
                timeout=15,
                creationflags=creationflags,
            )
        except Exception:
            return

    @staticmethod
    def _escape_powershell(value: str) -> str:
        return str(value or "").replace("'", "''")
