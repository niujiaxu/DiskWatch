"""验证卡片 / 迷你球 / 隐藏 三种状态的切换逻辑。

运行： .venv\\Scripts\\python.exe tests\\ui_state_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from diskwatch.app import DiskWatchApp
from diskwatch.ui.style import apply_dark_theme

failures = []


def check(label, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


def state(app):
    return f"card={app.widget.isVisible()} ball={app.ball.isVisible()}"


def main():
    # 这个测试断言的是"从卡片态开始"，所以必须自己把起点定死。
    # 否则会读到用户当前的配置（比如上次停在迷你球模式）而随机失败。
    from diskwatch.config import Config

    seed = Config()
    original = {k: seed.get(k) for k in ("collapsed", "widget_visible", "start_minimized")}
    seed.update({"collapsed": False, "widget_visible": True, "start_minimized": False})
    seed.save()

    qt = QApplication(sys.argv[:1])
    apply_dark_theme(qt)
    app = DiskWatchApp(qt)
    qt.processEvents()

    print("1) 初始状态应为卡片")
    check("卡片可见", app.widget.isVisible(), state(app))
    check("球不可见", not app.ball.isVisible())

    print("\n2) 收起为迷你球")
    app.collapse()
    qt.processEvents()
    check("球可见", app.ball.isVisible(), state(app))
    check("卡片已隐藏", not app.widget.isVisible())
    check("配置已记录 collapsed", app.config.get("collapsed") is True)
    check("托盘勾选项同步", app.act_ball.isChecked() and app.act_widget.isChecked())

    print("\n3) 重复收起应无副作用")
    app.collapse()
    qt.processEvents()
    check("仍是球", app.ball.isVisible() and not app.widget.isVisible(), state(app))

    print("\n4) 单击球展开")
    app.ball.expand_requested.emit()
    qt.processEvents()
    check("卡片回来", app.widget.isVisible(), state(app))
    check("球已隐藏", not app.ball.isVisible())
    check("配置已清除 collapsed", app.config.get("collapsed") is False)

    print("\n5) 球状态下整体隐藏，再从托盘唤回")
    app.collapse()
    qt.processEvents()
    app._toggle_widget(False)
    qt.processEvents()
    check("两者都隐藏", not app.widget.isVisible() and not app.ball.isVisible(), state(app))
    check("托盘显示项未勾选", not app.act_widget.isChecked())
    app._toggle_widget(True)
    qt.processEvents()
    check("唤回的是球（记住了模式）", app.ball.isVisible() and not app.widget.isVisible(), state(app))

    print("\n6) 从球模式经托盘切回卡片")
    app.act_ball.trigger()  # checkable，触发后变为未选中 -> expand
    qt.processEvents()
    check("切回卡片", app.widget.isVisible() and not app.ball.isVisible(), state(app))

    print("\n7) 迷你球体积压缩")
    from diskwatch.ui.ball import _compact_size

    cases = [
        (0, "0B"),
        (512, "512B"),
        (1536, "1.5K"),
        (10240, "10K"),
        (2_800_000, "2.7M"),
        (1_500_000_000, "1.4G"),
    ]
    for n, want in cases:
        got = _compact_size(n)
        check(f"_compact_size({n}) == {want}", got == want, got)

    app.config.update(original)
    app.config.save()
    app.monitor.stop()
    app.storage.close()
    app.tray.hide()

    print("\n" + ("全部通过" if not failures else f"失败 {len(failures)} 项: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
