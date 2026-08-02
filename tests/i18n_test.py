"""国际化和语言切换的回归测试。

覆盖：
- tr() 中英互译正确，英文缺失键兜底回中文不崩溃
- 所有源码里的 tr() 键都在翻译字典里（防止英文模式静默回退中文）
- 英文模式下构造全部 UI 组件不崩溃、关键文案是英文
- 设置页语言下拉框读写 config 正确

运行： QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe tests\\i18n_test.py
"""

from __future__ import annotations

import ast
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import diskwatch.i18n as i18n
from diskwatch import i18n as i18n_mod  # noqa: F401

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


class _FakeMon:
    roots: list[str] = ["C:\\"]
    def stats(self):
        return (0, 0, 0)


def test_tr_translation() -> None:
    print("tr() 翻译")
    i18n.set_language("en_US")
    check("基础翻译", i18n.tr("设置") == "Settings")
    check("插值翻译", i18n.tr("监控 {roots} 个位置", roots=3) == "Watching 3 locations")
    check(
        "多行插值",
        i18n.tr("今日 {today}\n近{days}天合计 {total}\n今日占比 {pct}%",
                today="1.2 MB", days=7, total="5 MB", pct=42)
        == "Today 1.2 MB\nLast 7d 5 MB\nShare 42%",
    )
    check("单位词英文为空", i18n.tr("个") == "")
    check("缺失键兜底回中文", i18n.tr("不存在的键") == "不存在的键")

    i18n.set_language("zh_CN")
    check("中文原样返回", i18n.tr("设置") == "设置")


def test_all_tr_keys_present() -> None:
    print("tr() 键覆盖")
    src_files = [
        Path("diskwatch/app.py"),
        Path("diskwatch/watcher.py"),
        Path("diskwatch/storage.py"),
        Path("diskwatch/grouping.py"),
        Path("diskwatch/ui/widget.py"),
        Path("diskwatch/ui/ball.py"),
        Path("diskwatch/ui/panel.py"),
        Path("diskwatch/ui/settings.py"),
    ]
    tr_keys: set[str] = set()
    for f in src_files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                tr_keys.add(node.args[0].value)
    missing = [k for k in tr_keys if k not in i18n._TRANSLATIONS]
    check("所有 tr() 键都在翻译字典", not missing, str(missing[:5]))
    check("有至少 100 个翻译键", len(i18n._TRANSLATIONS) >= 100, str(len(i18n._TRANSLATIONS)))


def test_english_ui_construction() -> None:
    print("英文模式 UI 构造")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    i18n.set_language("en_US")

    from diskwatch.config import Config
    from diskwatch.storage import Storage
    from diskwatch.ui.ball import MiniBall
    from diskwatch.ui.panel import DetailPanel
    from diskwatch.ui.settings import SettingsDialog
    from diskwatch.ui.widget import FloatingWidget

    tmp = Path(tempfile.mkdtemp(prefix="dw_i18n_"))
    cfg = Config()
    cfg.set("language", "en_US")
    storage = Storage(tmp / "t.db")
    try:
        ball = MiniBall(storage, _FakeMon(), cfg)
        widget = FloatingWidget(storage, _FakeMon(), cfg)
        panel = DetailPanel(storage)
        dlg = SettingsDialog(cfg, storage, panel)
    finally:
        storage.close()

    check("设置页标题英文", dlg.windowTitle() == "Settings", dlg.windowTitle())
    check("面板标题英文", panel.windowTitle() == "DiskWatch · Details", panel.windowTitle())
    check("卡片标题英文", widget.title.text() == "Files Added Today", widget.title.text())
    check("语言下拉框读对", dlg.cmb_language.currentData() == "en_US")
    check("语言下拉框选项", dlg.cmb_language.count() == 2, str(dlg.cmb_language.count()))


def main() -> int:
    test_tr_translation()
    test_all_tr_keys_present()
    test_english_ui_construction()
    print("\n" + ("全部通过" if not failures else f"失败 {len(failures)} 项: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
