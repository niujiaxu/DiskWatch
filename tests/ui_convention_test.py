"""UI 约定强制检查：源码禁止引入原生 QComboBox。

顶层窗（悬浮卡片 / 详情面板 / 设置对话框）下 QComboBox 的弹层会错位到
屏幕左上角（Qt popup 自动定位 bug），项目一律使用自绘 DayPicker。
新增下拉选择功能必须用 diskwatch/ui/panel.py 的 DayPicker。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_FILES = sorted((ROOT / "diskwatch").rglob("*.py"))


def _scan_qcombo_imports(tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "PySide6.QtWidgets":
            for alias in node.names:
                if alias.name == "QComboBox":
                    hits.append(f"import {alias.name}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "QComboBox" or (
                    alias.name == "PySide6" and alias.asname == "QtWidgets"
                ):
                    hits.append(f"import {alias.name}")
    return hits


def _scan_qcombo_usage(tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "QComboBox":
            hits.append(node.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "QComboBox" and node.func.value.id == "PySide6":
                hits.append("PySide6.QtWidgets.QComboBox")
    return hits


def test_no_qcombobox_import() -> None:
    offenders: list[str] = []
    for f in SRC_FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        hits = _scan_qcombo_imports(tree)
        if hits:
            offenders.append(f"{f.relative_to(ROOT)}: {hits}")
    assert not offenders, (
        "QComboBox 在置顶窗下弹层会错位到左上角，请改用 DayPicker（diskwatch/ui/panel.py）。\n"
        + "\n".join(offenders)
    )


def test_no_qcombo_usage() -> None:
    offenders: list[str] = []
    for f in SRC_FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        hits = _scan_qcombo_usage(tree)
        if hits:
            offenders.append(f"{f.relative_to(ROOT)}: {hits}")
    assert not offenders, (
        "检测到 QComboBox 使用，请改用 DayPicker（addItem/currentData/"
        "setCurrentIndex/setItemText/findData/currentIndexChanged 对齐 QComboBox API）。\n"
        + "\n".join(offenders)
    )
