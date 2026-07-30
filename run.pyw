"""无控制台启动入口（双击即可运行）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diskwatch.app import main

if __name__ == "__main__":
    sys.exit(main())
