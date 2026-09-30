"""验证卡片 / 迷你球 / 隐藏 三种状态的切换逻辑。"""

import pytest

from diskwatch.ui.style import apply_dark_theme


@pytest.fixture
def dw_app(qapp, monkeypatch, tmp_path):
    """构造完整应用，先钉死起点状态，结束后恢复用户配置。"""
    from diskwatch.app import DiskWatchApp
    from diskwatch.config import Config

    apply_dark_theme(qapp)
    import diskwatch.config as cfgmod
    import diskwatch.errorlog as errlog

    monkeypatch.setattr(cfgmod.paths, "config", tmp_path / "cfg.json")
    monkeypatch.setattr(cfgmod.paths, "db", tmp_path / "t.db")
    # 日志也写临时目录：否则测试进程会往用户真实日志里写（曾把诊断日志搅乱）
    monkeypatch.setattr(errlog, "default_home", lambda: tmp_path)

    seed = Config()
    original = {
        k: seed.get(k) for k in ("collapsed", "widget_visible", "start_minimized", "scan_on_startup")
    }
    seed.update(
        {
            "collapsed": False,
            "widget_visible": True,
            "start_minimized": False,
            "scan_on_startup": False,
            # 测试不做启动恢复：避免读真实磁盘的 USN/用户目录（慢且侵入）
            "startup_recovery": "disabled",
        }
    )
    seed.save()

    app = DiskWatchApp(qapp)
    qapp.processEvents()
    yield app

    try:
        app.monitor.stop()
        app.storage.close()
        app.tray.hide()
        app.config.update(original)
        app.config.save()
    except Exception:
        pass


def state(app) -> str:
    return f"card={app.widget.isVisible()} ball={app.ball.isVisible()}"


def test_ui_watchdog_reports_main_thread_hang(dw_app, qapp, monkeypatch) -> None:
    """主线程卡住时看门狗要把调用栈写进日志（供事后定位“点不动”）。

    测试里主线程就是本线程：直接 sleep 不跑事件循环，心跳随之停跳。
    """
    import time as _time

    import diskwatch.app as appmod
    from diskwatch.errorlog import errorlog

    monkeypatch.setattr(appmod, "UI_HANG_THRESHOLD", 0.4)
    monkeypatch.setattr(appmod, "UI_HANG_REPORT_INTERVAL", 0.0)
    monkeypatch.setattr(appmod, "UI_WATCHDOG_POLL", 0.05)

    _time.sleep(1.5)  # 主线程故意卡住（不处理事件）

    deadline = _time.monotonic() + 4
    hit = ""
    while _time.monotonic() < deadline:
        _time.sleep(0.1)
        for _level, message in errorlog.recent(50):
            if "主线程已卡住" in message:
                hit = message
                break
        if hit:
            break
    assert hit, "看门狗应检测到主线程停跳并写入日志"
    assert "调用栈" in hit and "ui_state_test.py" in hit, hit


def test_scan_finish_resets_strip_even_when_window_hidden(dw_app, qapp) -> None:
    """回归：主窗口隐藏时补扫结束也必须收尾。

    症状：补扫期间把主窗口隐藏（只看悬浮卡片），补扫结束后再打开主窗口，
    底部一直显示“正在补扫：N 个目录 · M 个文件”不动 —— 因为收尾曾经只在
    `scan_strip.isVisible()` 为真时执行，而窗口隐藏时它永远是 False。
    """
    app = dw_app
    app.main_window.scan_label.setText("正在补扫：10143 个目录 · 3014 个文件")
    app.main_window._scan_cancelling = True
    assert not app.main_window.scan_strip.isVisible()  # 主窗口未显示

    app._after_scan_refresh(444, False)

    label = app.main_window.scan_label.text()
    assert "正在补扫" not in label, label


def test_initial_card_state(dw_app, qapp) -> None:
    assert dw_app.widget.isVisible(), state(dw_app)
    assert not dw_app.ball.isVisible()


def test_settings_is_embedded_third_main_page(dw_app, qapp) -> None:
    dw_app.show_settings()
    qapp.processEvents()
    assert dw_app.main_window.isVisible()
    assert dw_app.main_window.pages.currentWidget() is dw_app.settings
    assert not dw_app.settings.isWindow()
    dw_app.settings.cancel_requested.emit()
    assert dw_app.main_window.pages.currentWidget() is dw_app.dashboard


def test_main_window_opens_centered(dw_app, qapp) -> None:
    """详情/设置窗口从隐藏状态打开时定位到屏幕中心，而不是左上角。"""
    win = dw_app.main_window
    screen = win.screen() or qapp.primaryScreen()
    area = screen.availableGeometry()

    dw_app.show_panel()
    qapp.processEvents()
    center = win.frameGeometry().center()
    assert abs(center.x() - area.center().x()) <= 2, (center, area)
    assert abs(center.y() - area.center().y()) <= 2, (center, area)

    # 挪到左上角再隐藏，重新打开时仍会回到屏幕中心
    win.move(area.left(), area.top())
    win.hide()
    qapp.processEvents()
    dw_app.show_settings()
    qapp.processEvents()
    center = win.frameGeometry().center()
    assert abs(center.x() - area.center().x()) <= 2, (center, area)
    assert abs(center.y() - area.center().y()) <= 2, (center, area)


