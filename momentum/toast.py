"""Windows toast notifications for an unpackaged app (no tray icon, no background service).

The app registers its AppUserModelID under HKCU\\Software\\Classes\\AppUserModelId (per-user,
no admin), then shows toasts through the built-in WinRT API via a hidden PowerShell call.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from xml.sax.saxutils import escape

AUMID = "Victor.MomentumScanner"


def register(icon_path: str | None = None) -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    except Exception:
        pass
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{AUMID}") as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "Momentum Scanner")
            if icon_path and os.path.exists(icon_path):
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, icon_path)
    except Exception:
        pass


def show(title: str, lines: list[str]) -> None:
    if sys.platform != "win32":
        return
    body = "".join(f"<text>{escape(l)}</text>" for l in lines[:2])
    xml = f"<toast><visual><binding template='ToastGeneric'><text>{escape(title)}</text>{body}</binding></visual></toast>"
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml($env:MS_TOAST_XML);"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{AUMID}')"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    env = dict(os.environ, MS_TOAST_XML=xml)
    try:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps],
                         env=env, creationflags=0x08000000)  # CREATE_NO_WINDOW
    except Exception:
        pass
