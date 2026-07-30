"""开机自启：写入 HKCU\\...\\Run，用 pythonw 静默启动，不留控制台窗口。"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

from . import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_command() -> str:
    entry = Path(__file__).resolve().parent.parent / "run.pyw"
    exe = Path(sys.executable)
    # 打包成 exe 后直接指向自身；否则用同目录的 pythonw 避免弹控制台
    if exe.name.lower() not in ("python.exe", "pythonw.exe"):
        return f'"{exe}"'
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe
    return f'"{runner}" "{entry}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
