"""pytest 公共配置：项目根入 sys.path、offscreen QApplication fixture。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """全会话共用一个 QApplication（Qt 限制单实例）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="session", autouse=True)
def _cleanup_temp_artifacts():
    """会话结束后清掉测试建的 dw_* 临时目录。

    测试里用 tempfile.mkdtemp 建了 40 多处临时库/目录，Windows 上不会自动
    回收，长期累积（实测残留 4800+ 个目录、1GB+）。这里在收尾时统一删除，
    不影响测试期间的正常使用。
    """
    yield
    import shutil
    import tempfile

    for path in Path(tempfile.gettempdir()).glob("dw_*"):
        shutil.rmtree(path, ignore_errors=True)
    for path in Path(tempfile.gettempdir()).glob("diskwatch-preview-*"):
        shutil.rmtree(path, ignore_errors=True)