def test_collapse_to_ball(dw_app, qapp) -> None:
    dw_app.collapse()
    qapp.processEvents()
    assert dw_app.ball.isVisible(), state(dw_app)
    assert not dw_app.widget.isVisible()
    assert dw_app.config.get("collapsed") is True
    assert dw_app.act_ball.isChecked() and dw_app.act_widget.isChecked()


def test_collapse_idempotent(dw_app, qapp) -> None:
    dw_app.collapse()
    qapp.processEvents()
    dw_app.collapse()
    qapp.processEvents()
    assert dw_app.ball.isVisible() and not dw_app.widget.isVisible(), state(dw_app)


def test_expand_from_ball(dw_app, qapp) -> None:
    dw_app.collapse()
    qapp.processEvents()
    dw_app.ball.expand_requested.emit()
    qapp.processEvents()
    assert dw_app.widget.isVisible(), state(dw_app)
    assert not dw_app.ball.isVisible()
    assert dw_app.config.get("collapsed") is False


def test_expand_from_ball_places_card_at_ball(dw_app, qapp) -> None:
    """迷你球展开时卡片跟随球的位置（右缘对齐），不再停在旧位置。"""
    app = dw_app
    app.collapse()
    qapp.processEvents()
    ball = app.ball
    screen = ball.screen() or qapp.primaryScreen()
    area = screen.availableGeometry()
    ball.move(area.left() + 200, area.top() + 60)
    qapp.processEvents()

    ball.expand_requested.emit()
    qapp.processEvents()

    widget = app.widget
    assert widget.isVisible() and not ball.isVisible()
    assert abs(widget.y() - (area.top() + 60)) <= 2, (widget.y(), area)
    assert (
        abs(widget.frameGeometry().right() - ball.frameGeometry().right()) <= 2
    ), (widget.frameGeometry(), ball.frameGeometry())


def test_hide_and_restore_via_tray(dw_app, qapp) -> None:
    dw_app.collapse()
    qapp.processEvents()
    dw_app._toggle_widget(False)
    qapp.processEvents()
    assert not dw_app.widget.isVisible() and not dw_app.ball.isVisible(), state(dw_app)
    assert not dw_app.act_widget.isChecked()
    dw_app._toggle_widget(True)
    qapp.processEvents()
    assert dw_app.ball.isVisible() and not dw_app.widget.isVisible(), state(dw_app)


def test_tray_switch_back_to_card(dw_app, qapp) -> None:
    dw_app.collapse()
    qapp.processEvents()
    dw_app.act_ball.trigger()
    qapp.processEvents()
    assert dw_app.widget.isVisible() and not dw_app.ball.isVisible(), state(dw_app)


def test_tray_menu_styled_with_icons(dw_app, qapp) -> None:
    """托盘菜单：圆角卡片样式；开关项保留勾选框（不配图标），其余项带图标。"""
    menu = dw_app.tray.contextMenu()
    assert menu is not None
    assert menu.styleSheet(), "菜单应套用圆角样式"
    toggles = (dw_app.act_widget, dw_app.act_ball)
    for action in toggles:
        assert action.isCheckable()
        # Qt 对"有图标的勾选项"不画勾选框，必须保持无图标才能看见选中态
        assert action.icon().isNull()
    icon_actions = [
        a for a in menu.actions() if not a.isSeparator() and a not in toggles
    ]
    assert icon_actions, "菜单应有普通动作项"
    assert all(not a.icon().isNull() for a in icon_actions)


def test_menu_icons_render(qapp) -> None:
    from diskwatch.ui.style import menu_icon

    kinds = (
        "window", "ball", "list", "chart", "alert", "sliders",
        "refresh", "restart", "info", "power", "hide",
    )
    for kind in kinds:
        icon = menu_icon(kind)
        assert not icon.isNull(), kind
        assert not icon.pixmap(16, 16).isNull(), kind


def test_main_window_can_shrink_freely(dw_app, qapp) -> None:
    """主窗口最小尺寸要够小：无边框窗口靠拖边缘缩放，别被页面顶死。"""
    w = dw_app.main_window
    assert w.minimumSizeHint().width() <= 820, w.minimumSizeHint().toTuple()
    assert w.minimumSizeHint().height() <= 620, w.minimumSizeHint().toTuple()
    # 嵌入的设置页用滚动区兜底，不再把自身大尺寸传导给主窗口
    assert dw_app.settings.minimumSizeHint().width() < 400


def test_compact_size() -> None:
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
        assert _compact_size(n) == want, n
