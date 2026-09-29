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
