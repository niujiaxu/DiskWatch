"""国际化和语言切换的回归测试。"""

from __future__ import annotations

import ast
import tempfile
from pathlib import Path

from diskwatch import i18n

SRC_FILES = [
    Path("diskwatch/app.py"),
    Path("diskwatch/watcher.py"),
    Path("diskwatch/storage.py"),
    Path("diskwatch/grouping.py"),
    Path("diskwatch/ui/widget.py"),
    Path("diskwatch/ui/ball.py"),
    Path("diskwatch/ui/panel.py"),
    Path("diskwatch/ui/settings.py"),
]


class _FakeMon:
    roots: list[str] = ["C:\\"]  # noqa: RUF012

    def stats(self):
        return (0, 0, 0)


def test_tr_translation() -> None:
    i18n.set_language("en_US")
    assert i18n.tr("设置") == "Settings"
    assert i18n.tr("监控 {roots} 个位置", roots=3) == "Watching 3 locations"
    assert (
        i18n.tr(
            "今日 {today}\n近{days}天合计 {total}\n今日占比 {pct}%",
            today="1.2 MB",
            days=7,
            total="5 MB",
            pct=42,
        )
        == "Today 1.2 MB\nLast 7d 5 MB\nShare 42%"
    )
    assert i18n.tr("个") == ""
    assert i18n.tr("不存在的键") == "不存在的键"

    i18n.set_language("zh_CN")
    assert i18n.tr("设置") == "设置"


def test_all_tr_keys_present() -> None:
    tr_keys: set[str] = set()
    for f in SRC_FILES:
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
    assert not missing, missing[:5]
    assert len(i18n._TRANSLATIONS) >= 100, len(i18n._TRANSLATIONS)


def test_english_ui_construction(qapp) -> None:
    from diskwatch.config import Config
    from diskwatch.storage import Storage
    from diskwatch.ui.ball import MiniBall
    from diskwatch.ui.panel import DetailPanel
    from diskwatch.ui.settings import SettingsDialog
    from diskwatch.ui.widget import FloatingWidget

    i18n.set_language("en_US")
    tmp = Path(tempfile.mkdtemp(prefix="dw_i18n_"))
    cfg = Config()
    cfg.set("language", "en_US")
    storage = Storage(tmp / "t.db")
    try:
        MiniBall(storage, _FakeMon(), cfg)  # 构造不崩溃即可，不引用后续
        widget = FloatingWidget(storage, _FakeMon(), cfg)
        panel = DetailPanel(storage)
        dlg = SettingsDialog(cfg, storage, panel)
    finally:
        storage.close()

    assert dlg.windowTitle() == "Settings", dlg.windowTitle()
    assert panel.windowTitle() == "DiskWatch · Details", panel.windowTitle()
    assert widget.title.text() == "Files Added Today", widget.title.text()
    assert dlg.cmb_language.currentData() == "en_US"
    assert dlg.cmb_language.count() == 2, dlg.cmb_language.count()
