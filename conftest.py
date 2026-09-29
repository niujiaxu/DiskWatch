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
    """会话结束后清掉测试建的临时目录。

    测试里用 tempfile.mkdtemp 建了几十处临时库/目录，Windows 上不会自动
    回收，长期累积（实测残留 4800+ 个目录、1GB+）。这里按前缀统一清理，
    不影响测试期间的正常使用。
    """
    yield
    import shutil
    import tempfile

    temp = Path(tempfile.gettempdir())
    for pattern in (
        "dw_*",
        "diskwatch_test_*",
        "diskwatch-preview-*",
        "pytest-of-*",  # pytest 的 tmp_path 基目录（空壳也一并删掉）
    ):
        for path in temp.glob(pattern):
            shutil.rmtree(path, ignore_errors=True)
