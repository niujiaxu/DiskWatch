"""PyInstaller 入口：无控制台启动 DiskWatch。"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    # 构建后无界面冒烟检查：模块导入成功即退出，不读取配置、不启动监控。
    # 使用环境变量避免 GUI 子系统对命令行转发的差异；可选日志便于定位
    # windowed PyInstaller 包在启动阶段的异常。
    if os.environ.get("DISKWATCH_SELF_TEST") == "1":
        log_path = os.environ.get("DISKWATCH_SELF_TEST_LOG")
        try:
            from diskwatch.app import main as _checked_main  # noqa: F401
        except BaseException as exc:
            if log_path:
                with open(log_path, "w", encoding="utf-8") as handle:
                    handle.write(f"{type(exc).__name__}: {exc}\n")
            raise SystemExit(1) from None
        if log_path:
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write("OK\n")
        raise SystemExit(0)

    from diskwatch.app import main

    sys.exit(main() or 0)
