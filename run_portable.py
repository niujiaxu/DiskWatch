"""PyInstaller 入口：无控制台启动 DiskWatch。"""

from __future__ import annotations

import sys

from diskwatch.app import main

if __name__ == "__main__":
    sys.exit(main() or 0)
