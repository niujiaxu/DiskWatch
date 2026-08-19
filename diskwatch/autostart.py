"""开机自启：写入 HKCU\\...\\Run，用 pythonw 静默启动，不留控制台窗口。"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

from . import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_args() -> tuple[list[str], str]:
    """重启自身用的 (命令行参数, 工作目录)。

    打包成 exe 后直接指向自身；源码运行时用同目录的 pythonw 拉起
    run.pyw，避免弹控制台。开机自启与「重启」菜单共用同一探测逻辑。
    """
    entry = Path(__file__).resolve().parent.parent / "run.pyw"
    exe = Path(sys.executable)
    if exe.name.lower() not in ("python.exe", "pythonw.exe"):
        return [str(exe)], str(exe.parent)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe
    return [str(runner), str(entry)], str(entry.parent)


def _launch_command() -> str:
    args, _cwd = launch_args()
    return " ".join(f'"{a}"' for a in args)


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
